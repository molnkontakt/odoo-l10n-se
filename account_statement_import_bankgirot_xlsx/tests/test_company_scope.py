"""Two companies in one database number their invoices each on their own: the first invoice of a
year has the same number, and so the same OCR number, in both. A deposit is only ever matched
within the company of its bank line, and the bank line is the receiving Bankgiro's company's.

All names and Bankgiro numbers are invented.
"""

from odoo import Command, fields
from odoo.tests import TransactionCase, new_test_user, tagged

from .test_candidates import _ocr
from .test_parsers import NBSP, RECEIVER_BG, _payer, _sheet

DEPOSIT_DATE = "2026-09-15"  # the date _sheet writes


@tagged("post_install", "-at_install")
class TestCompanyScope(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.wizard = cls.env["account.bankgirot.import.wizard"]
        cls.company_a = cls._company("Förening A", "7777-7777")
        cls.company_b = cls._company("Förening B", RECEIVER_BG)
        cls.inv_a = cls._invoice(cls.company_a, "Medlem Ett")
        cls.inv_b = cls._invoice(cls.company_b, "Medlem Två")
        cls.ocr = cls.inv_a.payment_reference

    @classmethod
    def _company(cls, name, bankgiro):
        company = cls.env["res.company"].create({"name": name})
        cls.env.user.company_ids |= company
        cls.env["account.chart.template"].try_loading(
            "generic_coa", company=company, install_demo=False
        )
        cls.env["res.partner.bank"].create(
            {"acc_number": bankgiro, "partner_id": company.partner_id.id}
        )
        return company

    @classmethod
    def _invoice(cls, company, member, amount=1000.0):
        partner = cls.env["res.partner"].create({"name": member})
        move = (
            cls.env["account.move"]
            .with_company(company)
            .create(
                {
                    "move_type": "out_invoice",
                    "partner_id": partner.id,
                    "invoice_date": fields.Date.today(),
                    "invoice_line_ids": [
                        Command.create(
                            {"name": "Avgift", "quantity": 1, "price_unit": amount, "tax_ids": []}
                        )
                    ],
                }
            )
        )
        move.action_post()
        move.payment_reference = _ocr(move.name)
        return move

    @classmethod
    def _sie_entry(cls, invoice):
        """A sales entry as the SIE import leaves it ("Kundfaktura, <number>"), in the invoice's company."""
        company = invoice.company_id
        receivable = invoice.line_ids.filtered(
            lambda ln: ln.account_id.account_type == "asset_receivable"
        )
        journal = cls.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", company.id)], limit=1
        )
        move = (
            cls.env["account.move"]
            .with_company(company)
            .create(
                {
                    "move_type": "entry",
                    "journal_id": journal.id,
                    "date": invoice.invoice_date,
                    "ref": "Kundfaktura, 1001",
                    "line_ids": [
                        Command.create(
                            {
                                "account_id": receivable.account_id.id,
                                "partner_id": invoice.partner_id.id,
                                "debit": 500.0,
                            }
                        ),
                        Command.create(
                            {"account_id": invoice.invoice_line_ids.account_id.id, "credit": 500.0}
                        ),
                    ],
                }
            )
        )
        move.action_post()
        return move

    def _bank_line(self, company, payment_ref="Insättning"):
        journal = self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", company.id)], limit=1
        )
        return self.env["account.bank.statement.line"].create(
            {
                "journal_id": journal.id,
                "date": DEPOSIT_DATE,
                "amount": 1000.0,
                "payment_ref": payment_ref,
            }
        )

    def _find(self, company, ref, amount=1000.0):
        return self.wizard._find_invoice(
            company, ref, amount, self.wizard._build_invoice_index(company)
        )

    def test_both_companies_have_the_same_number_and_ocr(self):
        """The premise: without the company scope an OCR number names two invoices."""
        self.assertEqual(self.inv_a.name, self.inv_b.name)
        self.assertEqual(self.inv_a.payment_reference, self.inv_b.payment_reference)

    def test_invoice_index_holds_only_the_company(self):
        sie_a, sie_b = self._sie_entry(self.inv_a), self._sie_entry(self.inv_b)
        index_a = self.wizard._build_invoice_index(self.company_a)
        self.assertIn(self.inv_a, index_a)
        self.assertIn(sie_a, index_a)
        self.assertNotIn(self.inv_b, index_a)
        self.assertNotIn(sie_b, index_a)
        # SIE entries by the number in their text, same number in both companies
        self.assertEqual(self._find(self.company_b, "1001", 500.0), sie_b)

    def test_ocr_number_and_name_find_the_company_invoice(self):
        for company, invoice in ((self.company_a, self.inv_a), (self.company_b, self.inv_b)):
            for ref in (self.ocr, invoice.name):
                self.assertEqual(self._find(company, ref), invoice, (company.name, ref))

    def test_settled_invoice_lookup_is_scoped(self):
        """The other company's invoice with the same number is neither reported as settled nor
        stops the lookup: B's credited invoice is reported, never replaced by B's other open invoice
        of the same amount, even though A's invoice with that number is open."""
        other_b = self._invoice(self.company_b, "Medlem Tre")
        self.inv_b._reverse_moves(cancel=True)
        self.assertEqual(self.wizard._find_settled_invoice(self.company_b, self.ocr), self.inv_b)
        self.assertFalse(self._find(self.company_b, self.ocr))
        self.assertTrue(self._find(self.company_b, other_b.payment_reference))
        self.assertFalse(self.wizard._find_settled_invoice(self.company_a, self.ocr))
        # both credited: each company gets its own (the lookup takes the newest)
        self.inv_a._reverse_moves(cancel=True)
        self.assertEqual(self.wizard._find_settled_invoice(self.company_a, self.ocr), self.inv_a)

    def test_payer_bankgiro_only_of_shared_or_company_partners(self):
        member = self.env["res.partner"].create(
            {"name": "Medlem A", "company_id": self.company_a.id}
        )
        self.env["res.partner.bank"].create({"acc_number": "555-5555", "partner_id": member.id})
        self.assertEqual(self.wizard._find_partner_by_bg("555-5555", self.company_a), member.id)
        self.assertFalse(self.wizard._find_partner_by_bg("555-5555", self.company_b))

    def test_bank_line_of_the_receiving_bankgiro(self):
        line_a, line_b = self._bank_line(self.company_a), self._bank_line(self.company_b)
        self.assertEqual(
            self.wizard._find_statement_line(DEPOSIT_DATE, 1000.0, RECEIVER_BG), line_b
        )
        self.assertEqual(
            self.wizard._find_statement_line(DEPOSIT_DATE, 1000.0, "7777-7777"), line_a
        )

    def test_no_bank_line_of_another_company(self):
        """Only the other company has a line that day, even one labelled as a Bankgiro deposit."""
        self._bank_line(self.company_a)
        self._bank_line(self.company_a, "77777777 Bankgiro inbetalning")
        self.assertFalse(self.wizard._find_statement_line(DEPOSIT_DATE, 1000.0, RECEIVER_BG))
        # an unknown receiving Bankgiro keeps the old behaviour: any company's line
        self.assertTrue(self.wizard._find_statement_line(DEPOSIT_DATE, 1000.0, "1234-5678"))

    def test_no_bank_line_when_the_bankgiro_company_is_not_selected(self):
        """B's deposit never lands on A's line, also when only A is selected in the company switcher.
        An accounting user who is not an ERP manager does not see B then, so the lookup of the
        receiving Bankgiro's company used to find none and fell back to any line of A. The other
        tests run as superuser, which sees every company."""
        self._bank_line(self.company_a)
        self._bank_line(self.company_a, "55555555 Bankgiro inbetalning")
        line_b = self._bank_line(self.company_b)
        both = self.company_a | self.company_b
        user = new_test_user(
            self.env,
            login="bokforare",
            groups="account.group_account_user",
            company_id=self.company_a.id,
            company_ids=[Command.set(both.ids)],
        )
        as_user = self.wizard.with_user(user)
        self.assertEqual(
            as_user.with_context(allowed_company_ids=both.ids)._find_statement_line(
                DEPOSIT_DATE, 1000.0, RECEIVER_BG
            ),
            line_b,
        )
        # the superuser too: record rules do not hide B's lines from it
        for wizard in (as_user, self.wizard):
            self.assertFalse(
                wizard.with_context(allowed_company_ids=self.company_a.ids)._find_statement_line(
                    DEPOSIT_DATE, 1000.0, RECEIVER_BG
                ),
                wizard.env.user.login,
            )

    def test_import_names_the_company_to_select(self):
        line_a = self._bank_line(self.company_a)
        self._bank_line(self.company_b)
        sheet = _sheet(
            [_payer("Anna Testsson", self.ocr, None, f"1{NBSP}000,00")],
            total=("1", f"1{NBSP}000,00", "TOTALT"),
        )
        wizard = self.wizard.with_context(allowed_company_ids=self.company_a.ids).create(
            {"file_ids": [Command.create({"name": "insattning.xlsx", "raw": sheet})]}
        )
        wizard.action_import()
        self.assertIn(
            f"Bankgirot {RECEIVER_BG} tillhör Förening B – välj bolaget i bolagsväljaren",
            str(wizard.result_html),
        )
        # A's invoice has the same OCR number: A's line of the same day and amount stays untouched
        self.assertIn("Matchade: 0", str(wizard.result_html))
        self.assertFalse(line_a.partner_id)

    def test_import_matches_within_the_bank_line_company(self):
        """Through the wizard: B's deposit sets B's member on B's bank line; A's line is untouched."""
        line_a, line_b = self._bank_line(self.company_a), self._bank_line(self.company_b)
        sheet = _sheet(
            [_payer("Anna Testsson", self.ocr, None, f"1{NBSP}000,00")],
            total=("1", f"1{NBSP}000,00", "TOTALT"),
        )
        wizard = self.wizard.create(
            {"file_ids": [Command.create({"name": "insattning.xlsx", "raw": sheet})]}
        )
        wizard.action_import()
        self.assertIn("Matchade: 1", str(wizard.result_html))
        self.assertEqual(line_b.partner_id, self.inv_b.partner_id)
        self.assertFalse(line_a.partner_id)
