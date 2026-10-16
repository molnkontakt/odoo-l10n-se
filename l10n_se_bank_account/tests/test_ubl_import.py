# The supplier, organisation numbers and giro numbers in the fixture are invented; the giro
# numbers only satisfy the check digits.
import re
from pathlib import Path

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

FIXTURE = (Path(__file__).parent / "data" / "peppol_bill_giro.xml").read_bytes()
BANKGIRO = "5551239"  # BG 555-1239
PLUSGIRO = "12345674"  # PG 1234567-4


@tagged("post_install", "-at_install", "l10n_se")
class TestUblGiroImport(TransactionCase):
    """A Peppol BIS 3 vendor bill with a Bankgiro and a Plusgiro (SE:BANKGIRO / SE:PLUSGIRO).

    Plain TransactionCase (not account's test common, which needs the test_mail addon): a SEK
    company with Odoo's generic chart of accounts.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sek = cls.env.ref("base.SEK")
        sek.active = True
        company = cls.env["res.company"].create({"name": "Testbolaget AB", "currency_id": sek.id})
        cls.env.user.company_ids |= company
        cls.env["account.chart.template"].try_loading("generic_coa", company=company, install_demo=False)
        company.currency_id = sek  # the generic chart sets its own currency
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=company.ids))
        cls.company = company
        cls.journal = cls.env["account.journal"].search(
            [("type", "=", "purchase"), ("company_id", "=", company.id)], limit=1
        )

    def _import(self, xml=FIXTURE):
        attachment = self.env["ir.attachment"].create(
            {"name": "bill.xml", "mimetype": "application/xml", "raw": xml}
        )
        bill = self.journal._create_document_from_attachment(attachment.id)
        self.assertEqual(bill.move_type, "in_invoice")
        self.assertEqual(bill.partner_id.name, "Exempelleverantören AB")
        return bill

    def _accounts(self, bill):
        return self.env["res.partner.bank"].search(
            [("partner_id", "child_of", bill.partner_id.commercial_partner_id.id)], order="id"
        )

    def _chatter(self, bill):
        return " ".join(bill.message_ids.mapped("body"))

    def test_both_giros_typed_formatted_and_bankgiro_preferred(self):
        bill = self._import()
        accounts = self._accounts(bill)
        self.assertEqual(
            {(a.acc_number, a.l10n_se_account_type) for a in accounts},
            {("BG 555-1239", "bankgiro"), ("PG 1234567-4", "plusgiro")},
        )
        self.assertEqual(bill.partner_bank_id.acc_number, "BG 555-1239")
        self.assertEqual(bill.partner_bank_id._l10n_se_payment_account(), BANKGIRO)
        self.assertFalse(bill.partner_bank_id.l10n_se_account_problem)
        self.assertEqual(bill.payment_reference, "12345678901234")
        self.assertNotIn("check digit", self._chatter(bill))

    def test_only_plusgiro_is_used(self):
        xml = re.sub(rb"<cac:PaymentMeans>(?:(?!</cac:PaymentMeans>).)*SE:BANKGIRO.*?</cac:PaymentMeans>", b"", FIXTURE, flags=re.S)
        self.assertNotIn(b"BANKGIRO", xml)
        bill = self._import(xml)
        self.assertEqual(bill.partner_bank_id.acc_number, "PG 1234567-4")
        self.assertEqual(bill.partner_bank_id.l10n_se_account_type, "plusgiro")
        self.assertEqual(len(self._accounts(bill)), 1)

    def test_scheme_in_scheme_id_attribute_and_lower_case(self):
        xml = FIXTURE.replace(
            b"<cbc:ID>5551239</cbc:ID>\n      <cac:FinancialInstitutionBranch><cbc:ID>SE:BANKGIRO</cbc:ID></cac:FinancialInstitutionBranch>",
            b'<cbc:ID schemeID="se:bankgiro">5551239</cbc:ID>',
        ).replace(b"SE:PLUSGIRO", b"Plusgiro")
        self.assertNotIn(b"SE:BANKGIRO", xml)
        bill = self._import(xml)
        self.assertEqual(
            {(a.acc_number, a.l10n_se_account_type) for a in self._accounts(bill)},
            {("BG 555-1239", "bankgiro"), ("PG 1234567-4", "plusgiro")},
        )
        self.assertEqual(bill.partner_bank_id.acc_number, "BG 555-1239")

    def test_reimport_reuses_the_accounts(self):
        first = self._import()
        second = self._import()
        self.assertEqual(first.partner_id, second.partner_id)
        self.assertEqual(len(self._accounts(second)), 2)
        self.assertEqual(second.partner_bank_id, first.partner_bank_id)

    def test_existing_giro_written_by_hand_is_reused(self):
        partner = self.env["res.partner"].create({"name": "Exempelleverantören AB", "is_company": True})
        existing = self.env["res.partner.bank"].create(
            {"acc_number": "555-1239", "partner_id": partner.id, "allow_out_payment": True}
        )
        self.assertEqual(existing.l10n_se_account_type, "bankgiro")
        bill = self._import()
        self.assertEqual(bill.partner_id, partner)
        self.assertEqual(bill.partner_bank_id, existing)
        self.assertEqual(existing.acc_number, "555-1239")  # untouched
        self.assertEqual(
            [a.acc_number for a in self._accounts(bill)], ["555-1239", "PG 1234567-4"]
        )

    def test_existing_untyped_number_is_reused_with_a_note(self):
        """An account created by an earlier import (bare digits, type Other) is not duplicated."""
        partner = self.env["res.partner"].create({"name": "Exempelleverantören AB", "is_company": True})
        existing = self.env["res.partner.bank"].create({"acc_number": BANKGIRO, "partner_id": partner.id})
        self.assertEqual(existing.l10n_se_account_type, "other")
        bill = self._import()
        self.assertEqual(bill.partner_id, partner)
        self.assertEqual(len(self._accounts(bill)), 2)
        self.assertEqual(existing.l10n_se_account_type, "other")  # not changed behind the user's back
        self.assertIn("has no Swedish account type", self._chatter(bill))
        # the untyped Bankgiro still comes first: it is the account the bill names
        self.assertEqual(bill.partner_bank_id, existing)

    def test_other_giro_type_with_same_digits_is_not_reused(self):
        partner = self.env["res.partner"].create({"name": "Exempelleverantören AB", "is_company": True})
        plusgiro = self.env["res.partner.bank"].create({"acc_number": "PG 555123-9", "partner_id": partner.id})
        self.assertEqual(plusgiro.l10n_se_account_type, "plusgiro")
        bill = self._import()
        self.assertEqual(bill.partner_bank_id.acc_number, "BG 555-1239")
        self.assertEqual(len(self._accounts(bill)), 3)

    def test_invalid_check_digit_stays_raw_with_a_note(self):
        xml = FIXTURE.replace(b"<cbc:ID>5551239</cbc:ID>", b"<cbc:ID>5551238</cbc:ID>")
        bill = self._import(xml)
        accounts = {a.acc_number: a.l10n_se_account_type for a in self._accounts(bill)}
        self.assertEqual(accounts, {"5551238": "other", "PG 1234567-4": "plusgiro"})
        self.assertIn("5551238", self._chatter(bill))
        self.assertIn("invalid check digit", self._chatter(bill))
        # the valid Plusgiro is the one to pay
        self.assertEqual(bill.partner_bank_id.acc_number, "PG 1234567-4")

    def test_no_scheme_is_left_alone(self):
        xml = re.sub(rb"\s*<cac:FinancialInstitutionBranch>.*?</cac:FinancialInstitutionBranch>", b"", FIXTURE)
        self.assertNotIn(b"GIRO", xml)
        bill = self._import(xml)
        self.assertEqual(
            {a.l10n_se_account_type for a in self._accounts(bill)}, {"other"}
        )  # a bare 7-8 digit number can be either giro: never guessed
        self.assertEqual(len(self._accounts(bill)), 2)
