from odoo import fields, models


class AccountPaymentMode(models.Model):
    _inherit = "account.payment.mode"

    l10n_se_bank_profile = fields.Selection(
        [("seb", "SEB")],
        string="Swedish bank profile",
        help="Writes Swedish domestic payments (Bankgiro, Plusgiro, bank account, OCR) the way "
        "the bank's ISO 20022 guide requires. Empty = OCA's standard SEPA file.",
    )
    l10n_se_customer_id = fields.Char(
        string="Customer id at the bank",
        help="Customer id from the bank's file agreement (SEB: 14 characters). Written as "
        "initiating party and debtor with SchmeNm/Cd = BANK.",
    )
