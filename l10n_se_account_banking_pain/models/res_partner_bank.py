import re

from odoo import api, fields, models

# Bank code in a Swedish IBAN (positions 5-7) -> BIC. Only used when the bank record has no BIC.
SE_IBAN_BANK_BIC = {
    "120": "DABASESX",  # Danske Bank
    "300": "NDEASESS",  # Nordea
    "500": "ESSESESS",  # SEB
    "600": "HANDSESS",  # Handelsbanken
    "800": "SWEDSESS",  # Swedbank / Sparbanker
    "902": "ELLFSESS",  # Länsförsäkringar
    "927": "IBCASES1",  # ICA Banken
    "957": "SKIASESS",  # Skandiabanken
}


def luhn_valid(number):
    digits = [int(d) for d in number]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


class ResPartnerBank(models.Model):
    _inherit = "res.partner.bank"

    l10n_se_account_type = fields.Selection(
        [
            ("bankgiro", "Bankgiro"),
            ("plusgiro", "Plusgiro"),
            ("bban", "Bankkonto (clearing + konto)"),
            ("iban", "IBAN"),
            ("other", "Annat"),
        ],
        string="Svensk kontotyp",
        compute="_compute_l10n_se_account_type",
        store=True,
        readonly=False,
        help="Hur kontot skrivs i svenska betalfiler (ISO 20022). Tolkas från kontonumret: "
        "prefix BG/PG, IBAN eller clearing- och kontonummer. Kan ändras manuellt.",
    )

    @api.depends("acc_number", "acc_type")
    def _compute_l10n_se_account_type(self):
        for bank in self:
            bank.l10n_se_account_type = bank._l10n_se_guess_account_type()

    def _l10n_se_guess_account_type(self):
        self.ensure_one()
        raw = (self.acc_number or "").strip()
        upper = raw.upper()
        if self.acc_type == "iban":
            return "iban"
        if re.match(r"^(BG|BANKGIRO)\b", upper):
            return "bankgiro"
        if re.match(r"^(PG|PLUSGIRO|POSTGIRO)\b", upper):
            return "plusgiro"
        digits = re.sub(r"\D", "", raw)
        if re.fullmatch(r"\d{3,4}-\d{4}", raw):
            return "bankgiro"
        if re.fullmatch(r"\d{1,7}-\d", raw.replace(" ", "")):
            return "plusgiro"
        if 9 <= len(digits) <= 15:
            return "bban"
        return "other"

    def _l10n_se_account_digits(self):
        """Account number as the bank expects it: digits only, without prefix or currency."""
        self.ensure_one()
        raw = (self.acc_number or "").upper()
        raw = re.sub(r"^(BG|BANKGIRO|PG|PLUSGIRO|POSTGIRO)\b", "", raw)
        raw = re.sub(r"(SEK|EUR)$", "", raw.strip())
        return re.sub(r"\D", "", raw)

    def _l10n_se_bic(self):
        self.ensure_one()
        if self.bank_bic:
            return self.bank_bic
        iban = (self.sanitized_acc_number or "").upper()
        if self.acc_type == "iban" and iban.startswith("SE") and len(iban) >= 7:
            return SE_IBAN_BANK_BIC.get(iban[4:7])
        return False
