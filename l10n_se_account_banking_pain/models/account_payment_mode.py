from odoo import fields, models


class AccountPaymentMode(models.Model):
    _inherit = "account.payment.mode"

    l10n_se_bank_profile = fields.Selection(
        [("seb", "SEB")],
        string="Svensk bankprofil",
        help="Skriver svenska inrikes betalningar (bankgiro, plusgiro, bankkonto, OCR) enligt "
        "bankens ISO 20022-anvisning. Tomt = OCA:s vanliga SEPA-fil.",
    )
    l10n_se_customer_id = fields.Char(
        string="Kund-id i banken",
        help="Kund-id enligt bankens filavtal (SEB: 14 tecken). Skrivs som initierande part och "
        "betalare med SchmeNm/Cd = BANK.",
    )
