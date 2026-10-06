from odoo import api, fields, models

from ..lib import bolagsverket as bv


class AccountMove(models.Model):
    _inherit = "account.move"

    l10n_se_bv_warning = fields.Char(compute="_compute_l10n_se_bv_warning")

    @api.depends("partner_id", "move_type", "state", "payment_state",
                 "partner_id.commercial_partner_id.l10n_se_bv_status",
                 "partner_id.commercial_partner_id.l10n_se_bv_name_mismatch")
    def _compute_l10n_se_bv_warning(self):
        for move in self:
            move.l10n_se_bv_warning = move._l10n_se_bv_warning_text()

    def _l10n_se_bv_warning_text(self):
        """Only on invoices and bills that still matter: not cancelled and not settled."""
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        if not partner or not self.is_invoice(include_receipts=True) or self.state == "cancel" \
                or self.payment_state in ("paid", "reversed", "in_payment"):
            return False
        env = self.env
        if partner.l10n_se_bv_status in bv.BAD_STATUSES:
            label = dict(partner._fields["l10n_se_bv_status"]._description_selection(env))[partner.l10n_se_bv_status]
            text = env._("Bolagsverket: %(name)s – %(status)s%(note)s.", name=partner.name, status=label,
                         note=f" ({partner.l10n_se_bv_status_note})" if partner.l10n_se_bv_status_note else "")
            if self.is_purchase_document(include_receipts=True):
                return text + " " + env._("Check before paying – the bank account may no longer be the company's.")
            return text + " " + env._("Check how the receivable is followed up (e.g. claim in the bankruptcy).")
        if partner.l10n_se_bv_name_mismatch and self.is_purchase_document(include_receipts=True):
            return env._("Bolagsverket: the organisation number on %(name)s belongs to %(registered)s. Check the "
                         "supplier before paying.", name=partner.name, registered=partner.l10n_se_bv_name)
        return False
