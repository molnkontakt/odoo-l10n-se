from odoo import api, fields, models
from odoo.addons.l10n_se_bank_account.lib import se_bank
from odoo.exceptions import UserError, ValidationError

from ..lib import seb_csv

SEB_BANK = "SEB"  # bank name in se_bank.CLEARING_RANGES
SEB_IBAN_BANK_CODE = "500"


def seb_iban_from_bban(bban):
    """The IBAN of an SEB account from its 11-digit BBAN (bank code 500, zero-padded)."""
    body = SEB_IBAN_BANK_CODE + bban.zfill(17)
    check = 98 - int(body + "281400") % 97  # "SE00" moved to the end, S=28, E=14
    return f"SE{check:02d}{body}"


class AccountJournal(models.Model):
    _inherit = "account.journal"

    l10n_se_seb_csv_export = fields.Boolean(
        string="SEB CSV export",
        help="Vendor bills can be paid from this bank journal with SEB's CSV payment upload "
        "(Export to SEB (CSV) on the vendor bill list). The journal's bank account must be an "
        "SEB account in SEK.",
    )
    l10n_se_seb_csv_from_account = fields.Char(
        string="From account (SEB CSV)",
        compute="_compute_l10n_se_seb_csv",
        help="The journal's own SEB account as the SEB CSV file writes it in 'Från konto'.",
    )
    l10n_se_seb_csv_problem = fields.Char(
        string="SEB CSV check",
        compute="_compute_l10n_se_seb_csv",
    )

    @api.depends(
        "type",
        "currency_id",
        "bank_account_id.acc_number",
        "bank_account_id.clearing_number",
        "bank_account_id.l10n_se_account_type",
        "l10n_se_seb_csv_export",
    )
    def _compute_l10n_se_seb_csv(self):
        for journal in self:
            number = problem = False
            if journal.type == "bank" and (journal.l10n_se_seb_csv_export or journal.bank_account_id):
                try:
                    number = journal._l10n_se_seb_csv_from_account()
                except UserError as exc:
                    problem = exc.args[0]
            journal.l10n_se_seb_csv_from_account = number
            journal.l10n_se_seb_csv_problem = problem if journal.l10n_se_seb_csv_export else False

    def _l10n_se_seb_csv_from_account(self):
        """'Från konto' for this journal: its own SEB account in the form
        seb_csv.FROM_ACCOUNT_FORM says (an 11-digit BBAN by default). UserError otherwise."""
        self.ensure_one()
        _ = self.env._
        bank = self.bank_account_id
        if self.type != "bank" or not bank:
            raise UserError(
                _("Journal %(journal)s has no bank account number.", journal=self.display_name)
            )
        currency = self.currency_id or self.company_id.currency_id
        if currency.name != "SEK":
            raise UserError(
                _(
                    "Journal %(journal)s is in %(currency)s; the SEB CSV file pays from a SEK "
                    "account.",
                    journal=self.display_name,
                    currency=currency.name,
                )
            )
        bban = bank._l10n_se_domestic_bban()  # UserError with the reason
        info = se_bank.clearing_info(bban)
        if len(bban) != 11 or not info or info.bank != SEB_BANK:
            raise UserError(
                _(
                    "The SEB CSV file pays from an SEB account (clearing number and account "
                    "number, 11 digits). The bank account of journal %(journal)s is "
                    "%(number)s.",
                    journal=self.display_name,
                    number=bank.acc_number,
                )
            )
        if seb_csv.FROM_ACCOUNT_FORM == "iban":
            if bank.l10n_se_account_type == "iban":
                return se_bank.iban_compact(bank.acc_number)
            return seb_iban_from_bban(bban)
        return bban

    @api.constrains("l10n_se_seb_csv_export", "type", "bank_account_id", "currency_id")
    def _check_l10n_se_seb_csv_export(self):
        for journal in self.filtered("l10n_se_seb_csv_export"):
            if journal.type != "bank":
                raise ValidationError(
                    self.env._("Only bank journals can pay with the SEB CSV file.")
                )
            try:
                journal._l10n_se_seb_csv_from_account()
            except UserError as exc:
                raise ValidationError(exc.args[0]) from None
