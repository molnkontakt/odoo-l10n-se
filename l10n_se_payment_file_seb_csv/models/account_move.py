import re

from odoo import api, fields, models
from odoo.addons.l10n_se_bank_account.lib import se_bank
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools.misc import format_date, formatLang

from ..lib import seb_csv

GIRO_TYPES = ("bankgiro", "plusgiro")
PAYABLE_TYPES = ("bankgiro", "plusgiro", "bban", "iban")


class AccountMove(models.Model):
    _inherit = "account.move"

    l10n_se_payment_export_line_ids = fields.One2many(
        "l10n_se.payment.export.line",
        "move_id",
        string="SEB payment file lines",
        readonly=True,
        copy=False,
    )
    l10n_se_payment_export_id = fields.Many2one(
        "l10n_se.payment.export",
        string="SEB payment file",
        compute="_compute_l10n_se_payment_export",
        help="The SEB payment file that holds this bill: a draft or exported file, or a done file "
        "whose payment has not yet reached the bill.",
    )
    l10n_se_payment_exported = fields.Boolean(
        string="In an SEB payment file",
        compute="_compute_l10n_se_payment_export",
        search="_search_l10n_se_payment_exported",
    )

    @api.depends("l10n_se_payment_export_line_ids.state")
    def _compute_l10n_se_payment_export(self):
        for move in self:
            line = move._l10n_se_seb_active_lines()[:1]
            move.l10n_se_payment_export_id = line.export_id.id
            move.l10n_se_payment_exported = bool(line)

    def _search_l10n_se_payment_exported(self, operator, value):
        if operator != "in":
            return NotImplemented
        active = Domain("l10n_se_payment_export_line_ids", "any", [("holds_bill", "=", True)])
        values = {bool(v) for v in value}
        if values == {True, False}:
            return Domain.TRUE
        if values == {True}:
            return active
        if values == {False}:
            return ~active
        return Domain.FALSE

    def _l10n_se_seb_active_lines(self):
        """Export lines that still hold these bills (see l10n_se.payment.export.line.holds_bill),
        read as superuser: the double-export guard must see every line."""
        return self.sudo().l10n_se_payment_export_line_ids.filtered("holds_bill")

    def _l10n_se_seb_pending_lines(self):
        """Export lines of these bills in files that may still be signed (draft or exported)."""
        return self._l10n_se_seb_active_lines().filtered(
            lambda line: line.state in ("draft", "exported")
        )

    def button_draft(self):
        """A bill in a payment file that may still be paid (draft or exported) cannot be reset
        to draft: the file would no longer match the bill."""
        pending = self._l10n_se_seb_pending_lines()
        if pending:
            raise UserError(
                self.env._(
                    "%(bills)s is in SEB payment file %(batch)s. Cancel its payment there before "
                    "resetting the bill to draft.",
                    bills=", ".join(pending.move_id.mapped("display_name")),
                    batch=", ".join(pending.export_id.mapped("name")),
                )
            )
        return super().button_draft()

    # --- Defaults for the export wizard --------------------------------------------------------

    def _l10n_se_seb_default_bank(self):
        """(bank account, from_partner) for paying this bill: the bill's recipient bank account,
        or else the supplier's only trusted bank account of a Swedish payable type (from_partner
        is then True). An empty recordset when there is none or more than one."""
        self.ensure_one()
        Bank = self.env["res.partner.bank"]
        if self.partner_bank_id:
            return self.partner_bank_id, False
        if not self.commercial_partner_id:
            return Bank, False
        candidates = Bank.search(
            [
                ("partner_id", "child_of", self.commercial_partner_id.id),
                ("allow_out_payment", "=", True),
                ("l10n_se_account_type", "in", PAYABLE_TYPES),
                *Bank._check_company_domain(self.company_id),
            ]
        )
        if len(candidates) == 1:
            return candidates, True
        return Bank, False

    def _l10n_se_seb_default_payment_date(self):
        """The last Swedish bank day on or before the due date, so the payment is not late; today
        at the earliest."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        due = self.invoice_date_due
        return max(se_bank.previous_bank_day(due), today) if due else today

    def _l10n_se_seb_default_reference(self, bank):
        """(reference type, reference) the payee sees: a valid OCR number when paying to a
        Bankgiro/Plusgiro number, a valid RF reference, otherwise the supplier's invoice number
        (the bill's reference) - in "Fakturanummer" for a bank account, as a message for a
        Bankgiro/Plusgiro number, where SEB refuses "Fakturanummer" - or, failing that, a message.
        Never both OCR and invoice number."""
        self.ensure_one()
        payment_ref = se_bank.compact_reference(self.payment_reference)
        # a Bankgiro/Plusgiro payee that takes only messages
        structured = not (bank.l10n_se_ocr_refused and bank.l10n_se_account_type in GIRO_TYPES)
        if structured and bank.l10n_se_account_type in GIRO_TYPES and se_bank.ocr_valid(payment_ref):
            return seb_csv.REFERENCE_OCR, payment_ref
        if structured and se_bank.rf_valid(payment_ref):
            return seb_csv.REFERENCE_RF, payment_ref.upper()
        ref = seb_csv.clean_text(self.ref)
        if (
            seb_csv.REFERENCE_WITHOUT_OCR == "invoice_number"
            and ref
            and len(ref) <= seb_csv.INVOICE_NUMBER_MAX
            and bank.l10n_se_account_type in seb_csv.INVOICE_NUMBER_ACCOUNT_TYPES
        ):
            return seb_csv.REFERENCE_INVOICE, ref
        text = ref or seb_csv.clean_text(self.payment_reference) or seb_csv.clean_text(self.name)
        return seb_csv.REFERENCE_MESSAGE, text[: seb_csv.MESSAGE_MAX].rstrip()

    def _l10n_se_seb_payee_name(self, bank):
        """'Mottagarens namn': the bank account's holder, else the supplier."""
        self.ensure_one()
        return (
            bank.acc_holder_name
            or bank.partner_id.commercial_partner_id.name
            or self.commercial_partner_id.name
            or ""
        )

    @api.model
    def _l10n_se_seb_written_reference(self, reference_type, reference):
        """The reference exactly as the file writes it."""
        if reference_type == seb_csv.REFERENCE_OCR:
            return se_bank.compact_reference(reference)
        if reference_type == seb_csv.REFERENCE_RF:
            return se_bank.compact_reference(reference).upper()
        return seb_csv.clean_text(reference)

    # --- Checks --------------------------------------------------------------------------------

    def _l10n_se_seb_check(
        self, journal, bank, amount, payment_date, reference_type, reference, export_line=None
    ):
        """Can this bill be paid in an SEB CSV file with these values?

        Returns (blocks, warnings), lists of translated texts. A block keeps the bill out of the
        file; a warning must be acknowledged before the file is generated. ``export_line`` is the
        bill's own export line when an existing batch re-checks it.
        """
        self.ensure_one()
        _ = self.env._
        if self.move_type == "in_refund":
            return [
                _(
                    "Credit note: credit notes are not exported. Reconcile it against the "
                    "supplier's bill (outstanding credits on the bill), then export the bill's "
                    "remaining amount."
                )
            ], []
        if self.move_type != "in_invoice":
            return [_("Not a vendor bill.")], []
        if self.state != "posted":
            return [_("The bill is not posted.")], []
        if self.l10n_se_auto_debit:
            return [
                _(
                    "Paid by direct debit: the supplier collects this bill from your account. Do not "
                    "pay it; reconcile it against the bank line instead."
                )
            ], []

        blocks, warnings = [], []
        if journal and self.company_id != journal.company_id:
            blocks.append(
                _(
                    "The bill belongs to %(company)s, the SEB account to %(other)s.",
                    company=self.company_id.name,
                    other=journal.company_id.name,
                )
            )
        currency = self.currency_id
        if currency.name != "SEK":
            blocks.append(
                _(
                    "Foreign currency (%(currency)s): the SEB domestic file only pays SEK. Pay "
                    "the bill as a foreign payment in the internet bank.",
                    currency=currency.name,
                )
            )
        residual = self.amount_residual
        if self.payment_state not in ("not_paid", "partial") or currency.compare_amounts(
            residual, 0
        ) <= 0:
            status = dict(self._fields["payment_state"]._description_selection(self.env)).get(
                self.payment_state, self.payment_state
            )
            blocks.append(_("Nothing to pay: the payment status is %(status)s.", status=status))
        active = self._l10n_se_seb_active_lines()
        if export_line:
            active = active.filtered(lambda line: line.id != export_line.id)
        if active:
            blocks.append(
                _(
                    "Already in SEB payment file %(batch)s. Cancel the bill's line or that file "
                    "to export the bill again.",
                    batch=", ".join(active.export_id.mapped("name")),
                )
            )

        blocks += self._l10n_se_seb_bank_problems(bank, reference_type)
        blocks += self._l10n_se_seb_reference_problems(bank, reference_type, reference)

        if amount is None or currency.compare_amounts(amount, 0) <= 0:
            blocks.append(_("The amount must be positive."))
        elif currency.compare_amounts(amount, residual) > 0 and currency.compare_amounts(
            residual, 0
        ) > 0:
            blocks.append(
                _(
                    "The amount %(amount)s is more than the amount due %(residual)s.",
                    amount=formatLang(self.env, amount, currency_obj=currency),
                    residual=formatLang(self.env, residual, currency_obj=currency),
                )
            )
        elif currency.compare_amounts(amount, residual) < 0:
            warnings.append(
                _(
                    "Partial payment: %(amount)s of %(residual)s due.",
                    amount=formatLang(self.env, amount, currency_obj=currency),
                    residual=formatLang(self.env, residual, currency_obj=currency),
                )
            )

        today = fields.Date.context_today(self)
        if not payment_date:
            blocks.append(_("The payment date is missing."))
        elif payment_date < today:
            blocks.append(
                _(
                    "The payment date %(date)s is in the past.",
                    date=format_date(self.env, payment_date),
                )
            )
        elif not se_bank.is_bank_day(payment_date):
            warnings.append(
                _(
                    "The payment date %(date)s is not a bank day (weekend or Swedish public "
                    "holiday). Check the date the internet bank shows after the upload.",
                    date=format_date(self.env, payment_date),
                )
            )

        if bank:
            holder = bank.partner_id.commercial_partner_id
            if holder and holder not in (
                self.commercial_partner_id,
                self.company_id.partner_id.commercial_partner_id,
            ):
                warnings.append(
                    _(
                        "The bank account %(account)s belongs to %(holder)s, not to the supplier "
                        "%(supplier)s.",
                        account=bank.acc_number,
                        holder=holder.name,
                        supplier=self.commercial_partner_id.name,
                    )
                )
            warnings += self._l10n_se_seb_bank_warnings(bank)
            payment_ref = se_bank.compact_reference(self.payment_reference)
            if (
                reference_type != seb_csv.REFERENCE_OCR
                and not bank.l10n_se_ocr_refused
                and bank.l10n_se_account_type in GIRO_TYPES
                and re.fullmatch(r"[0-9]{2,25}", payment_ref)
                and not se_bank.ocr_valid(payment_ref)
            ):
                warnings.append(
                    _(
                        "The payment reference %(reference)s looks like an OCR number but its "
                        "check digit is wrong, so it is not sent as OCR. If the payee requires "
                        "OCR, the bank will reject the payment.",
                        reference=self.payment_reference,
                    )
                )
        if (
            reference_type == seb_csv.REFERENCE_OCR
            and self.ref
            and se_bank.compact_reference(reference) == se_bank.compact_reference(self.ref)
        ):
            warnings.append(
                _(
                    "The OCR number %(reference)s is the same as the supplier's invoice number. "
                    "Check on the invoice that it is also the OCR reference: the bank checks the "
                    "check digit (and for some payees the length), so a wrong number can go "
                    "through and the supplier then cannot match the payment.",
                    reference=reference,
                )
            )
        warnings += self._l10n_se_seb_credit_note_warnings()
        if self.duplicated_ref_ids:
            warnings.append(
                _(
                    "This bill might be a duplicate of %(bills)s.",
                    bills=", ".join(self.duplicated_ref_ids.mapped("display_name")),
                )
            )
        return blocks, warnings

    def _l10n_se_seb_bank_problems(self, bank, reference_type):
        """Blocking problems with the payee's bank account."""
        _ = self.env._
        if not bank:
            return [
                _(
                    "No bank account: set the supplier's bank account on the bill (Recipient "
                    "Bank) or choose it on this line."
                )
            ]
        problems = []
        if bank.partner_id.commercial_partner_id == self.company_id.partner_id.commercial_partner_id:
            problems.append(
                _(
                    "The bank account %(account)s belongs to your own company, not to the "
                    "supplier.",
                    account=bank.acc_number,
                )
            )
        if not bank.allow_out_payment:
            problems.append(
                _(
                    "The bank account %(account)s is not trusted: mark it as trusted (Send Money) "
                    "before paying to it.",
                    account=bank.acc_number,
                )
            )
        problem = bank._l10n_se_account_problem_message()
        if problem:
            if bank.l10n_se_account_type == "other":
                problem = _(
                    "%(problem)s Set the Swedish account type on the bank account.",
                    problem=problem,
                )
            problems.append(problem)
        elif bank.l10n_se_account_type == "iban" and not se_bank.iban_compact(
            bank.acc_number
        ).startswith("SE"):
            problems.append(
                _(
                    "Foreign IBAN %(account)s: the SEB domestic file only pays Swedish accounts.",
                    account=bank.acc_number,
                )
            )
        if (
            bank.l10n_se_ocr_refused
            and bank.l10n_se_account_type in GIRO_TYPES
            and reference_type in (seb_csv.REFERENCE_OCR, seb_csv.REFERENCE_RF)
        ):
            problems.append(
                _(
                    "The payee %(account)s accepts only text messages, no OCR or RF reference: "
                    "send the reference as a message.",
                    account=bank.acc_number,
                )
            )
        if bank.l10n_se_ocr_required and reference_type != seb_csv.REFERENCE_OCR:
            problems.append(
                _(
                    "The payee requires an OCR reference, but the bill's payment reference "
                    "%(reference)s is not a valid OCR number.",
                    reference=self.payment_reference or _("(empty)"),
                )
            )
        return problems

    def _l10n_se_seb_bank_warnings(self, bank):
        """Warnings about a usable payee bank account: a check digit that could not be confirmed,
        or a bank on the account record that is not the bank of its clearing number."""
        _ = self.env._
        if bank._l10n_se_account_problem_message():
            return []  # a blocking problem is reported instead
        warnings = []
        warning = bank._l10n_se_account_warning_message()
        if warning:
            warnings.append(warning)
        clearing_bank = bank.l10n_se_clearing_bank
        expected_bic = se_bank.bank_bic(clearing_bank)
        bic = (bank.bank_bic or "").strip().upper()
        if expected_bic and bic and bic[:8] != expected_bic[:8]:
            warnings.append(
                _(
                    "The bank account %(account)s is registered at %(registered)s, but its "
                    "clearing number belongs to %(bank)s. Check the account number.",
                    account=bank.acc_number,
                    registered=bank.bank_id.display_name or bic,
                    bank=clearing_bank,
                )
            )
        return warnings

    def _l10n_se_seb_reference_problems(self, bank, reference_type, reference):
        """Blocking problems with the reference the payee sees."""
        _ = self.env._
        text = self._l10n_se_seb_written_reference(reference_type, reference)
        if reference_type == seb_csv.REFERENCE_OCR:
            if bank and bank.l10n_se_account_type not in GIRO_TYPES:
                return [_("An OCR number can only be sent to a Bankgiro or Plusgiro number.")]
            if not se_bank.ocr_valid(text):
                return [
                    _(
                        "OCR number %(reference)s is not valid: an OCR number has 2-25 digits "
                        "and a correct check digit.",
                        reference=reference or "",
                    )
                ]
        elif reference_type == seb_csv.REFERENCE_RF:
            if not se_bank.rf_valid(text):
                return [_("RF reference %(reference)s is not valid.", reference=reference or "")]
        elif reference_type == seb_csv.REFERENCE_INVOICE:
            if bank and bank.l10n_se_account_type not in seb_csv.INVOICE_NUMBER_ACCOUNT_TYPES:
                return [
                    _(
                        "SEB does not accept an invoice number for a Bankgiro or Plusgiro payment: "
                        "send it as a message."
                    )
                ]
            if not text:
                return [_("The invoice number is empty.")]
            if len(text) > seb_csv.INVOICE_NUMBER_MAX:
                return [
                    _(
                        "The invoice number has more than %(max)s characters; send it as a "
                        "message instead.",
                        max=seb_csv.INVOICE_NUMBER_MAX,
                    )
                ]
        elif reference_type == seb_csv.REFERENCE_MESSAGE:
            if not text:
                return [_("The message is empty.")]
            if len(text) > seb_csv.MESSAGE_MAX:
                return [
                    _(
                        "The message has more than %(max)s characters.",
                        max=seb_csv.MESSAGE_MAX,
                    )
                ]
        else:
            return [
                _("Choose what the payee sees: OCR number, RF reference, invoice number or message.")
            ]
        return []

    def _l10n_se_seb_credit_note_warnings(self):
        """A warning when the supplier has open credit notes: they are not netted in the file."""
        _ = self.env._
        if not self.commercial_partner_id:
            return []
        refunds = self.search(
            [
                ("move_type", "=", "in_refund"),
                ("state", "=", "posted"),
                ("payment_state", "in", ("not_paid", "partial")),
                ("commercial_partner_id", "=", self.commercial_partner_id.id),
                ("company_id", "=", self.company_id.id),
            ],
            order="invoice_date, id",
        )
        if not refunds:
            return []
        listed = ", ".join(
            f"{refund.name} ({formatLang(self.env, refund.amount_residual, currency_obj=refund.currency_id)})"
            for refund in refunds[:5]
        )
        if len(refunds) > 5:
            listed += ", …"
        return [
            _(
                "The supplier has open credit notes: %(credit_notes)s. Credit notes are not netted "
                "in the file: reconcile them against this bill first, then export the remaining "
                "amount.",
                credit_notes=listed,
            )
        ]
