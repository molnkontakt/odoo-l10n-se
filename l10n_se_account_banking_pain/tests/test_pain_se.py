import base64

from lxml import etree

from odoo import fields
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
            {"acc_number": "SE1150000000054401060156", "partner_id": cls.company.partner_id.id, "bank_id": bank.id}
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

    def test_account_type_guess(self):
        cases = {
            "BG 843-6008": "bankgiro",
            "123-4567": "bankgiro",
            "PG 12 34 56-7": "plusgiro",
            "8327-9 123 456 789-0": "bban",
            "SE6780000832791234567890": "iban",
        }
        for acc, expected in cases.items():
            bank = self.env["res.partner.bank"].create(
                {"acc_number": acc, "partner_id": self.company.partner_id.id}
            )
            self.assertEqual(bank.l10n_se_account_type, expected, acc)

    def test_payment_file(self):
        moves = self.env["account.move"]
        for name, acc, ref in [
            ("BG OCR", "BG 843-6008", "1234567897"),
            ("BG text", "BG 123-4567", "Faktura 4711"),
            ("PG", "PG 12 34 56-7", "Faktura 88"),
            ("BBAN", "8327-9 123 456 789-0", "Faktura 99"),
            ("IBAN", "SE6780000832791234567890", "Faktura 77"),
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
        self.assertEqual(bg.findtext(".//p:CdtrAcct//p:Othr/p:Id", namespaces=NS), "8436008")
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
        self.assertEqual(bban.findtext(".//p:CdtrAcct//p:Othr/p:Id", namespaces=NS), "832791234567890")
        self.assertEqual(bban.findtext(".//p:CdtrAgt//p:MmbId", namespaces=NS), "8327")
        self.assertEqual(txs["IBAN"].findtext(".//p:CdtrAcct//p:IBAN", namespaces=NS), "SE6780000832791234567890")
        self.assertEqual(txs["IBAN"].findtext(".//p:CdtrAgt//p:BIC", namespaces=NS), "SWEDSESS")
