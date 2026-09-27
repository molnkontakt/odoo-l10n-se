from odoo import models
from odoo.addons.l10n_se_bank_account.lib import se_bank


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    def _get_communication(self):
        communication_type, communication = super()._get_communication()
        move = self.move_id
        ref = se_bank.compact_reference(move.payment_reference)
        if communication_type == "normal" and move.is_purchase_document() and se_bank.ocr_valid(ref):
            return "ocr", ref
        return communication_type, communication
