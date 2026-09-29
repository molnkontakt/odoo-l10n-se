from odoo import models
from odoo.addons.l10n_se_bank_account.lib import se_bank


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    def _get_communication(self):
        communication_type, communication = super()._get_communication()
        move = self.move_id
        ref = se_bank.compact_reference(move.payment_reference)
        # the account the payment line will use (OCA: the bill's, else the supplier's first)
        bank = move.partner_bank_id or move.commercial_partner_id.bank_ids[:1]
        if bank.l10n_se_ocr_refused and move.is_purchase_document() and move.ref:
            # a payee that takes only messages gets the supplier's invoice number, as in the SEB CSV,
            # followed by what OCA adds (credit notes, partial payments)
            direct = move._get_payment_order_communication_direct() or ""
            rest = communication[len(direct):] if communication and direct and communication.startswith(direct) else ""
            return "normal", move.ref + rest
        if (
            communication_type == "normal"
            and move.is_purchase_document()
            and se_bank.ocr_valid(ref)
            and not bank.l10n_se_ocr_refused  # a payee that takes only messages
        ):
            return "ocr", ref
        return communication_type, communication
