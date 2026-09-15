from odoo import api, models


def luhn_checksum(number_str):
    """Calculate Luhn (modulus 10) check digit for a numeric string."""
    digits = [int(d) for d in number_str]
    odd_sum = 0
    even_sum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            doubled = d * 2
            odd_sum += doubled - 9 if doubled > 9 else doubled
        else:
            even_sum += d
    total = odd_sum + even_sum
    return (10 - (total % 10)) % 10


def make_ocr(invoice_number):
    """Generate OCR reference from invoice number.

    Extracts the numeric sequence from the invoice name (e.g. INV/2026/0001 -> 202600001),
    then appends a Luhn check digit.
    """
    # Extract digits from invoice number
    digits = "".join(c for c in invoice_number if c.isdigit())
    if not digits:
        return False
    check = luhn_checksum(digits)
    return f"{digits}{check}"


class AccountMove(models.Model):
    _inherit = "account.move"

    def _compute_payment_reference(self):
        """Override to set OCR as payment reference for Swedish customer invoices."""
        for move in self:
            if (move.move_type in ("out_invoice", "out_refund")
                    and move.name and move.name != "/"
                    and not move.payment_reference):
                ocr = make_ocr(move.name)
                if ocr:
                    move.payment_reference = ocr
                    continue
            super(AccountMove, move)._compute_payment_reference()

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        return moves

    def action_post(self):
        """Set OCR payment reference after posting (when name is assigned)."""
        res = super().action_post()
        for move in self:
            if (move.move_type in ("out_invoice", "out_refund")
                    and move.name and move.name != "/"):
                ocr = make_ocr(move.name)
                if ocr:
                    move.payment_reference = ocr
        return res
