from odoo import models
from odoo.exceptions import UserError


class AccountPaymentRegister(models.TransientModel):
    _inherit = "account.payment.register"

    def _create_payments(self):
        """Refuse to pay a bill whose SEB payment file may still be signed: the bill would be paid
        twice. Reconciling the bank statement line against the bill is not affected."""
        pending = self.line_ids.move_id._l10n_se_seb_pending_lines()
        if pending:
            raise UserError(
                self.env._(
                    "%(bills)s is in SEB payment file %(batch)s, which may still be signed in the "
                    "internet bank. If SEB has paid it, mark the file as done first; if the payment "
                    "will not be signed, cancel its line in the file.",
                    bills=", ".join(pending.move_id.mapped("display_name")),
                    batch=", ".join(pending.export_id.mapped("name")),
                )
            )
        return super()._create_payments()
