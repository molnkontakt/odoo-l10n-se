from odoo import fields, models


class ResPartnerBank(models.Model):
    _inherit = "res.partner.bank"

    l10n_se_ocr_required = fields.Boolean(
        string="Payee requires OCR",
        help="This Bankgiro or Plusgiro number only accepts payments with a valid OCR "
        "reference. The SEB CSV export refuses bills to it that have no valid OCR number in "
        "their payment reference.",
    )
