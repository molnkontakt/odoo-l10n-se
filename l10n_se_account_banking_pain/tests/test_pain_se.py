# All account numbers are invented; they only satisfy the check digits.
import base64

from lxml import etree

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

NS = {"p": "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"}


@tagged("post_install", "-at_install")
class TestPainSE(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.sek = cls.env.ref("base.SEK")
        cls.sek.active = True
        bank = cls.env["res.bank"].create({"name": "SEB", "bic": "ESSESESS"})
        cls.journal = cls.env["account.journal"].create(
            {"name": "SEB test", "type": "bank", "code": "SEBT", "currency_id": cls.sek.id}
        )
        cls.journal.bank_account_id = cls.env["res.partner.bank"].create(
            {"acc_number": "SE3150000000052031234560", "partner_id": cls.company.partner_id.id, "bank_id": bank.id}
        )
        method = cls.env["account.payment.method"].search(
            [("code", "=", "sepa_credit_transfer"), ("payment_type", "=", "outbound")], limit=1
        )
        cls.mode = cls.env["account.payment.mode"].create(
            {
                "name": "SEB SE",
                "payment_method_id": method.id,
                "bank_account_link": "fixed",
                "fixed_journal_id": cls.journal.id,
                "l10n_se_bank_profile": "seb",
                "l10n_se_customer_id": "55667755110004",
            }
        )
        cls.expense = cls.env["account.account"].search(
            [("account_type", "=", "expense"), ("company_ids", "in", cls.company.id)], limit=1
        )

    def _bill(self, name, acc_number, payment_reference):
        partner = self.env["res.partner"].create({"name": name, "is_company": True})
        bank = self.env["res.partner.bank"].create(
            {"acc_number": acc_number, "partner_id": partner.id, "allow_out_payment": True}
        )
        move = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": partner.id,
                "invoice_date": fields.Date.today(),
                "payment_reference": payment_reference,
                "partner_bank_id": bank.id,
                "currency_id": self.sek.id,
                "invoice_line_ids": [
                    (0, 0, {"name": "x", "quantity": 1, "price_unit": 100.0,
                            "account_id": self.expense.id, "tax_ids": [(6, 0, [])]})
                ],
            }
        )
        move.action_post()
        return move, bank

    def test_payment_file(self):
        moves = self.env["account.move"]
        for name, acc, ref in [
            ("BG OCR", "BG 555-1239", "1234567897"),
            ("BG text", "BG 5551-2347", "Faktura 4711"),
            ("PG", "PG 12 34 56-6", "Faktura 88"),
            ("BBAN", "8327-9 123 456 789-7", "Faktura 99"),
            ("Swedbank short", "8327-9, 12 345 678-2", "Faktura 98"),
            ("Handelsbanken", "6789 123 456 789", "Faktura 97"),
            ("Danske 918x", "9180-1234567897", "Faktura 96"),
            ("IBAN", "SE7280000832791234567897", "Faktura 77"),
        ]:
            moves |= self._bill(name, acc, ref)[0]
        order = self.env["account.payment.order"].create(
            {"payment_mode_id": self.mode.id, "payment_type": "outbound"}
        )
        moves.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        ).create_payment_line_from_move_line(order)
        order.draft2open()
        order.open2generated()  # validates against the pain.001.001.03 XSD
        att = self.env["ir.attachment"].search(
            [("res_model", "=", "account.payment.order"), ("res_id", "=", order.id)], limit=1
        )
        root = etree.fromstring(base64.b64decode(att.datas))
        self.assertEqual(root.findtext(".//p:PmtTpInf/p:SvcLvl/p:Prtry", namespaces=NS), "MPNS")
        self.assertIsNone(root.find(".//p:BtchBookg", NS))
        self.assertEqual(root.findtext(".//p:InitgPty//p:SchmeNm/p:Cd", namespaces=NS), "BANK")
        txs = {tx.findtext("p:Cdtr/p:Nm", namespaces=NS): tx for tx in root.iterfind(".//p:CdtTrfTxInf", NS)}
        bg = txs["BG OCR"]
        self.assertEqual(bg.findtext(".//p:CdtrAgt//p:MmbId", namespaces=NS), "9900")
        self.assertEqual(bg.findtext(".//p:CdtrAcct//p:Prtry", namespaces=NS), "BGNR")
        self.assertEqual(bg.findtext(".//p:CdtrAcct//p:Othr/p:Id", namespaces=NS), "5551239")
        self.assertEqual(bg.findtext(".//p:CdtrRefInf/p:Ref", namespaces=NS), "1234567897")
        self.assertEqual(bg.findtext(".//p:CdtrRefInf//p:Cd", namespaces=NS), "SCOR")
        self.assertEqual(bg.findtext(".//p:Strd/p:RfrdDocAmt/p:RmtdAmt", namespaces=NS), "100.00")
        self.assertEqual(bg.findtext(".//p:Cdtr/p:PstlAdr/p:Ctry", namespaces=NS), "SE")
        self.assertIsNone(root.find(".//p:Dbtr/p:PstlAdr", NS))
        self.assertEqual(txs["BG text"].findtext(".//p:Ustrd", namespaces=NS), "Faktura 4711")
        pg = txs["PG"]
        self.assertEqual(pg.findtext(".//p:CdtrAgt//p:MmbId", namespaces=NS), "9960")
        self.assertEqual(pg.findtext(".//p:CdtrAcct//p:SchmeNm/p:Cd", namespaces=NS), "BBAN")
        bban = txs["BBAN"]
        self.assertEqual(bban.findtext(".//p:CdtrAcct//p:Othr/p:Id", namespaces=NS), "832791234567897")
        self.assertEqual(bban.findtext(".//p:CdtrAgt//p:MmbId", namespaces=NS), "8327")
        # MIG Annex 5: Swedbank 8-series zero-padded to 15 digits, Handelsbanken and Danske Bank
        # 9180-9189 without the clearing number (which still identifies the creditor agent)
        for name, account, clearing in [
            ("Swedbank short", "832790123456782", "8327"),
            ("Handelsbanken", "123456789", "6789"),
            ("Danske 918x", "1234567897", "9180"),
        ]:
            tx = txs[name]
            self.assertEqual(tx.findtext(".//p:CdtrAcct//p:Othr/p:Id", namespaces=NS), account, name)
            self.assertEqual(tx.findtext(".//p:CdtrAcct//p:SchmeNm/p:Cd", namespaces=NS), "BBAN", name)
            self.assertEqual(tx.findtext(".//p:CdtrAgt//p:MmbId", namespaces=NS), clearing, name)
        self.assertEqual(txs["IBAN"].findtext(".//p:CdtrAcct//p:IBAN", namespaces=NS), "SE7280000832791234567897")
        self.assertEqual(txs["IBAN"].findtext(".//p:CdtrAgt//p:BIC", namespaces=NS), "SWEDSESS")

    def _order(self, moves):
        order = self.env["account.payment.order"].create(
            {"payment_mode_id": self.mode.id, "payment_type": "outbound"}
        )
        moves.line_ids.filtered(
            lambda line: line.account_id.account_type == "liability_payable"
        ).create_payment_line_from_move_line(order)
        order.draft2open()
        return order

    def test_invalid_account_number_is_refused(self):
        for acc, text in [
            ("BG 555-1238", "invalid check digit"),
            ("PG 12 34 56-7", "invalid check digit"),
            ("6789 12345678", "expects 9 digits"),
        ]:
            with self.subTest(acc=acc):
                order = self._order(self._bill(f"Refused {acc}", acc, "Faktura 1")[0])
                with self.assertRaisesRegex(UserError, text):
                    order.open2generated()

    def test_unknown_account_type_is_refused(self):
        move, bank = self._bill("Unknown type", "55512347", "Faktura 2")
        self.assertEqual(bank.l10n_se_account_type, "other")
        order = self._order(move)
        with self.assertRaisesRegex(UserError, "no known Swedish account type"):
            order.open2generated()
        # set by hand, the same number is paid as Plusgiro
        bank.l10n_se_account_type = "plusgiro"
        order.open2generated()

    def test_swedish_translation(self):
        self.env["res.lang"]._activate_lang("sv_SE")
        self.env["ir.module.module"]._load_module_terms(
            ["l10n_se_bank_account", "l10n_se_account_banking_pain"], ["sv_SE"], overwrite=True
        )
        mode = self.mode.with_context(lang="sv_SE")
        self.assertEqual(mode.fields_get(["l10n_se_bank_profile"])["l10n_se_bank_profile"]["string"], "Svensk bankprofil")
        move = self._bill("Refused sv", "BG 555-1238", "Faktura 3")[0]
        order = self._order(move).with_context(lang="sv_SE")
        with self.assertRaisesRegex(UserError, "Bankgironumret BG 555-1238 har ogiltig kontrollsiffra"):
            order.open2generated()
