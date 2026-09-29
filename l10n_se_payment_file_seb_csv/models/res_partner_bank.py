from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResPartnerBank(models.Model):
    _inherit = "res.partner.bank"

    l10n_se_ocr_required = fields.Boolean(
        string="Payee requires OCR",
        help="This Bankgiro or Plusgiro number only accepts payments with a valid OCR "
        "reference. The SEB CSV export refuses bills to it that have no valid OCR number in "
        "their payment reference.",
    )
    # Some Bankgiro payees take only text messages; SEB's payment form says so ("tillåter bara
    # textmeddelanden, inte OCR", 2026-09-29), but an uploaded file gives no warning in advance.
    l10n_se_ocr_refused = fields.Boolean(
        string="Payee accepts only messages",
        help="This Bankgiro or Plusgiro number takes only text messages, no OCR or RF "
        "reference. The SEB CSV export sends the supplier's invoice number as a message, even "
        "when the bill has a valid OCR number.",
    )

    @api.constrains("l10n_se_ocr_required", "l10n_se_ocr_refused")
    def _check_l10n_se_ocr_rule(self):
        for bank in self:
            if bank.l10n_se_ocr_required and bank.l10n_se_ocr_refused:
                raise ValidationError(
                    self.env._(
                        "A payee cannot both require OCR and accept only messages (%(account)s).",
                        account=bank.acc_number,
                    )
                )
