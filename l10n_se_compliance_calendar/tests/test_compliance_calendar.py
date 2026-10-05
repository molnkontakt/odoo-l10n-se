from datetime import date

from freezegun import freeze_time

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

TODAY = "2026-10-01"


@tagged("post_install", "-at_install")
class TestComplianceCalendar(TransactionCase):
    """Synchronisation of the compliance calendar into calendar.event.

    The tests create their own companies and only look at events of those companies, so they can
    also run inside an existing database (rolled back).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env["res.company"].create({
            "name": "Compliance Test A",
            "l10n_se_cc_active": True,
            "l10n_se_cc_company_form": "ab",
            "l10n_se_cc_vat_period": "quarter",
            "l10n_se_cc_employer": True,
            "l10n_se_cc_f_tax": True,
        })
        cls.company_b = cls.env["res.company"].create({
            "name": "Compliance Test B",
            "l10n_se_cc_active": True,
            "l10n_se_cc_company_form": "ideell_forening",
        })
        cls.Event = cls.env["calendar.event"].with_context(active_test=False)

    def _events(self, company):
        return self.Event.search([("l10n_se_cc_company_id", "=", company.id)])

    def _keys(self, company):
        return set(self._events(company).mapped("l10n_se_cc_key"))

    def _sync(self, companies=None):
        with freeze_time(TODAY):
            (companies or (self.company_a | self.company_b))._l10n_se_cc_sync()

    # ------------------------------------------------------------------
    # Creating, updating and removing
    # ------------------------------------------------------------------

    def test_defaults(self):
        company = self.env["res.company"].create({"name": "Compliance Test Defaults"})
        self.assertFalse(company.l10n_se_cc_active)
        self.assertEqual(company.l10n_se_cc_company_form, "ab")
        self.assertEqual(company.l10n_se_cc_vat_period, "none")
        self.assertFalse(company.l10n_se_cc_employer)
        self.assertFalse(company.l10n_se_cc_f_tax)
        self.assertFalse(company.l10n_se_cc_alarm_id)
        with freeze_time(TODAY):
            company._l10n_se_cc_sync()
        self.assertFalse(self._events(company))

    def test_sync_creates_all_day_events(self):
        self._sync()
        events = self._events(self.company_a)
        # 2026-2028: 4 VAT quarters x 3, 12 AGI x 3, 12 F-tax x 3, income tax, AGM, report x 3.
        self.assertEqual(len(events), 12 + 36 + 36 + 9)
        self.assertTrue(all(events.mapped("allday")))
        self.assertEqual(events.mapped("start_date"), events.mapped("stop_date"))
        self.assertEqual(min(events.mapped("start_date")), date(2026, 1, 19))
        self.assertEqual(max(events.mapped("start_date")), date(2028, 12, 12))
        q2 = events.filtered(lambda e: e.l10n_se_cc_key == "vat:2026-q2")
        self.assertEqual(q2.start_date, date(2026, 8, 17))
        self.assertEqual(q2.l10n_se_cc_kind, "vat")
        self.assertIn("26 kap.", q2.l10n_se_cc_legal_ref)
        self.assertEqual(q2.partner_ids, self.company_a.l10n_se_cc_partner_id)
        self.assertFalse(q2.alarm_ids)
        self.assertFalse(q2.user_id)
        income = events.filtered(lambda e: e.l10n_se_cc_key == "income_tax:fy2026-12-31")
        self.assertEqual(income.start_date, date(2027, 8, 2))
        # The non-profit association has no statutory dates with this profile.
        self.assertFalse(self._events(self.company_b))

    def test_sync_twice_no_duplicates(self):
        self._sync()
        first = self._events(self.company_a)
        write_dates = {e.id: e.write_date for e in first}
        self._sync()
        second = self._events(self.company_a)
        self.assertEqual(first, second)
        self.assertEqual(write_dates, {e.id: e.write_date for e in second})
        self.assertEqual(len(second.mapped("l10n_se_cc_key")), len(second))

    def test_profile_change_updates_and_removes(self):
        self._sync()
        q4 = self._events(self.company_a).filtered(lambda e: e.l10n_se_cc_key == "vat:2026-q4")
        self.company_a.write({"l10n_se_cc_vat_period": "month", "l10n_se_cc_employer": False})
        self._sync()
        keys = self._keys(self.company_a)
        self.assertFalse(q4.exists())
        self.assertFalse(any(k.startswith("employer") for k in keys))
        self.assertIn("vat:2026-10", keys)
        self.assertIn("f_tax:2026-10", keys)

    def test_switch_off_kind(self):
        self._sync()
        self.company_a.l10n_se_cc_show_f_tax = False
        self._sync()
        self.assertFalse(any(k.startswith("f_tax") for k in self._keys(self.company_a)))

    def test_removal_only_touches_own_events(self):
        self._sync()
        foreign = self.Event.create({
            "name": "Someone else's meeting",
            "start": "2026-11-12 09:00:00",
            "stop": "2026-11-12 10:00:00",
            "partner_ids": [Command.set(self.company_a.l10n_se_cc_partner_id.ids)],
        })
        self.company_a.l10n_se_cc_active = False
        self._sync()
        self.assertFalse(self._events(self.company_a))
        self.assertTrue(foreign.exists())

    def test_past_years_kept(self):
        self._sync()
        old = self._events(self.company_a).filtered(lambda e: e.l10n_se_cc_key == "vat:2025-q4")
        self.assertEqual(old.start_date, date(2026, 2, 12))
        with freeze_time("2027-03-01"):
            self.company_a._l10n_se_cc_sync()
        # 2026 is now history: kept, not recreated or removed; 2029 is added.
        self.assertTrue(old.exists())
        self.assertIn("vat:2029-q1", self._keys(self.company_a))

    def test_edited_event_is_restored(self):
        self._sync()
        event = self._events(self.company_a).filtered(lambda e: e.l10n_se_cc_key == "vat:2026-q3")
        event.write({"name": "changed", "l10n_se_cc_hash": "x"})
        self._sync()
        self.assertTrue(event.name.startswith("VAT return") or event.name.startswith("Moms"))

    # ------------------------------------------------------------------
    # No mail, contact without e-mail
    # ------------------------------------------------------------------

    def test_no_mail_mail(self):
        Mail = self.env["mail.mail"].sudo()
        before = Mail.search_count([])
        alarm = self.env["calendar.alarm"].create({
            "name": "Test notification", "alarm_type": "notification",
            "duration": 1, "interval": "days",
        })
        self.company_a.l10n_se_cc_alarm_id = alarm
        self._sync()
        self.company_a.l10n_se_cc_vat_period = "month"
        self._sync()
        self.company_a.l10n_se_cc_active = False
        self._sync()
        self.assertEqual(Mail.search_count([]), before)

    def test_contact_cannot_have_email(self):
        self._sync()
        partner = self.company_a.l10n_se_cc_partner_id
        self.assertTrue(partner)
        self.assertFalse(partner.email)
        self.assertEqual(partner.l10n_se_cc_company_id, self.company_a)
        with self.assertRaises(ValidationError):
            partner.write({"email": "someone@example.com"})
        with self.assertRaises(ValidationError):
            self.env["res.partner"].create({
                "name": "x", "email": "x@example.com", "l10n_se_cc_company_id": self.company_b.id,
            })
        # One contact per company, reused.
        self._sync()
        self.assertEqual(
            self.env["res.partner"].search_count([("l10n_se_cc_company_id", "=", self.company_a.id)]),
            1,
        )

    # ------------------------------------------------------------------
    # Custom rules
    # ------------------------------------------------------------------

    def test_custom_fixed_rule(self):
        Rule = self.env["l10n_se.compliance.custom.rule"]
        Rule.create({
            "name": "Year-end accounts", "rule_type": "fixed", "month": "2", "day": 15,
            "company_id": self.company_b.id,
        })
        Rule.create({
            "name": "Motions", "rule_type": "fixed", "month": "2", "day": 31,
            "company_id": self.company_b.id, "non_business_day": "previous",
        })
        self._sync()
        events = self._events(self.company_b)
        accounts = events.filtered(lambda e: e.name == "Year-end accounts")
        self.assertEqual(
            sorted(accounts.mapped("start_date")),
            [date(2026, 2, 15), date(2027, 2, 15), date(2028, 2, 15)],
        )
        motions = events.filtered(lambda e: e.name == "Motions")
        # Last day of February; 2026-02-28 and 2027-02-28 are weekend days -> Friday before.
        self.assertEqual(
            sorted(motions.mapped("start_date")),
            [date(2026, 2, 27), date(2027, 2, 26), date(2028, 2, 29)],
        )
        self.assertTrue(all(k.startswith("custom:") for k in events.mapped("l10n_se_cc_key")))
        self.assertFalse(self._keys(self.company_a) & set(events.mapped("l10n_se_cc_key")))

    def test_custom_relative_rule(self):
        Anchor = self.env["l10n_se.compliance.anchor"]
        Rule = self.env["l10n_se.compliance.custom.rule"]
        Anchor.create({"name": "Annual meeting", "date": "2027-04-24", "company_id": self.company_b.id})
        Anchor.create({"name": "annual  MEETING", "date": "2028-04-22", "company_id": self.company_b.id})
        Rule.create({
            "name": "Accounts to the auditor", "rule_type": "relative", "anchor_name": "Annual meeting",
            "offset": 6, "offset_unit": "weeks", "offset_direction": "before",
            "company_id": self.company_b.id,
        })
        Rule.create({
            "name": "Notice of meeting", "rule_type": "relative", "anchor_name": "Annual meeting",
            "offset": 14, "offset_unit": "days", "offset_direction": "before",
            "company_id": self.company_b.id,
        })
        self._sync()
        events = self._events(self.company_b)
        by_name = {}
        for event in events:
            by_name.setdefault(event.name, []).append(event.start_date)
        self.assertEqual(sorted(by_name["Accounts to the auditor"]), [date(2027, 3, 13), date(2028, 3, 11)])
        self.assertEqual(sorted(by_name["Notice of meeting"]), [date(2027, 4, 10), date(2028, 4, 8)])
        self.assertEqual(sorted(by_name["Annual meeting"]), [date(2027, 4, 24)])
        self.assertIn("2027-04-24", events.filtered(lambda e: e.name == "Notice of meeting"
                                                    and e.start_date.year == 2027).description)
        # Moving the anchor moves the dates, without duplicates.
        Anchor.search([("date", "=", "2027-04-24")]).date = "2027-05-08"
        self._sync()
        notices = self._events(self.company_b).filtered(lambda e: e.name == "Notice of meeting")
        self.assertEqual(sorted(notices.mapped("start_date")), [date(2027, 4, 24), date(2028, 4, 8)])

    def test_anchor_one_per_year(self):
        Anchor = self.env["l10n_se.compliance.anchor"]
        Anchor.create({"name": "Annual meeting", "date": "2027-04-24", "company_id": self.company_b.id})
        with self.assertRaises(ValidationError):
            Anchor.create({"name": "annual meeting ", "date": "2027-05-01", "company_id": self.company_b.id})

    def test_rule_validation(self):
        Rule = self.env["l10n_se.compliance.custom.rule"]
        with self.assertRaises(ValidationError):
            Rule.create({"name": "x", "rule_type": "fixed", "month": "2", "day": 32})
        with self.assertRaises(ValidationError):
            Rule.create({"name": "x", "rule_type": "relative", "anchor_name": " "})

    def test_vat_period_constraint(self):
        with self.assertRaises(ValidationError):
            self.company_a.write({"l10n_se_cc_turnover": "gt40", "l10n_se_cc_vat_period": "quarter"})

    # ------------------------------------------------------------------
    # Multi-company
    # ------------------------------------------------------------------

    def test_multi_company_isolation(self):
        Rule = self.env["l10n_se.compliance.custom.rule"]
        Rule.create({"name": "Only A", "rule_type": "fixed", "month": "3", "day": 1,
                     "company_id": self.company_a.id})
        Rule.create({"name": "Shared", "rule_type": "fixed", "month": "3", "day": 2,
                     "company_id": False})
        self._sync()
        b_before = self._events(self.company_b)
        b_snapshot = {e.id: (e.l10n_se_cc_key, e.start_date, e.write_date) for e in b_before}
        self.assertEqual(set(b_before.mapped("name")), {"Shared"})
        self.assertIn("Only A", self._events(self.company_a).mapped("name"))
        # Changing and syncing A alone leaves B untouched.
        self.company_a.write({"l10n_se_cc_vat_period": "month", "l10n_se_cc_active": True})
        self._sync(self.company_a)
        self.company_a.l10n_se_cc_active = False
        self._sync(self.company_a)
        b_after = self._events(self.company_b)
        self.assertEqual(b_snapshot, {e.id: (e.l10n_se_cc_key, e.start_date, e.write_date) for e in b_after})
        self.assertNotEqual(self.company_a.l10n_se_cc_partner_id, self.company_b.l10n_se_cc_partner_id)

    # ------------------------------------------------------------------
    # Filters, access, translations, view
    # ------------------------------------------------------------------

    def test_calendar_filters_for_groups(self):
        user = new_test_user(
            self.env, login="cc_filter_user", groups="base.group_user",
            company_id=self.company_a.id, company_ids=[Command.set(self.company_a.ids)],
        )
        group = self.env["res.groups"].create({"name": "Compliance test group",
                                               "user_ids": [Command.link(user.id)]})
        self.company_a.l10n_se_cc_filter_group_ids = group
        self._sync()
        partner = self.company_a.l10n_se_cc_partner_id
        filters = self.env["calendar.filters"].search([("partner_id", "=", partner.id)])
        self.assertEqual(filters.user_id, user)
        self.assertTrue(filters.partner_checked)
        self._sync()
        self.assertEqual(
            self.env["calendar.filters"].search_count([("partner_id", "=", partner.id)]), 1
        )
        # B's contact is not given to a user without access to B.
        self.company_b.l10n_se_cc_filter_group_ids = group
        self._sync()
        self.assertFalse(self.env["calendar.filters"].search(
            [("partner_id", "=", self.company_b.l10n_se_cc_partner_id.id)]
        ))

    def test_refresh_requires_admin(self):
        user = new_test_user(self.env, login="cc_plain_user", groups="base.group_user")
        with self.assertRaises(AccessError):
            self.env["calendar.event"].with_user(user).action_l10n_se_cc_refresh()
        with self.assertRaises(AccessError):
            self.company_a.with_user(user).action_l10n_se_cc_sync()

    def test_swedish_titles(self):
        self.env["res.lang"]._activate_lang("sv_SE")
        self.env["ir.module.module"]._load_module_terms(["l10n_se_compliance_calendar"], ["sv_SE"])
        self.company_a.partner_id.lang = "sv_SE"
        self._sync()
        events = self._events(self.company_a)
        q2 = events.filtered(lambda e: e.l10n_se_cc_key == "vat:2026-q2")
        self.assertEqual(q2.name, "Momsdeklaration för april–juni 2026")
        self.assertTrue(self.company_a.l10n_se_cc_partner_id.name.startswith("Årshjul – "))

    def test_list_view_and_action(self):
        action = self.env.ref("l10n_se_compliance_calendar.action_l10n_se_cc_events")
        views = self.env["calendar.event"].get_views(
            [(self.env.ref("l10n_se_compliance_calendar.calendar_event_view_list_l10n_se_cc").id, "list")]
        )
        arch = views["views"]["list"]["arch"]
        self.assertIn('name="start_date"', arch)
        self.assertNotIn('name="start"', arch.replace('name="start_date"', ""))
        self.assertEqual(action.res_model, "calendar.event")
        settings = self.env["res.company"].with_company(self.company_a).action_l10n_se_cc_open_settings()
        self.assertEqual(settings["res_id"], self.company_a.id)
