# All account numbers, giro numbers and references are invented; they only satisfy the check digits.
import csv
import io
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import psycopg2

from odoo import Command, fields
from odoo.addons.l10n_se_bank_account.lib import se_bank
from odoo.addons.l10n_se_payment_file_seb_csv.lib import seb_csv
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, tagged
from odoo.tests.common import TransactionCase, new_test_user
from odoo.tools import mute_logger

TEMPLATE = (Path(__file__).parent / "data" / "Domestic.csv").read_bytes()
SEB_IBAN = "SE3150000000052031234560"  # SEB 5203-123 45 60
SWEDBANK_IBAN = "SE7280000832791234567897"  # Swedbank 8327-9, 123 456 789-7
DE_IBAN = "DE89370400440532013000"  # the standard example IBAN


@tagged("post_install", "-at_install", "l10n_se")
class TestSebCsvExport(TransactionCase):
    """Plain TransactionCase (not account's test common, which needs the test_mail addon): a SEK
    company with Odoo's generic chart of accounts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sek = cls.env.ref("base.SEK")
        cls.sek.active = True
        company = cls._create_company("Testbolaget AB")
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=company.ids))
        cls.today = fields.Date.context_today(cls.env.user)
        cls.journal = cls._seb_journal(cls.env.company)

    @classmethod
    def _create_company(cls, name):
        company = cls.env["res.company"].create({"name": name, "currency_id": cls.sek.id})
        cls.env.user.company_ids |= company
        cls.env["account.chart.template"].try_loading(
            "generic_coa", company=company, install_demo=False
        )
        company.currency_id = cls.sek  # the generic chart sets its own currency
        return company

    def _journal(self, journal_type, company=None):
        return self.env["account.journal"].search(
            [
                ("type", "=", journal_type),
                ("company_id", "=", (company or self.env.company).id),
                ("l10n_se_seb_csv_export", "=", False),
            ],
            limit=1,
        )

    @classmethod
    def _seb_journal(cls, company, acc_number=SEB_IBAN, code="SEBX"):
        bank = cls.env["res.partner.bank"].create(
            {"acc_number": acc_number, "partner_id": company.partner_id.id}
        )
        return cls.env["account.journal"].create(
            {
                "name": f"SEB {code}",
                "type": "bank",
                "code": code,
                "company_id": company.id,
                "bank_account_id": bank.id,
                "l10n_se_seb_csv_export": True,
            }
        )

    def _partner(self, name="Leverantör AB"):
        return self.env["res.partner"].create({"name": name, "is_company": True})

    def _bank(self, partner, acc_number, trusted=True, **vals):
        return self.env["res.partner.bank"].create(
            {"acc_number": acc_number, "partner_id": partner.id, "allow_out_payment": trusted, **vals}
        )

    def _bill(
        self,
        acc_number="BG 555-1239",
        payment_reference=False,
        ref=False,
        amount=1000.0,
        partner=None,
        move_type="in_invoice",
        currency=None,
        due=None,
        trusted=True,
        post=True,
        company=None,
        bank_vals=None,
    ):
        partner = partner or self._partner()
        bank = self.env["res.partner.bank"]
        if acc_number:
            bank = self._bank(partner, acc_number, trusted, **(bank_vals or {}))
        move = (
            self.env["account.move"]
            .with_company(company or self.env.company)
            .create(
                {
                    "move_type": move_type,
                    "partner_id": partner.id,
                    "invoice_date": self.today,
                    "invoice_payment_term_id": False,
                    "invoice_date_due": due or self.today + timedelta(days=10),
                    "payment_reference": payment_reference,
                    "ref": ref,
                    "partner_bank_id": bank.id,
                    "currency_id": (currency or self.sek).id,
                    "invoice_line_ids": [
                        Command.create(
                            {"name": "Tjänst", "quantity": 1, "price_unit": amount, "tax_ids": []}
                        )
                    ],
                }
            )
        )
        if post:
            move.action_post()
        return move

    def _wizard(self, moves, user=None, **vals):
        Wizard = self.env["l10n_se.payment.export.wizard"]
        if user:
            Wizard = Wizard.with_user(user)
        return Wizard.with_context(active_model="account.move", active_ids=moves.ids).create(vals)

    def _line(self, wizard, move):
        return wizard.line_ids.filtered(lambda line: line.move_id == move)

    def _export(self, moves, user=None, **vals):
        action = self._wizard(moves, user=user, **vals).action_generate()
        return self.env["l10n_se.payment.export"].browse(action["res_id"])

    def _rows(self, batch):
        text = batch.attachment_id.raw.decode("utf-8")
        rows = list(csv.reader(io.StringIO(text, newline="")))
        return [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]

    def _messages(self, record):
        return "\n".join(record.message_ids.mapped("body"))

    # --- Journal ---------------------------------------------------------------------------------

    def test_journal_from_account(self):
        self.assertEqual(self.journal.l10n_se_seb_csv_from_account, "52031234560")
        self.assertFalse(self.journal.l10n_se_seb_csv_problem)
        bban_journal = self._seb_journal(self.env.company, "5203-123 45 60", "SEBY")
        self.assertEqual(bban_journal.l10n_se_seb_csv_from_account, "52031234560")
        # the IBAN form, should a test upload show that SEB wants it
        with patch.object(seb_csv, "FROM_ACCOUNT_FORM", "iban"):
            self.assertEqual(self.journal._l10n_se_seb_csv_from_account(), SEB_IBAN)
            self.assertEqual(bban_journal._l10n_se_seb_csv_from_account(), SEB_IBAN)
        with self.assertRaisesRegex(ValidationError, "SEB account"):
            self._seb_journal(self.env.company, SWEDBANK_IBAN, "SWBX")
        with self.assertRaisesRegex(ValidationError, "no bank account"):
            self.env["account.journal"].create(
                {"name": "No account", "type": "bank", "code": "NOAC", "l10n_se_seb_csv_export": True}
            )
        with self.assertRaises(ValidationError):
            self._journal("purchase").l10n_se_seb_csv_export = True

    # --- The file ------------------------------------------------------------------------------

    def test_export_every_account_type(self):
        cases = [
            # account, payment reference, bill reference -> to account, format, column, value
            ("BG 555-1239", "1234567897", "F-1001", "5551239", "BG", "OCR", "1234567897"),
            # SEB refuses "Fakturanummer" for Bankgiro/Plusgiro (upload 2026-09-29): a message
            ("BG 5551-2347", False, "F-1002", "55512347", "BG", "Meddelande", "F-1002"),
            ("PG 12 34 56-6", "12345 67897", False, "1234566", "PG", "OCR", "1234567897"),
            ("5203-123 45 60", False, "F-1004", "52031234560", "BBAN", "Fakturanummer", "F-1004"),
            ("6789 123 456 789", False, "F-1005", "123456789", "BBAN", "Fakturanummer", "F-1005"),
            ("8327-9 123 456 789-7", False, "F-1006", "832791234567897", "BBAN", "Fakturanummer",
             "F-1006"),
            ("9180-1234567897", False, "F-1007", "1234567897", "BBAN", "Fakturanummer", "F-1007"),
            (SWEDBANK_IBAN, False, "F-1008", SWEDBANK_IBAN, "IBAN", "Fakturanummer", "F-1008"),
        ]
        moves = self.env["account.move"]
        for i, (acc, pref, ref, *_expected) in enumerate(cases):
            moves |= self._bill(acc, pref, ref, amount=100.0 + i)
        wizard = self._wizard(moves)
        self.assertEqual(wizard.journal_id, self.journal)
        self.assertEqual(wizard.from_account, "52031234560")
        self.assertEqual(set(wizard.line_ids.mapped("status")), {"ok"}, wizard.line_ids.mapped("problems"))
        self.assertEqual(wizard.ready_count, len(cases))
        batch = self.env["l10n_se.payment.export"].browse(wizard.action_generate()["res_id"])

        self.assertEqual(batch.state, "exported")
        self.assertEqual(batch.from_account, "52031234560")
        self.assertEqual(batch.line_count, len(cases))
        self.assertAlmostEqual(batch.amount_total, sum(100.0 + i for i in range(len(cases))))
        data = batch.attachment_id.raw
        self.assertEqual(data.split(b"\n", 1)[0], TEMPLATE.split(b"\n", 1)[0])
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", data)
        self.assertTrue(data.endswith(b"\n"))
        self.assertEqual(batch.attachment_id.name, f"{batch.name}.csv")
        self.assertEqual(batch.attachment_id.res_model, "l10n_se.payment.export")

        rows = self._rows(batch)
        self.assertEqual(len(rows), len(cases))
        due = se_bank.previous_bank_day(self.today + timedelta(days=10)).isoformat()
        for i, (row, move, case) in enumerate(zip(rows, moves, cases, strict=True), start=1):
            _acc, _pref, _ref, to_account, code, column, value = case
            with self.subTest(account=case[0]):
                self.assertEqual(len(row), 37)
                self.assertEqual(row["Betaltyp"], "sverige inrikes")
                self.assertEqual(row["Från konto"], "52031234560")
                self.assertEqual(row["Till konto"], to_account)
                self.assertEqual(row["Till konto - format (IBAN/BBAN/BG/PG)"], code)
                self.assertEqual(row["Mottagarens namn"], "Leverantör AB")
                self.assertEqual(row["Belopp"], f"{100.0 + i - 1:.2f}")
                self.assertEqual(row["Betaldatum"], due)
                self.assertEqual(row[column], value)
                others = {"OCR", "Fakturanummer", "RF", "Meddelande"} - {column}
                self.assertFalse(any(row[c] for c in others), row)
                self.assertEqual(row["Egen anteckning"], move.name)
                sender_ref = f"{batch.name}-{i:03d}"
                # SEB refuses the field for Bankgiro/Plusgiro payments; kept on the line for the trace
                self.assertEqual(row["Avsändarens referens"], sender_ref if code in ("BBAN", "IBAN") else "")
                self.assertEqual(row["Standard eller Express"], "Standard")
                line = batch.line_ids.filtered(lambda line, move=move: line.move_id == move)
                self.assertEqual(line.state, "exported")
                self.assertEqual(line.sender_reference, sender_ref)
                self.assertEqual(line.own_note, move.name)
                self.assertEqual(line.to_account, to_account)
                self.assertEqual(line.to_account_format, code)
                self.assertEqual(line.reference, value)
                self.assertEqual(line.residual_at_export, move.amount_residual)
                # the bill stays unpaid, is marked and has a chatter note
                self.assertEqual(move.payment_state, "not_paid")
                self.assertEqual(move.l10n_se_payment_export_id, batch)
                self.assertIn("Exported to SEB payment file", self._messages(move))
                self.assertIn(line.sender_reference, self._messages(move))
        self.assertIn(batch.attachment_id, batch.message_ids.attachment_ids)
        Move = self.env["account.move"]
        self.assertEqual(Move.search([("id", "in", moves.ids), ("l10n_se_payment_exported", "=", True)]), moves)
        self.assertFalse(Move.search([("id", "in", moves.ids), ("l10n_se_payment_exported", "=", False)]))
        self.assertFalse(Move.search([("id", "in", moves.ids), ("l10n_se_payment_exported", "!=", True)]))
        self.assertTrue(batch.action_download()["url"].endswith("?download=true"))

    def test_references(self):
        not_ocr = self._bill("BG 555-1239", "12345", "F-2001")
        bban_numeric = self._bill("5203-123 45 60", "1234567897", False)
        rf = self._bill("5203-123 45 60", "RF18 5390 0754 7034", "F-2003")
        nothing = self._bill("BG 555-1239", False, False)
        long_ref = self._bill("5203-123 45 60", False, "Faktura " + "9" * 40)  # over 35 on a bank account
        text_ref = self._bill("BG 555-1239", "Faktura 4711, maj", False)
        wizard = self._wizard(not_ocr | bban_numeric | rf | nothing | long_ref | text_ref)

        line = self._line(wizard, not_ocr)
        self.assertEqual((line.reference_type, line.reference), ("message", "F-2001"))
        self.assertEqual(line.status, "warning")
        self.assertIn("looks like an OCR number", line.warnings)
        # an OCR number is only sent to a Bankgiro/Plusgiro number
        line = self._line(wizard, bban_numeric)
        self.assertEqual((line.reference_type, line.reference), ("message", "1234567897"))
        line = self._line(wizard, rf)
        self.assertEqual((line.reference_type, line.reference), ("rf", "RF18539007547034"))
        line = self._line(wizard, nothing)
        self.assertEqual((line.reference_type, line.reference), ("message", nothing.name))
        line = self._line(wizard, long_ref)
        self.assertEqual(line.reference_type, "message")
        line = self._line(wizard, text_ref)
        self.assertEqual((line.reference_type, line.reference), ("message", "Faktura 4711 maj"))

        wizard.warnings_acknowledged = True
        batch = self.env["l10n_se.payment.export"].browse(wizard.action_generate()["res_id"])
        rows = {row["Egen anteckning"]: row for row in self._rows(batch)}
        self.assertEqual(rows[rf.name]["RF"], "RF18539007547034")
        self.assertEqual(rows[text_ref.name]["Meddelande"], "Faktura 4711 maj")
        self.assertEqual(rows[not_ocr.name]["Meddelande"], "F-2001")
        self.assertEqual(rows[not_ocr.name]["Fakturanummer"], "")
        self.assertEqual(rows[not_ocr.name]["OCR"], "")

    def test_user_chosen_reference_is_checked(self):
        bill = self._bill("5203-123 45 60", "1234567897", "F-3001")
        wizard = self._wizard(bill)
        line = wizard.line_ids
        line.reference_type = "ocr"
        self.assertEqual(line.status, "blocked")
        self.assertIn("Bankgiro or Plusgiro", line.problems)
        line.write({"reference_type": "invoice", "reference": "X" * 36})
        self.assertIn("more than 35", line.problems)
        line.write({"reference_type": "message", "reference": "Ert ordernr 17"})
        self.assertEqual(line.status, "ok")
        batch = self.env["l10n_se.payment.export"].browse(wizard.action_generate()["res_id"])
        self.assertEqual(self._rows(batch)[0]["Meddelande"], "Ert ordernr 17")

    def test_invoice_number_refused_for_giro(self):
        """SEB refused a file with "Fakturanummer" on a Bankgiro payment (2026-09-29)."""
        bill = self._bill("BG 555-1239", False, "F-3101")
        line = self._wizard(bill).line_ids
        self.assertEqual((line.reference_type, line.reference), ("message", "F-3101"))
        line.reference_type = "invoice"
        self.assertEqual(line.status, "blocked")
        self.assertIn("does not accept an invoice number", line.problems)
        # a bank account keeps the invoice number
        bban = self._bill("5203-123 45 60", False, "F-3102")
        line = self._wizard(bban).line_ids
        self.assertEqual((line.reference_type, line.reference), ("invoice", "F-3102"))

    def test_payee_accepts_only_messages(self):
        """SEB's payment form: some Bankgiro payees take only text messages, no OCR (2026-09-29)."""
        bill = self._bill("BG 555-1239", "1234567897", "F-3201", bank_vals={"l10n_se_ocr_refused": True})
        line = self._wizard(bill).line_ids
        # a valid OCR number on the bill is not sent; the invoice number goes as a message
        self.assertEqual((line.reference_type, line.reference), ("message", "F-3201"))
        self.assertEqual(line.status, "ok", line.warnings)
        line.reference_type = "ocr"
        self.assertEqual(line.status, "blocked")
        self.assertIn("accepts only text messages", line.problems)
        # a numeric reference that fails the OCR check is no warning for such a payee
        other = self._bill("BG 555-1239", "12345", "F-3202", bank_vals={"l10n_se_ocr_refused": True})
        self.assertEqual(self._wizard(other).line_ids.status, "ok")
        with self.assertRaises(ValidationError):
            other.partner_bank_id.l10n_se_ocr_required = True
        # the flag only means something for Bankgiro/Plusgiro: a bank account keeps its RF
        rf = self._bill("5203-123 45 60", "RF18 5390 0754 7034", "F-3203", bank_vals={"l10n_se_ocr_refused": True})
        self.assertEqual(self._wizard(rf).line_ids.reference_type, "rf")

    def test_bban_form_switch(self):
        """Should a test upload show that SEB wants clearing + account for every bank."""
        handelsbanken = self._bill("6789 123 456 789", ref="F-14001")
        swedbank = self._bill("8327-9, 12 345 678-2", ref="F-14002")
        with patch.object(seb_csv, "BBAN_FORM", "clearing_account"):
            batch = self._export(handelsbanken | swedbank)
        rows = {row["Egen anteckning"]: row for row in self._rows(batch)}
        self.assertEqual(rows[handelsbanken.name]["Till konto"], "6789123456789")
        self.assertEqual(rows[swedbank.name]["Till konto"], "83279123456782")
        batch.action_cancel()
        batch = self._export(handelsbanken | swedbank)  # default: MIG Annex 5
        rows = {row["Egen anteckning"]: row for row in self._rows(batch)}
        self.assertEqual(rows[handelsbanken.name]["Till konto"], "123456789")
        self.assertEqual(rows[swedbank.name]["Till konto"], "832790123456782")

    # --- Blocking and warnings -----------------------------------------------------------------

    def test_blocked_reasons(self):
        eur = self.env.ref("base.EUR")
        eur.active = True
        company_bank = self.env.company.partner_id.bank_ids[:1]
        cases = {
            "Foreign currency": self._bill(currency=eur),
            "not posted": self._bill(post=False),
            "Credit note": self._bill(move_type="in_refund"),
            "No bank account": self._bill(acc_number=False),
            "not trusted": self._bill(trusted=False),
            "no known Swedish account type": self._bill("55512347"),
            "Foreign IBAN": self._bill(DE_IBAN, ref="F-1"),
            "Bankgiro number BG 555-1238 has an invalid check digit": self._bill("BG 555-1238"),
            "Plusgiro number PG 12 34 56-7 has an invalid check digit": self._bill("PG 12 34 56-7"),
            "requires an OCR reference": self._bill(
                "BG 5551-2347", "12345", bank_vals={"l10n_se_ocr_required": True}
            ),
            "belongs to your own company": self._bill(),
            "Paid by direct debit": self._bill(),
            "is in the past": self._bill(),
            "more than the amount due": self._bill(),
            "must be positive": self._bill(),
        }
        cases["Paid by direct debit"].l10n_se_auto_debit = True
        paid = self._bill()
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=paid.ids
        ).create({"journal_id": self._journal("bank").id})._create_payments()
        cases["Nothing to pay"] = paid
        wizard = self._wizard(self.env["account.move"].union(*cases.values()))
        self._line(wizard, cases["belongs to your own company"]).partner_bank_id = company_bank
        self._line(wizard, cases["is in the past"]).payment_date = self.today - timedelta(days=1)
        self._line(wizard, cases["more than the amount due"]).amount = 1000.01
        self._line(wizard, cases["must be positive"]).amount = 0
        for reason, move in cases.items():
            with self.subTest(reason=reason):
                line = self._line(wizard, move)
                self.assertEqual(line.status, "blocked")
                self.assertIn(reason, line.problems)
                self.assertFalse(line.include)
        self.assertIn("Set the Swedish account type", self._line(wizard, cases["no known Swedish account type"]).problems)
        self.assertEqual(wizard.ready_count, 0)
        self.assertEqual(wizard.blocked_count, len(cases))
        with self.assertRaisesRegex(UserError, "No bill to export"):
            wizard.action_generate()
        # a blocked bill forced back in is refused by the server
        self._line(wizard, cases["not trusted"]).include = True
        with self.assertRaisesRegex(UserError, "cannot be exported"):
            wizard.action_generate()
        self.assertFalse(self.env["l10n_se.payment.export"].search([]))

    def test_credit_note_warning(self):
        partner = self._partner("Kreditbolaget AB")
        bill = self._bill(partner=partner, ref="F-5001", amount=1000.0)
        refund = self._bill(partner=partner, move_type="in_refund", acc_number=False, amount=200.0)
        wizard = self._wizard(bill | refund)
        self.assertEqual(self._line(wizard, refund).status, "blocked")
        line = self._line(wizard, bill)
        self.assertEqual(line.status, "warning")
        self.assertIn(refund.name, line.warnings)
        self.assertIn("not netted", line.warnings)
        self.assertTrue(wizard.has_warnings)
        with self.assertRaisesRegex(UserError, "I have read the warnings"):
            wizard.action_generate()
        wizard.warnings_acknowledged = True
        batch = self.env["l10n_se.payment.export"].browse(wizard.action_generate()["res_id"])
        self.assertEqual(batch.line_ids.move_id, bill)
        rows = self._rows(batch)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Belopp"], "1000.00")

    def test_partial_amount(self):
        bill = self._bill(amount=1000.0)
        wizard = self._wizard(bill)
        wizard.line_ids.amount = 400.0
        self.assertEqual(wizard.line_ids.status, "warning")
        self.assertIn("Partial payment", wizard.line_ids.warnings)
        wizard.warnings_acknowledged = True
        batch = self.env["l10n_se.payment.export"].browse(wizard.action_generate()["res_id"])
        self.assertEqual(self._rows(batch)[0]["Belopp"], "400.00")
        self.assertEqual(batch.line_ids.residual_at_export, 1000.0)
        self.assertEqual(bill.payment_state, "not_paid")

    def test_payment_dates(self):
        overdue = self._bill(due=self.today - timedelta(days=5))
        later = self._bill(due=self.today + timedelta(days=20))
        # due on a Sunday: paid on the last bank day before it, so the payment is not late
        sunday = self.today + timedelta(days=(6 - self.today.weekday()) % 7 + 14)
        on_sunday = self._bill(due=sunday)
        wizard = self._wizard(overdue | later | on_sunday)
        self.assertEqual(self._line(wizard, overdue).payment_date, self.today)
        self.assertEqual(
            self._line(wizard, later).payment_date,
            se_bank.previous_bank_day(self.today + timedelta(days=20)),
        )
        self.assertEqual(self._line(wizard, on_sunday).payment_date, se_bank.previous_bank_day(sunday))
        self.assertLess(self._line(wizard, on_sunday).payment_date, sunday)
        self.assertNotIn("not a bank day", self._line(wizard, on_sunday).warnings or "")
        saturday = self.today + timedelta(days=(5 - self.today.weekday()) % 7 or 7)
        wizard.write({"date_mode": "fixed", "payment_date": saturday})
        wizard._onchange_date_mode()
        for line in wizard.line_ids:
            self.assertEqual(line.payment_date, saturday)
            self.assertEqual(line.status, "warning")
            self.assertIn("not a bank day", line.warnings)
        # a Swedish bank holiday on a weekday
        year = self.today.year + 1
        midsummer_eve = next(d for d in se_bank.bank_holidays(year) if d.month == 6 and d.day > 18)
        self._line(wizard, later).payment_date = midsummer_eve
        self.assertIn("not a bank day", self._line(wizard, later).warnings)
        wizard.date_mode = "due"
        wizard._onchange_date_mode()
        self.assertEqual(self._line(wizard, overdue).payment_date, self.today)

    def test_without_selection_takes_the_bills_to_pay(self):
        open_bill = self._bill(ref="F-15001")
        exported = self._bill(ref="F-15002")
        self._bill(ref="F-15003", post=False)
        self._bill(ref="F-15004", move_type="in_refund")
        self._export(exported)
        wizard = self.env["l10n_se.payment.export.wizard"].create({})
        self.assertEqual(wizard.line_ids.move_id, open_bill)
        self.assertEqual(
            self.env["account.move"].search(
                [("id", "in", (open_bill | exported).ids), ("l10n_se_payment_exported", "=", False)]
            ),
            open_bill,
        )

    def test_fallback_to_the_suppliers_only_trusted_account(self):
        partner = self._partner()
        bank = self._bank(partner, "BG 5551-2347")
        bill = self._bill(acc_number=False, partner=partner, ref="F-6001")
        bill.partner_bank_id = False
        line = self._wizard(bill).line_ids
        self.assertEqual(line.partner_bank_id, bank)
        self.assertEqual(line.status, "warning")
        self.assertIn("only trusted account", line.warnings)
        self._bank(partner, "BG 555-1239")
        line = self._wizard(bill).line_ids
        self.assertFalse(line.partner_bank_id)
        self.assertIn("No bank account", line.problems)

    # --- Double payment ------------------------------------------------------------------------

    def test_double_export_is_prevented(self):
        bill, other = self._bill(ref="F-7001"), self._bill(ref="F-7002")
        batch = self._export(bill)
        line = self._wizard(bill).line_ids
        self.assertEqual(line.status, "blocked")
        self.assertIn(f"Already in SEB payment file {batch.name}", line.problems)

        # two exports opened before either is generated
        first, second = self._wizard(other), self._wizard(other)
        self.assertTrue(second.line_ids.include)
        first.action_generate()
        with self.assertRaisesRegex(UserError, "Already in SEB payment file"):
            second.action_generate()

        # the model refuses a second active line, and the database index backs it up
        with self.assertRaisesRegex(UserError, "Already in an active SEB payment file"):
            self.env["l10n_se.payment.export"].create(
                {
                    "journal_id": self.journal.id,
                    "line_ids": [
                        Command.create(
                            {
                                "move_id": bill.id,
                                "partner_bank_id": bill.partner_bank_id.id,
                                "amount": 1.0,
                                "payment_date": self.today,
                                "reference_type": "message",
                                "reference": "x",
                            }
                        )
                    ],
                }
            )
        line = batch.line_ids
        with self.assertRaises(psycopg2.IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            self.env.cr.execute(
                """INSERT INTO l10n_se_payment_export_line
                   (export_id, company_id, move_id, partner_bank_id, amount, payment_date,
                    reference_type, state)
                   VALUES (%s, %s, %s, %s, 1, %s, 'message', 'draft')""",
                [batch.id, batch.company_id.id, bill.id, line.partner_bank_id.id, self.today],
            )

    def test_cancel_releases_the_bills(self):
        bill, other = self._bill(ref="F-8001"), self._bill(ref="F-8002")
        batch = self._export(bill | other)
        batch.action_cancel()
        self.assertEqual(batch.state, "cancelled")
        self.assertEqual(set(batch.line_ids.mapped("state")), {"cancelled"})
        self.assertFalse(bill.l10n_se_payment_exported)
        self.assertIn("can be exported again", self._messages(bill))
        self.assertEqual(batch.amount_total, 2000.0)  # a cancelled file still shows what it held

        second = self._export(bill | other)
        self.assertEqual(bill.l10n_se_payment_export_id, second)
        self.assertNotEqual(second.name, batch.name)
        # one payment was rejected by the bank: cancel only that line
        second.line_ids.filtered(lambda line: line.move_id == bill).action_cancel()
        self.assertEqual(second.state, "exported")
        self.assertFalse(bill.l10n_se_payment_exported)
        self.assertEqual(other.l10n_se_payment_export_id, second)
        self.assertEqual(second.line_count, 1)
        third = self._export(bill)
        second.action_done()
        self.assertEqual(second.state, "done")
        self.assertEqual(second.line_ids.filtered(lambda line: line.move_id == other).state, "done")
        with self.assertRaises(UserError):
            second.action_cancel()
        with self.assertRaises(UserError):
            second.unlink()
        # a done file still holds its bills until the payment reaches them
        self.assertIn("Already in", self._wizard(other).line_ids.problems)
        self.assertTrue(other.l10n_se_payment_exported)
        with self.assertRaises(UserError):
            third.action_generate()  # already generated
        third.action_cancel()
        third.unlink()

    def test_exported_bill_cannot_be_reset_to_draft(self):
        bill = self._bill(ref="F-13001")
        batch = self._export(bill)
        with self.assertRaisesRegex(UserError, "Cancel its payment there"):
            bill.button_draft()
        batch.action_cancel()
        bill.button_draft()
        self.assertEqual(bill.state, "draft")

    def test_draft_batch(self):
        bill = self._bill(ref="F-9001")
        action = self._wizard(bill).action_save_draft()
        batch = self.env["l10n_se.payment.export"].browse(action["res_id"])
        self.assertEqual(batch.state, "draft")
        self.assertFalse(batch.attachment_id)
        self.assertTrue(bill.l10n_se_payment_exported)  # reserved
        with self.assertRaises(UserError):
            batch.action_download()
        # the bill changed after the draft was saved: generating re-checks it
        bill.partner_bank_id.allow_out_payment = False
        with self.assertRaisesRegex(UserError, "not trusted"):
            batch.action_generate()
        bill.partner_bank_id.allow_out_payment = True
        batch.action_generate()
        self.assertEqual(batch.state, "exported")
        self.assertEqual(len(self._rows(batch)), 1)

    def test_remainder_of_a_partial_payment_after_done(self):
        """A done line releases its bill once the paid amount has reached it; the rest can then
        be exported. Until then the bill stays held (the payment is not yet reconciled)."""
        bill = self._bill(amount=1000.0, ref="F-16001")
        wizard = self._wizard(bill)
        wizard.line_ids.amount = 400.0
        wizard.warnings_acknowledged = True
        batch = self.env["l10n_se.payment.export"].browse(wizard.action_generate()["res_id"])
        batch.action_done()
        line = batch.line_ids
        self.assertTrue(line.holds_bill)
        self.assertIn("Already in", self._wizard(bill).line_ids.problems)
        # the SEB payment reaches the bill (here: registered once the file is done)
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=bill.ids
        ).create({"journal_id": self.journal.id, "amount": 400.0})._create_payments()
        self.assertEqual(bill.amount_residual, 600.0)
        self.assertFalse(line.holds_bill)
        self.assertFalse(bill.l10n_se_payment_exported)
        rest = self._wizard(bill).line_ids
        self.assertEqual((rest.status, rest.amount), ("ok", 600.0))
        second = self._export(bill)
        self.assertEqual(second.line_ids.amount, 600.0)
        self.assertEqual(bill.l10n_se_payment_export_id, second)

    def test_register_payment_refused_while_the_file_may_be_signed(self):
        bill = self._bill(ref="F-17001")
        batch = self._export(bill)
        register = self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=bill.ids
        )
        with self.assertRaisesRegex(UserError, "mark the file as done first"):
            register.create({"journal_id": self.journal.id})._create_payments()
        batch.action_done()
        register.create({"journal_id": self.journal.id})._create_payments()
        self.assertIn(bill.payment_state, ("in_payment", "paid"))

    def test_bill_paid_otherwise_is_flagged_on_the_file(self):
        partner = self._partner("Kreditbolaget AB")
        bill = self._bill(partner=partner, ref="F-18001", amount=1000.0)
        batch = self._export(bill)
        self.assertFalse(batch.has_residual_short)
        refund = self._bill(partner=partner, move_type="in_refund", acc_number=False, amount=200.0)
        (bill | refund).line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        ).reconcile()
        self.assertEqual(bill.amount_residual, 800.0)
        self.assertTrue(batch.line_ids.residual_short)
        self.assertTrue(batch.has_residual_short)

    def test_bank_account_warnings(self):
        seb_bank = self.env["res.bank"].create({"name": "SEB", "bic": "ESSESESS"})
        swedbank = self.env["res.bank"].create({"name": "Swedbank", "bic": "SWEDSESS"})
        soft = self._bill("8327-9 123 456 789-0", ref="F-19001")
        unknown = self._bill("9990 1234567", ref="F-19002")
        mismatch = self._bill("5203-123 45 60", ref="F-19003", bank_vals={"bank_id": swedbank.id})
        matching = self._bill("5203-123 45 60", ref="F-19004", bank_vals={"bank_id": seb_bank.id})
        ocr_is_ref = self._bill("BG 555-1239", "1234567897", "1234567897")
        typo = self._bill("5203-123 45 67", ref="F-19006")
        wizard = self._wizard(soft | unknown | mismatch | matching | ocr_is_ref | typo)
        self.assertIn("does not match", self._line(wizard, soft).warnings)
        self.assertIn("is not in the Swedish banks' list", self._line(wizard, unknown).warnings)
        self.assertIn("its clearing number belongs to SEB", self._line(wizard, mismatch).warnings)
        self.assertEqual(self._line(wizard, matching).status, "ok")
        self.assertEqual(self._line(wizard, matching).clearing_bank, "SEB")
        self.assertIn("same as the supplier's invoice number", self._line(wizard, ocr_is_ref).warnings)
        self.assertIn("invalid check digit for clearing number 5203", self._line(wizard, typo).problems)
        self.assertEqual(self._line(wizard, typo).status, "blocked")

    def test_summaries_above_the_bills(self):
        """Blocked reasons and the warnings to acknowledge are listed above the bills: as the last
        columns of the list they were out of sight in the dialog."""
        partner = self._partner("Varning & <Co> AB")
        warned = self._bill(amount=1000.0, ref="F-22001", partner=partner)
        blocked = self._bill(trusted=False, ref="F-22002")
        ready = self._bill(ref="F-22003")
        wizard = self._wizard(warned | blocked | ready)
        self._line(wizard, warned).amount = 400.0
        self.assertIn(warned.name, wizard.warning_summary)
        self.assertIn("Partial payment", wizard.warning_summary)
        self.assertIn("Varning &amp; &lt;Co&gt; AB", wizard.warning_summary)  # escaped
        self.assertNotIn(ready.name, wizard.warning_summary)
        self.assertNotIn(blocked.name, wizard.warning_summary)
        self.assertIn(blocked.name, wizard.blocked_summary)
        self.assertIn("not trusted", wizard.blocked_summary)
        self.assertNotIn(warned.name, wizard.blocked_summary)
        # an unticked bill is not paid: its warnings need no acknowledgement and are not listed
        self._line(wizard, warned).include = False
        self.assertFalse(wizard.has_warnings)
        self.assertFalse(wizard.warning_summary)
        # a bill without blocked reasons leaves no blocked list
        self.assertFalse(self._wizard(ready).blocked_summary)
        # a draft bill is named "/": the list shows its display name instead
        draft = self._bill(ref="F-22004", post=False)
        summary = self._wizard(draft).blocked_summary
        self.assertIn(draft.display_name, summary)
        self.assertNotIn("<strong>/</strong>", summary)

    def test_action_opens_a_wide_dialog(self):
        action = self.env.ref("l10n_se_payment_file_seb_csv.action_l10n_se_payment_export_wizard")
        self.assertEqual(action.target, "new")
        self.assertIn("'dialog_size': 'extra-large'", action.context)

    # --- The wizard in the form view -----------------------------------------------------------

    def _form(self, moves):
        return Form(
            self.env["l10n_se.payment.export.wizard"].with_context(
                active_model="account.move", active_ids=moves.ids
            )
        )

    def test_wizard_through_the_form(self):
        """Open, untick a bill, change the date mode, acknowledge, save and generate, as the web
        client does (read-only fields are only saved with force_save)."""
        partner = self._partner()
        self._bank(partner, "BG 5551-2347")
        fallback = self._bill(acc_number=False, partner=partner, ref="F-20001")
        fallback.partner_bank_id = False
        left_out = self._bill(ref="F-20002")
        blocked = self._bill(trusted=False, ref="F-20003")
        form = self._form(fallback | left_out | blocked)
        self.assertEqual(len(form.line_ids), 3)
        index = {vals["move_id"]: i for i, vals in enumerate(form.line_ids._records)}
        with form.line_ids.edit(index[left_out.id]) as line:
            line.include = False
        form.date_mode = "fixed"
        form.payment_date = se_bank.previous_bank_day(self.today + timedelta(days=12))
        # the user's untick survives the date change; the blocked bill stays out
        self.assertEqual(
            {vals["move_id"]: vals["include"] for vals in form.line_ids._records},
            {fallback.id: True, left_out.id: False, blocked.id: False},
        )
        self.assertEqual(form.ready_count, 1)
        self.assertTrue(form.has_warnings)  # the supplier's only trusted account is used
        form.warnings_acknowledged = True
        wizard = form.save()
        self.assertEqual(wizard.line_ids.move_id, fallback | left_out | blocked)
        self.assertTrue(self._line(wizard, fallback).bank_from_partner)
        self.assertIn("only trusted account", self._line(wizard, fallback).warnings)
        batch = self.env["l10n_se.payment.export"].browse(wizard.action_generate()["res_id"])
        self.assertEqual(batch.line_ids.move_id, fallback)
        self.assertEqual(batch.line_ids.payment_date, wizard.payment_date)
        self.assertFalse(left_out.l10n_se_payment_exported)

    def test_acknowledged_warnings_go_stale(self):
        bill = self._bill(amount=1000.0, ref="F-21001")
        form = self._form(bill)
        with form.line_ids.edit(0) as line:
            line.amount = 400.0  # partial payment: a warning
        form.warnings_acknowledged = True
        with form.line_ids.edit(0) as line:
            line.amount = 300.0  # the warning changes: the acknowledgement is taken back
        self.assertFalse(form.warnings_acknowledged)
        form.warnings_acknowledged = True
        wizard = form.save()
        self.assertTrue(wizard.warnings_acknowledged)
        # changed on the server after the acknowledgement: refused at generation
        wizard.line_ids.amount = 200.0
        with self.assertRaisesRegex(UserError, "warnings changed"):
            wizard.action_generate()
        wizard.warnings_acknowledged = False
        wizard.warnings_acknowledged = True
        batch = self.env["l10n_se.payment.export"].browse(wizard.action_generate()["res_id"])
        self.assertEqual(batch.line_ids.amount, 200.0)

    # --- Companies and access ------------------------------------------------------------------

    def test_multi_company(self):
        company_2 = self._create_company("Bolag 2")
        journal_2 = self._seb_journal(company_2, "5204-765 43 27", "SEBZ")
        bill_1 = self._bill(ref="F-10001")
        bill_2 = self._bill(ref="F-10002", company=company_2)
        wizard = self._wizard(bill_1 | bill_2)
        self.assertEqual(wizard.company_id, self.env.company)
        self.assertEqual(self._line(wizard, bill_1).status, "ok")
        self.assertIn("belongs to Bolag 2", self._line(wizard, bill_2).problems)

        batch_2 = self._export(bill_2)
        self.assertEqual(batch_2.company_id, company_2)
        self.assertEqual(batch_2.journal_id, journal_2)
        self.assertEqual(self._rows(batch_2)[0]["Från konto"], "52047654327")

        user = new_test_user(
            self.env,
            login="seb_company_1",
            groups="base.group_user,account.group_account_invoice",
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.company.ids)],
        )
        batch_1 = self._export(bill_1)
        Export = self.env["l10n_se.payment.export"].with_user(user)
        self.assertEqual(Export.search([("id", "in", (batch_1 | batch_2).ids)]), batch_1)
        with self.assertRaises(AccessError):
            batch_2.with_user(user).read(["name"])

    def test_access_rights(self):
        company = self.env.company
        billing = new_test_user(
            self.env,
            login="seb_billing",
            groups="base.group_user,account.group_account_invoice",
            company_id=company.id,
            company_ids=[Command.set(company.ids)],
        )
        readonly = new_test_user(
            self.env,
            login="seb_readonly",
            groups="base.group_user,account.group_account_readonly",
            company_id=company.id,
            company_ids=[Command.set(company.ids)],
        )
        internal = new_test_user(
            self.env,
            login="seb_internal",
            groups="base.group_user",
            company_id=company.id,
            company_ids=[Command.set(company.ids)],
        )
        bill, other = self._bill(ref="F-11001"), self._bill(ref="F-11002")
        # Billing (group_account_invoice) and above may export, cancel and mark done
        batch = self._export(bill, user=billing)
        self.assertEqual(batch.state, "exported")
        self.assertEqual(batch.create_uid, billing)
        batch.with_user(billing).action_done()
        with self.assertRaises(AccessError):
            batch.with_user(billing).unlink()
        draft = self.env["l10n_se.payment.export"].browse(
            self._wizard(other, user=billing).action_save_draft()["res_id"]
        )
        # read-only accountants can look but not act
        self.assertEqual(draft.with_user(readonly).state, "draft")
        with self.assertRaises(AccessError):
            draft.with_user(readonly).action_generate()
        with self.assertRaises(AccessError):
            draft.with_user(readonly).action_cancel()
        with self.assertRaises(AccessError):
            self._wizard(other, user=readonly)
        # users without accounting rights see nothing
        with self.assertRaises(AccessError):
            draft.with_user(internal).read(["name"])
        action = self.env.ref("l10n_se_payment_file_seb_csv.action_l10n_se_payment_export_wizard")
        self.assertEqual(action.group_ids, self.env.ref("account.group_account_invoice"))
        # the recorded values and the state can only be changed by the file's buttons
        with self.assertRaisesRegex(UserError, "set by its buttons only"):
            batch.with_user(billing).write({"state": "draft"})
        with self.assertRaisesRegex(UserError, "set by its buttons only"):
            batch.line_ids.with_user(billing).write({"to_account": "5551239"})
        with self.assertRaisesRegex(UserError, "set by its buttons only"):
            draft.line_ids.with_user(billing).write({"amount": 1.0})
        with self.assertRaisesRegex(UserError, "only be changed on a draft file"):
            batch.with_user(billing).write({"journal_id": self.journal.id})
        with self.assertRaisesRegex(UserError, "set by its buttons only"):
            self.env["l10n_se.payment.export"].with_user(billing).create(
                {"journal_id": self.journal.id, "state": "exported"}
            )
        with self.assertRaisesRegex(UserError, "only be added to a draft"):
            self.env["l10n_se.payment.export.line"].with_user(billing).create(
                {
                    "export_id": batch.id,
                    "move_id": other.id,
                    "partner_bank_id": other.partner_bank_id.id,
                    "amount": 1.0,
                    "payment_date": self.today,
                    "reference_type": "message",
                    "reference": "x",
                }
            )
        draft.with_user(billing).write({"journal_id": self.journal.id})  # a draft may change
        draft.with_user(billing).action_cancel()
        self.assertEqual(draft.state, "cancelled")

    # --- Translation ---------------------------------------------------------------------------

    def test_swedish_translation(self):
        self.env["res.lang"]._activate_lang("sv_SE")
        self.env["ir.module.module"]._load_module_terms(
            ["l10n_se_payment_file_seb_csv"], ["sv_SE"], overwrite=True
        )
        bill = self._bill("BG 555-1239", ref="F-12001", trusted=False)
        line = self._wizard(bill).with_context(lang="sv_SE").line_ids
        line.invalidate_recordset(["problems"])
        self.assertIn("inte betrott", line.problems)
        field = self.env["ir.model.fields"]._get("account.journal", "l10n_se_seb_csv_export")
        self.assertEqual(field.with_context(lang="sv_SE").field_description, "SEB CSV-export")
        labels = dict(
            self.env["l10n_se.payment.export"]
            .with_context(lang="sv_SE")
            .fields_get(["state"])["state"]["selection"]
        )
        self.assertEqual(labels["exported"], "Exporterad")
