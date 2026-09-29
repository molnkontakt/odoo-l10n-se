from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..lib import se_bank


class ResPartnerBank(models.Model):
    _inherit = "res.partner.bank"

    # What the payee's Bankgiro/Plusgiro number accepts. SEB's payment form shows it when the
    # number is typed ("Du måste fylla i OCR", "tillåter bara textmeddelanden, inte OCR"); an
    # uploaded file is refused without saying so in advance. Used by the payment-file modules.
    l10n_se_ocr_required = fields.Boolean(
        string="Payee requires OCR",
        help="This Bankgiro or Plusgiro number only accepts payments with a valid OCR "
        "reference. Payment files refuse bills to it that have no valid OCR number in their "
        "payment reference.",
    )
    l10n_se_ocr_refused = fields.Boolean(
        string="Payee accepts only messages",
        help="This Bankgiro or Plusgiro number takes only text messages, no OCR or RF "
        "reference. Payment files send the supplier's invoice number as a message, even when "
        "the bill has a valid OCR number.",  # SEB CSV and pain.001 both do
    )
    l10n_se_account_type = fields.Selection(
        [
            ("bankgiro", "Bankgiro"),
            ("plusgiro", "Plusgiro"),
            ("bban", "Bank account (clearing + account)"),
            ("iban", "IBAN"),
            ("other", "Other"),
        ],
        string="Swedish account type",
        compute="_compute_l10n_se_account_type",
        store=True,
        readonly=False,
        help="How the account is written in Swedish payment files. Guessed from the account "
        "number: a BG/PG prefix, an IBAN, or a clearing number and account number. Can be "
        "changed by hand; changing the account number or the clearing number guesses it again.",
    )
    l10n_se_payment_number = fields.Char(
        string="Number in payment files",
        compute="_compute_l10n_se_check",
        help="The account number as Swedish payment files write it: Bankgiro and Plusgiro "
        "digits only, bank accounts per clearing-number range as the banks' ISO 20022 guides "
        "require, IBAN without spaces.",
    )
    l10n_se_clearing_bank = fields.Char(
        string="Bank (from clearing number)",
        compute="_compute_l10n_se_check",
    )
    l10n_se_account_problem = fields.Char(
        string="Swedish account check",
        compute="_compute_l10n_se_check",
    )
    l10n_se_account_warning = fields.Char(
        string="Swedish account warning",
        compute="_compute_l10n_se_check",
        help="The number can be used, but its check digit could not be confirmed.",
    )

    @api.depends("acc_number", "acc_type", "clearing_number")
    def _compute_l10n_se_account_type(self):
        for bank in self:
            bank.l10n_se_account_type = bank._l10n_se_guess_account_type()

    @api.depends("acc_number", "clearing_number", "l10n_se_account_type")
    def _compute_l10n_se_check(self):
        for bank in self:
            number = bank_name = problem = warning = False
            if bank.acc_number and bank.l10n_se_account_type not in (False, "other"):
                try:
                    number = bank._l10n_se_lib_payment_account()
                    if bank.l10n_se_account_type == "bban":
                        bank_name = bank._l10n_se_lib_bban().bank or False
                        warning = bank._l10n_se_account_warning_message()
                except se_bank.InvalidAccountNumber as exc:
                    problem = bank._l10n_se_error_message(exc)
            bank.l10n_se_payment_number = number
            bank.l10n_se_clearing_bank = bank_name
            bank.l10n_se_account_problem = problem
            bank.l10n_se_account_warning = warning

    # --- Thin wrappers around lib/se_bank.py -------------------------------------------------

    def _l10n_se_guess_account_type(self):
        self.ensure_one()
        return se_bank.guess_account_type(self.acc_number, self.acc_type, self.clearing_number)

    def _l10n_se_account_digits(self):
        """Digits of the account number without a BG/PG prefix or currency suffix."""
        self.ensure_one()
        return se_bank.account_digits(self.acc_number)

    def _l10n_se_bic(self):
        """The bank's BIC, or one derived from a Swedish IBAN's bank code; False when unknown."""
        self.ensure_one()
        if self.bank_bic:
            return self.bank_bic
        return se_bank.se_iban_bic(self.sanitized_acc_number) or False

    def _l10n_se_lib_bban(self):
        self.ensure_one()
        return se_bank.split_bban(self.acc_number, self.clearing_number)

    def _l10n_se_lib_payment_account(self):
        self.ensure_one()
        return se_bank.payment_account_number(
            self.acc_number, self.l10n_se_account_type, self.clearing_number
        )

    def _l10n_se_bban(self):
        """The bank account split by its clearing number (se_bank.Bban); UserError when invalid.

        ``.bban`` is the account as payment files write it (MIG Annex 5), ``.clearing`` the
        clearing number (5 digits for Swedbank's 8-series), ``.bank`` the bank's name.
        """
        try:
            return self._l10n_se_lib_bban()
        except se_bank.InvalidAccountNumber as exc:
            raise UserError(self._l10n_se_error_message(exc)) from None

    def _l10n_se_payment_account(self):
        """The payee account number for a Swedish payment file, per l10n_se_account_type.

        Bankgiro/Plusgiro: digits with a verified check digit; bank account: MIG Annex 5 form;
        IBAN: compact. Raises UserError with a translated reason when the number is not usable.
        """
        try:
            return self._l10n_se_lib_payment_account()
        except se_bank.InvalidAccountNumber as exc:
            raise UserError(self._l10n_se_error_message(exc)) from None

    def _l10n_se_domestic_bban(self):
        """This account as a domestic Swedish bank account number (MIG Annex 5 BBAN).

        For a bank account type it is the normalised number; for a Swedish IBAN it is derived
        from the IBAN where the IBAN carries the clearing number (e.g. SEB). Meant for the
        payer's own account ("from account") in payment files. UserError otherwise.
        """
        self.ensure_one()
        try:
            if self.l10n_se_account_type == "iban":
                return se_bank.bban_from_se_iban(self.acc_number)
            if self.l10n_se_account_type == "bban":
                return self._l10n_se_lib_bban().bban
            raise se_bank.InvalidAccountNumber(
                se_bank.ERR_ACCOUNT_TYPE_UNKNOWN, number=self.acc_number or ""
            )
        except se_bank.InvalidAccountNumber as exc:
            raise UserError(self._l10n_se_error_message(exc)) from None

    def _l10n_se_account_problem_message(self):
        """Why the account cannot be used in a Swedish payment file (translated), or False."""
        self.ensure_one()
        try:
            self._l10n_se_lib_payment_account()
        except se_bank.InvalidAccountNumber as exc:
            return self._l10n_se_error_message(exc)
        return False

    def _l10n_se_account_warning_message(self):
        """Why the account's check digit could not be confirmed (translated), or False.

        A bank account (type bban) whose clearing number is not in the banks' list cannot be
        checked; Swedbank 8-series and PlusGirot accounts pass mod 10 only as a rule, so a failure
        there is a warning, not an error. False for other types and for unusable numbers (see
        _l10n_se_account_problem_message).
        """
        self.ensure_one()
        if self.l10n_se_account_type != "bban":
            return False
        try:
            parts = self._l10n_se_lib_bban()
        except se_bank.InvalidAccountNumber:
            return False
        if parts.check_digit == se_bank.CHECK_DIGIT_FAILED:
            return self.env._(
                "The check digit of bank account %(number)s does not match. %(bank)s has a few "
                "accounts like this: confirm the number with the payee.",
                number=self.acc_number,
                bank=parts.bank,
            )
        if parts.check_digit == se_bank.CHECK_DIGIT_UNKNOWN:
            return self.env._(
                "Clearing number %(clearing)s is not in the Swedish banks' list, so bank account "
                "%(number)s could not be checked. Confirm the number with the payee.",
                clearing=parts.clearing,
                number=self.acc_number,
            )
        return False

    def _l10n_se_error_templates(self):
        """Translated message templates per se_bank error code (se_bank.MESSAGES in English)."""
        _ = self.env._
        return {
            se_bank.ERR_EMPTY: _("The account number is empty."),
            se_bank.ERR_BANKGIRO_LENGTH: _(
                "Bankgiro number %(number)s has %(length)s digits; a Bankgiro number has 7 or 8."
            ),
            se_bank.ERR_BANKGIRO_CHECK_DIGIT: _(
                "Bankgiro number %(number)s has an invalid check digit."
            ),
            se_bank.ERR_PLUSGIRO_LENGTH: _(
                "Plusgiro number %(number)s has %(length)s digits; a Plusgiro number has 2 to 8."
            ),
            se_bank.ERR_PLUSGIRO_CHECK_DIGIT: _(
                "Plusgiro number %(number)s has an invalid check digit."
            ),
            se_bank.ERR_BBAN_TOO_SHORT: _(
                "Bank account %(number)s is too short to hold a clearing number and an account "
                "number."
            ),
            se_bank.ERR_BBAN_LENGTH: _(
                "Bank account %(number)s: clearing number %(clearing)s%(bank)s expects "
                "%(expected)s digits after the clearing number, found %(length)s."
            ),
            se_bank.ERR_BBAN_CLEARING_CHECK_DIGIT: _(
                "Bank account %(number)s: the Swedbank clearing number %(clearing)s has an "
                "invalid check digit (expected %(expected)s)."
            ),
            se_bank.ERR_BBAN_AMBIGUOUS: _(
                "Bank account %(number)s: cannot tell where the Swedbank clearing number ends. "
                "Write it with its check digit and a separator, e.g. 8xxx-x followed by the "
                "account number."
            ),
            se_bank.ERR_BBAN_CHECK_DIGIT: _(
                "Bank account %(number)s has an invalid check digit for clearing number "
                "%(clearing)s%(bank)s. Check the clearing and account number with the payee."
            ),
            se_bank.ERR_ZERO: _("Account number %(number)s is only zeros."),
            se_bank.ERR_IBAN_INVALID: _("%(number)s is not a valid IBAN."),
            se_bank.ERR_IBAN_NOT_SWEDISH: _("%(number)s is not a Swedish IBAN."),
            se_bank.ERR_IBAN_NO_BBAN: _(
                "The domestic account number cannot be derived from IBAN %(number)s."
            ),
            se_bank.ERR_ACCOUNT_TYPE_UNKNOWN: _(
                "Account %(number)s has no known Swedish account type (Bankgiro, Plusgiro, bank "
                "account or IBAN)."
            ),
        }

    def _l10n_se_error_message(self, exc):
        """Translated text for a se_bank.InvalidAccountNumber."""
        template = self._l10n_se_error_templates().get(exc.code)
        return template % exc.params if template else str(exc)

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
