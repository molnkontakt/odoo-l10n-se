from odoo import fields, models


class AccountPaymentLine(models.Model):
    _inherit = "account.payment.line"

    communication_type = fields.Selection(
        selection_add=[("ocr", "OCR")], ondelete={"ocr": "set normal"},
    )
