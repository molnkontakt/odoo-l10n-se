import re

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


def _ocr(number):
    """The invoice number's digits and a modulus 10 check digit, as l10n_se_ocr builds it."""
    digits = re.sub(r"\D", "", number)
    for check in "0123456789":
        total = 0
        for i, ch in enumerate(reversed(digits + check)):
            d = int(ch) * (2 if i % 2 else 1)
            total += d - 9 if d > 9 else d
        if total % 10 == 0:
            return digits + check
    raise AssertionError(number)


@tagged("post_install", "-at_install")
class TestCandidatesFromRef(TransactionCase):
    def test_every_candidate_is_a_number_and_a_year(self):
        """Two year-prefixed variants yielded a bare number: 'too many values to unpack'."""
        wizard = self.env["account.bankgirot.import.wizard"]
        for ref in ("203100425", "20310042", "12345", "INV/2031/0042", "7", "", False):
            for candidate in wizard._candidates_from_ref(ref):
                self.assertIsInstance(candidate, tuple, ref)
                self.assertEqual(len(candidate), 2, ref)

    def test_year_variants_keep_the_whole_sequence(self):
        """Cutting a digit off the sequence gives another invoice of the same year: the OCR of
        .../2031/0042 must never be tried as .../2031/0004."""
        wizard = self.env["account.bankgirot.import.wizard"]
        # an OCR number: the last digit is the check digit
        self.assertEqual(
            list(wizard._candidates_from_ref("203100425")),
            [
                ("203100425", None),
                ("20310042", None),
                ("2031004", None),
                ("0042", "2031"),
                ("42", "2031"),
            ],
        )
        # a typed invoice number has no check digit (20310420 fails it): the whole sequence
        self.assertIn(("0420", "2031"), list(wizard._candidates_from_ref("20310420")))
        self.assertNotIn(("042", "2031"), list(wizard._candidates_from_ref("20310420")))


@tagged("post_install", "-at_install")
class TestFindInvoice(TransactionCase):
    """A reference that names an invoice exactly never ends on another invoice (2026-09-28: the OCR
    of a paid invoice ended on an open invoice of the same year, a re-imported file set its
    member on the bank line)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.wizard = cls.env["account.bankgirot.import.wizard"]
        cls.open_inv = cls._invoice("Medlem Ett")
        cls.credited = cls._invoice("Medlem Två")
        cls.credited._reverse_moves(cancel=True)

    @classmethod
    def _invoice(cls, name):
        partner = cls.env["res.partner"].create({"name": name})
        move = cls.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": partner.id,
                "invoice_date": fields.Date.today(),
                "invoice_line_ids": [
                    Command.create({"name": "Avgift", "quantity": 1, "price_unit": 1000.0, "tax_ids": []})
                ],
            }
        )
        move.action_post()
        move.payment_reference = _ocr(move.name)
        return move

    def _find(self, ref):
        return self.wizard._find_invoice(ref, 1000.0, self.wizard._build_invoice_index())

    def test_open_invoice_by_ocr_name_and_digits(self):
        self.assertEqual(self._find(self.open_inv.payment_reference), self.open_inv)
        self.assertEqual(self._find(self.open_inv.name), self.open_inv)
        self.assertEqual(self._find(re.sub(r"\D", "", self.open_inv.name)), self.open_inv)

    def test_invoice_no_longer_open_is_reported_not_replaced(self):
        self.assertEqual(self.credited.payment_state, "reversed")
        for ref in (
            self.credited.payment_reference,
            self.credited.name,
            re.sub(r"\D", "", self.credited.name),
        ):
            self.assertFalse(self._find(ref), ref)
            self.assertEqual(self.wizard._find_settled_invoice(ref), self.credited, ref)
        self.assertEqual(self.wizard._settled_label(self.credited), "krediterad")

    def test_open_invoice_is_not_settled(self):
        self.assertFalse(self.wizard._find_settled_invoice(self.open_inv.payment_reference))
        self.assertFalse(self.wizard._find_settled_invoice("okänd referens 999"))
