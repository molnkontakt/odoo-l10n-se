import re

from odoo import models

from .res_partner_bank import luhn_valid


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    def _get_communication(self):
        communication_type, communication = super()._get_communication()
        move = self.move_id
        ref = (move.payment_reference or "").replace(" ", "")
        if (
            communication_type == "normal"
            and move.is_purchase_document()
            and re.fullmatch(r"\d{2,25}", ref)
            and luhn_valid(ref)
        ):
            return "ocr", ref
        return communication_type, communication
