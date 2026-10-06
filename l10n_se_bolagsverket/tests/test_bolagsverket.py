import json
import pathlib
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..lib import bolagsverket as bv

DATA = pathlib.Path(__file__).resolve().parent / "data"


def fixture(name):
    return json.loads((DATA / name).read_text())["organisationer"]


class FakeClient:
    """Stands in for bv.Client: answers per identity number from the JSON fixtures."""

    def __init__(self, answers, error=None):
        self.answers = answers
        self.error = error
        self.asked = []
        self.pings = 0

    def organisations(self, identity):
        self.asked.append(identity)
        if self.error:
            raise self.error
        answer = self.answers.get(identity, fixture("not_found.json"))
        if isinstance(answer, Exception):
            raise answer
        return bv.parse_answer(answer)

    def is_alive(self):
        self.pings += 1
        return True


@tagged("post_install", "-at_install")
class TestBolagsverket(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("l10n_se_bolagsverket.enabled", "True")
        ICP.set_param("l10n_se_bolagsverket.client_id", "id")
        ICP.set_param("l10n_se_bolagsverket.client_secret", "secret")
        cls.se = cls.env.ref("base.se")
        cls.Partner = cls.env["res.partner"]
        cls.user = cls.env["res.users"].create({"name": "Bokförare", "login": "bv-bokforare@example.com",
                                                "group_ids": [(4, cls.env.ref("base.group_user").id)]})
        cls.env.company.l10n_se_bv_responsible_id = cls.user
        ICP.set_param("l10n_se_bolagsverket.last_ping", False)
        # existing contacts in the database must not take part in the cron tests
        cls.Partner.search([("l10n_se_bv_identity", "!=", False)]).sudo().write(
            {"l10n_se_bv_next_check": fields.Datetime.now() + timedelta(days=30)})

    def run_with(self, client, func):
        cls = type(self.Partner)
        with patch.object(cls, "_l10n_se_bv_client", lambda self_: client), \
             patch.object(cls, "_l10n_se_bv_commit", lambda self_: None):
            return func()

    def supplier(self, **vals):
        return self.Partner.create(dict({"name": "Exempelbolaget i Teststad AB", "is_company": True,
                                         "country_id": self.se.id, "company_registry": "556000-0001",
                                         "supplier_rank": 1}, **vals))

    # -- identity -----------------------------------------------------------------------------------

    def test_identity_from_registry_or_vat(self):
        self.assertEqual(self.supplier().l10n_se_bv_identity, "5560000001")
        p = self.Partner.create({"name": "X", "vat": "SE559588041901", "country_id": self.se.id})
        self.assertEqual(p.l10n_se_bv_identity, "5595880419")
        foreign = self.Partner.create({"name": "Y", "company_registry": "556000-0001",
                                       "country_id": self.env.ref("base.de").id})
        self.assertFalse(foreign.l10n_se_bv_identity, "a German registry number is not a Swedish org.nr")

    def test_changed_number_is_checked_again(self):
        p = self.supplier()
        p.sudo().write({"l10n_se_bv_checked": fields.Datetime.now(), "l10n_se_bv_status": "active"})
        p.company_registry = "559588-0419"
        self.assertFalse(p.l10n_se_bv_checked)
        self.assertFalse(p.l10n_se_bv_status)

    # -- fetch button ---------------------------------------------------------------------------------

    def test_fetch_fills_name_and_address(self):
        p = self.Partner.create({"name": "exempelbolaget", "vat": "SE556000000101", "country_id": self.se.id})
        client = FakeClient({"5560000001": fixture("active.json")})
        self.run_with(client, p.action_l10n_se_bv_fetch)
        self.assertEqual(p.name, "Exempelbolaget i Teststad AB")
        self.assertEqual((p.street, p.street2, p.zip, p.city), ("Provgatan 1", "c/o Test Testsson", "12345",
                                                                 "Upplands Väsby"))
        self.assertEqual(p.country_id, self.se)
        self.assertTrue(p.is_company)
        self.assertIn(p.company_registry, ("5560000001", "556000-0001"))
        self.assertEqual(p.l10n_se_bv_status, "active")
        self.assertFalse(p.l10n_se_bv_name_mismatch)
        self.assertEqual(p.l10n_se_bv_sni, "62100 Dataprogrammering")
        self.assertIn("Bolagsverket", p.message_ids[0].body)

    def test_fetch_unknown_number(self):
        p = self.supplier(company_registry="559588-0419")
        action = self.run_with(FakeClient({}), p.action_l10n_se_bv_fetch)
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(p.l10n_se_bv_status, "not_found", "the result is saved, not rolled back")
        self.assertEqual(p.name, "Exempelbolaget i Teststad AB", "nothing to fill in")

    def test_fetch_without_number(self):
        p = self.Partner.create({"name": "Utan nummer"})
        with self.assertRaises(UserError):
            self.run_with(FakeClient({}), p.action_l10n_se_bv_fetch)

    # -- status, notification, bill warning -----------------------------------------------------------

    def test_bankruptcy_notifies_once_and_warns_on_bill(self):
        p = self.supplier()
        client = FakeClient({"5560000001": fixture("bankrupt.json")})
        self.run_with(client, p.action_l10n_se_bv_check)
        self.assertEqual(p.l10n_se_bv_status, "procedure")
        self.assertEqual(p.l10n_se_bv_status_note, "Konkurs från 2026-09-30")
        self.assertTrue(p.l10n_se_bv_next_check > fields.Datetime.now() + timedelta(days=6))
        acts = self.env["mail.activity"].search([("res_model", "=", "res.partner"), ("res_id", "=", p.id)])
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts.user_id, self.user)
        self.run_with(client, p.action_l10n_se_bv_check)
        acts = self.env["mail.activity"].search([("res_model", "=", "res.partner"), ("res_id", "=", p.id)])
        self.assertEqual(len(acts), 1, "same status again must not add a second to-do")
        bill = self.env["account.move"].create({"move_type": "in_invoice", "partner_id": p.id})
        self.assertIn("Konkurs", bill.l10n_se_bv_warning)
        self.assertIn("paying", bill.l10n_se_bv_warning)
        invoice = self.env["account.move"].create({"move_type": "out_invoice", "partner_id": p.id})
        self.assertIn("receivable", invoice.l10n_se_bv_warning)

    def test_deregistered(self):
        p = self.supplier()
        self.run_with(FakeClient({"5560000001": fixture("deregistered.json")}), p.action_l10n_se_bv_check)
        self.assertEqual(p.l10n_se_bv_status, "deregistered")
        self.assertTrue(p.l10n_se_bv_bad)

    def test_name_mismatch(self):
        p = self.supplier(name="Nemoris AB")
        self.run_with(FakeClient({"5560000001": fixture("active.json")}), p.action_l10n_se_bv_check)
        self.assertTrue(p.l10n_se_bv_name_mismatch)
        self.assertEqual(p.l10n_se_bv_name, "Exempelbolaget i Teststad AB")
        self.assertEqual(p.name, "Nemoris AB", "a status check never changes the contact's own data")
        bill = self.env["account.move"].create({"move_type": "in_invoice", "partner_id": p.id})
        self.assertIn("Exempelbolaget i Teststad AB", bill.l10n_se_bv_warning)
        invoice = self.env["account.move"].create({"move_type": "out_invoice", "partner_id": p.id})
        self.assertFalse(invoice.l10n_se_bv_warning, "the fraud warning is about paying suppliers")
        self.assertEqual(len(self.env["mail.activity"].search([("res_model", "=", "res.partner"),
                                                                ("res_id", "=", p.id)])), 1)

    def test_healthy_partner_gives_no_warning(self):
        p = self.supplier()
        self.run_with(FakeClient({"5560000001": fixture("active.json")}), p.action_l10n_se_bv_check)
        bill = self.env["account.move"].create({"move_type": "in_invoice", "partner_id": p.id})
        self.assertFalse(bill.l10n_se_bv_warning)

    # -- scheduled check ------------------------------------------------------------------------------

    def test_cron_checks_new_then_due_and_skips_fresh(self):
        new = self.supplier()
        fresh = self.supplier(name="Färsk AB", company_registry="559588-0419")
        fresh.sudo().write({"l10n_se_bv_next_check": fields.Datetime.now() + timedelta(days=3),
                            "l10n_se_bv_status": "active"})
        stale = self.supplier(name="Gammal AB", vat="SE559588041901", company_registry=False)
        stale.sudo().write({"l10n_se_bv_next_check": fields.Datetime.now() - timedelta(days=1)})
        not_watched = self.Partner.create({"name": "Inte kund", "company_registry": "556000-0001",
                                           "country_id": self.se.id, "is_company": True})
        person = self.Partner.create({"name": "Privatperson", "company_registry": "195501011232",
                                      "customer_rank": 1, "country_id": self.se.id})
        client = FakeClient({"5560000001": fixture("active.json"), "5595880419": fixture("active.json")})
        self.run_with(client, self.Partner._cron_l10n_se_bv_check)
        self.assertIn("5560000001", client.asked)
        self.assertEqual(client.asked.count("5595880419"), 1, "only the stale one, not the fresh one")
        self.assertTrue(new.l10n_se_bv_checked and stale.l10n_se_bv_checked)
        self.assertFalse(not_watched.l10n_se_bv_checked, "neither customer nor supplier")
        self.assertFalse(person.l10n_se_bv_checked, "private persons are never sent to Bolagsverket")

    def test_cron_stops_on_temporary_error_and_keeps_status(self):
        p = self.supplier()
        p.sudo().write({"l10n_se_bv_status": "procedure", "l10n_se_bv_next_check": fields.Datetime.now()})
        for error in (bv.BolagsverketError("busy", 429, retry=True),
                      bv.BolagsverketError("token request refused (400)", 400, retry=True)):
            client = FakeClient({}, error=error)
            self.run_with(client, self.Partner._cron_l10n_se_bv_check)
            self.assertEqual(len(client.asked), 1)
            self.assertEqual(p.l10n_se_bv_status, "procedure", "an error must never clear a bankruptcy")

    def test_incomplete_answer_keeps_bankruptcy(self):
        p = self.supplier()
        self.run_with(FakeClient({"5560000001": fixture("bankrupt.json")}), p.action_l10n_se_bv_check)
        client = FakeClient({"5560000001": fixture("source_error_spec.json")})
        p.sudo().l10n_se_bv_next_check = fields.Datetime.now()
        self.run_with(client, self.Partner._cron_l10n_se_bv_check)
        self.assertEqual(p.l10n_se_bv_status, "procedure")
        with self.assertRaises(UserError):
            self.run_with(client, p.action_l10n_se_bv_fetch)
        self.assertEqual(p.l10n_se_bv_status, "procedure")

    def test_cron_bad_request_keeps_status_and_retries_tomorrow(self):
        a = self.supplier()
        a.sudo().l10n_se_bv_status = "deregistered"
        a.sudo().l10n_se_bv_next_check = fields.Datetime.now()
        b = self.supplier(name="B AB", company_registry="559588-0419")
        client = FakeClient({}, error=bv.BolagsverketError("bad", 400))
        self.run_with(client, self.Partner._cron_l10n_se_bv_check)
        self.assertEqual(len(client.asked), 2)
        self.assertEqual(a.l10n_se_bv_status, "deregistered")
        self.assertFalse(b.l10n_se_bv_status)
        self.assertTrue(a.l10n_se_bv_error and b.l10n_se_bv_error)
        self.assertTrue(b.l10n_se_bv_next_check > fields.Datetime.now() + timedelta(hours=20))

    def test_cron_survives_unexpected_error(self):
        a = self.supplier()
        b = self.supplier(name="B AB", company_registry="559588-0419")
        client = FakeClient({"5560000001": ValueError("odd"), "5595880419": fixture("active.json")})
        self.run_with(client, self.Partner._cron_l10n_se_bv_check)
        self.assertIn("odd", a.l10n_se_bv_error)
        self.assertEqual(b.l10n_se_bv_status, "active", "the queue continues after one odd record")

    def test_sole_trader_gets_the_active_business(self):
        p = self.supplier(name="Testssons Cykel", company_registry="194001011230")
        self.run_with(FakeClient({"4001011230": fixture("sole_trader_spec.json")}), p.action_l10n_se_bv_check)
        self.assertEqual(p.l10n_se_bv_status, "active")
        self.assertFalse(p.l10n_se_bv_name_mismatch)
        self.assertNotIn("4001011230", "".join(p.message_ids.mapped("body")), "never a personal number in chatter")

    def test_cron_pings_daily_when_idle(self):
        client = FakeClient({})
        self.run_with(client, self.Partner._cron_l10n_se_bv_check)
        self.run_with(client, self.Partner._cron_l10n_se_bv_check)
        self.assertEqual(client.pings, 1, "keep the API account alive, but at most once a day")

    def test_cron_disabled(self):
        self.env["ir.config_parameter"].sudo().set_param("l10n_se_bolagsverket.enabled", False)
        self.supplier()
        client = FakeClient({})
        self.run_with(client, self.Partner._cron_l10n_se_bv_check)
        self.assertFalse(client.asked)

    def test_settings_open(self):
        self.env["res.config.settings"].create({}).execute()


@tagged("post_install", "-at_install")
class TestBolagsverketMore(TestBolagsverket):
    """More cases, with the same setup."""

    def test_ordinary_accountant_can_create_and_edit_contacts(self):
        accountant = self.env["res.users"].create({
            "name": "Kassör", "login": "bv-kassor@example.com",
            "group_ids": [(4, self.env.ref("account.group_account_manager").id),
                          (4, self.env.ref("base.group_partner_manager").id)]})
        Partner = self.Partner.with_user(accountant)
        p = Partner.create({"name": "Ny leverantör AB", "company_registry": "556000-0001", "country_id": self.se.id,
                            "supplier_rank": 1})
        p.write({"company_registry": "559588-0419"})
        self.assertEqual(p.l10n_se_bv_identity, "5595880419")

    def test_incomplete_answer_does_not_block_the_queue(self):
        a = self.supplier()
        b = self.supplier(name="B AB", company_registry="559588-0419")
        b.sudo().l10n_se_bv_next_check = fields.Datetime.now() - timedelta(days=1)
        client = FakeClient({"5560000001": fixture("source_error_spec.json"), "5595880419": fixture("active.json")})
        self.run_with(client, self.Partner._cron_l10n_se_bv_check)
        self.assertEqual(b.l10n_se_bv_status, "active", "the next partner is still checked")
        self.assertFalse(a.l10n_se_bv_status)
        self.assertTrue(fields.Datetime.now() + timedelta(hours=2) < a.l10n_se_bv_next_check
                        < fields.Datetime.now() + timedelta(hours=4))

    def test_quick_created_supplier_with_org_number_is_watched(self):
        p = self.Partner.create({"name": "Snabbskapad AB", "company_registry": "556000-0001",
                                 "country_id": self.se.id, "supplier_rank": 1})   # is_company False
        client = FakeClient({"5560000001": fixture("active.json")})
        self.run_with(client, self.Partner._cron_l10n_se_bv_check)
        self.assertEqual(p.l10n_se_bv_status, "active")

    def test_bankruptcy_todo_not_hidden_by_open_mismatch_todo(self):
        p = self.supplier(name="Fel namn AB")
        self.run_with(FakeClient({"5560000001": fixture("active.json")}), p.action_l10n_se_bv_check)
        self.run_with(FakeClient({"5560000001": fixture("bankrupt.json")}), p.action_l10n_se_bv_check)
        acts = self.env["mail.activity"].search([("res_model", "=", "res.partner"), ("res_id", "=", p.id)])
        self.assertEqual(len(acts), 2)
