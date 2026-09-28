from datetime import date

from markupsafe import Markup

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from ..models.l10n_se_payment_export import REFERENCE_TYPES


class L10nSePaymentExportWizard(models.TransientModel):
    """Checks the selected vendor bills and turns the payable ones into an SEB payment file.

    Every bill gets a line with the reasons it cannot be paid (blocked) or needs attention
    (warning) before anything is generated. Blocked bills are left out; warnings must be
    acknowledged.
    """

    _name = "l10n_se.payment.export.wizard"
    _description = "Export vendor bills to an SEB payment file"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, default=lambda self: self.env.company
    )
    journal_id = fields.Many2one(
        "account.journal",
        string="Pay from",
        check_company=True,
        domain="[('type', '=', 'bank'), ('l10n_se_seb_csv_export', '=', True), "
        "('company_id', '=', company_id)]",
        help="Bank journals flagged 'SEB CSV export' (Journal Entries tab of the journal).",
    )
    from_account = fields.Char(
        related="journal_id.l10n_se_seb_csv_from_account", string="From account"
    )
    date_mode = fields.Selection(
        [("due", "Due date (today at the earliest)"), ("fixed", "Same date for all")],
        string="Payment date",
        default="due",
        required=True,
    )
    payment_date = fields.Date(string="Date", default=fields.Date.context_today)
    line_ids = fields.One2many("l10n_se.payment.export.wizard.line", "wizard_id", string="Bills")
    currency_id = fields.Many2one(
        "res.currency", readonly=True, default=lambda self: self.env.ref("base.SEK")
    )
    ready_count = fields.Integer(string="Bills to pay", compute="_compute_summary")
    blocked_count = fields.Integer(string="Blocked bills", compute="_compute_summary")
    amount_total = fields.Monetary(string="Total", compute="_compute_summary")
    has_warnings = fields.Boolean(string="Has warnings", compute="_compute_summary")
    # Listed above the bills: as the last of many list columns they were out of sight in the dialog
    warning_summary = fields.Html(
        string="Warnings to read", compute="_compute_summary", sanitize=False
    )
    blocked_summary = fields.Html(
        string="Why bills are blocked", compute="_compute_summary", sanitize=False
    )
    warnings_acknowledged = fields.Boolean(string="I have read the warnings")
    acknowledged_warnings = fields.Text(
        compute="_compute_acknowledged_warnings",
        store=True,
        readonly=False,
        help="The warnings as they were when 'I have read the warnings' was ticked. A warning "
        "that appears or changes afterwards needs a new acknowledgement.",
    )

    @api.model
    def _candidate_domain(self):
        """Bills that can be paid via SEB, when the wizard is opened without a selection."""
        return [
            ("move_type", "=", "in_invoice"),
            ("state", "=", "posted"),
            ("payment_state", "in", ("not_paid", "partial")),
            ("company_id", "=", self.env.company.id),
            ("currency_id.name", "=", "SEK"),
            ("l10n_se_payment_exported", "=", False),
        ]

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Move = self.env["account.move"]
        context = self.env.context
        if context.get("active_model") == "account.move" and context.get("active_ids"):
            moves = Move.browse(context["active_ids"]).exists()
        else:
            moves = Move.search(self._candidate_domain())
        company = moves.company_id if len(moves.company_id) == 1 else self.env.company
        journal = self.env["l10n_se.payment.export"].with_company(company)._default_journal()
        res["company_id"] = company.id
        res["journal_id"] = journal.id
        lines = []
        for move in moves.sorted(lambda m: (m.invoice_date_due or date.max, m.id)):
            bank, from_partner = move._l10n_se_seb_default_bank()
            lines.append(
                Command.create(
                    {
                        "move_id": move.id,
                        "partner_bank_id": bank.id,
                        "bank_from_partner": from_partner,
                        "amount": move.amount_residual,
                        "payment_date": move._l10n_se_seb_default_payment_date(),
                    }
                )
            )
        res["line_ids"] = lines
        return res

    @api.depends(
        "line_ids.include",
        "line_ids.amount",
        "line_ids.status",
        "line_ids.warnings",
        "line_ids.problems",
    )
    def _compute_summary(self):
        for wizard in self:
            ready = wizard.line_ids.filtered(lambda line: line.include and line.status != "blocked")
            blocked = wizard.line_ids.filtered(lambda line: line.status == "blocked")
            warned = ready.filtered(lambda line: line.status == "warning")
            wizard.ready_count = len(ready)
            wizard.blocked_count = len(blocked)
            wizard.amount_total = sum(ready.mapped("amount"))
            wizard.has_warnings = bool(warned)
            # the same bills as _l10n_se_warnings_signature: those that need the acknowledgement
            wizard.warning_summary = self._l10n_se_summary_html(warned, "warnings")
            wizard.blocked_summary = self._l10n_se_summary_html(blocked, "problems")

    @api.model
    def _l10n_se_summary_html(self, lines, field_name):
        """One list item per text of the lines' field, starting with the bill and its supplier."""

        def bill(move):
            # a draft is called "/"; its display name says "Draft Bill (reference)"
            return move.name if move.name and move.name != "/" else move.display_name

        items = [
            Markup("<li><strong>%s</strong> %s: %s</li>")
            % (bill(line.move_id), line.partner_id.name or "", text)
            for line in lines
            for text in (line[field_name] or "").splitlines()
            if text.strip()
        ]
        return Markup('<ul class="mb-0 ps-3">%s</ul>') % Markup().join(items) if items else False

    def _l10n_se_warnings_signature(self):
        """The warnings of the bills to be paid, one line per bill."""
        self.ensure_one()
        lines = self.line_ids.filtered(lambda line: line.include and line.status == "warning")
        return "\n".join(
            f"{line.move_id.id}: {line.warnings}" for line in lines.sorted(lambda line: line.move_id.id)
        )

    @api.depends("warnings_acknowledged")
    def _compute_acknowledged_warnings(self):
        for wizard in self:
            wizard.acknowledged_warnings = (
                wizard._l10n_se_warnings_signature() if wizard.warnings_acknowledged else False
            )

    @api.onchange("line_ids", "journal_id", "date_mode", "payment_date")
    def _onchange_warnings_changed(self):
        """Untick 'I have read the warnings' when the warnings change after it was ticked."""
        if self.warnings_acknowledged and (
            (self.acknowledged_warnings or "") != self._l10n_se_warnings_signature()
        ):
            self.warnings_acknowledged = False

    @api.onchange("date_mode", "payment_date")
    def _onchange_date_mode(self):
        for line in self.line_ids:
            if self.date_mode == "fixed" and self.payment_date:
                line.payment_date = self.payment_date
            else:
                line.payment_date = line.move_id._l10n_se_seb_default_payment_date()

    def _l10n_se_create_batch(self):
        self.ensure_one()
        _ = self.env._
        self.env["l10n_se.payment.export"].check_access("create")
        if not self.journal_id:
            raise UserError(
                _(
                    "Choose the SEB account to pay from. Flag a bank journal with 'SEB CSV "
                    "export' if there is none."
                )
            )
        self.journal_id._l10n_se_seb_csv_from_account()  # UserError with the reason
        included = self.line_ids.filtered("include")
        if not included:
            raise UserError(_("No bill to export. The reason is shown on each bill's line."))
        problems, warned = [], False
        for line in included:
            blocks, warnings = line._l10n_se_check()
            problems += [f"{line.move_id.display_name}: {block}" for block in blocks]
            warned = warned or bool(warnings)
        if problems:
            raise UserError(
                _(
                    "These bills cannot be exported:\n%(problems)s\n\nUntick them, or fix them "
                    "and open the export again.",
                    problems="\n".join(problems),
                )
            )
        if warned and not self.warnings_acknowledged:
            raise UserError(
                _("Some bills have warnings. Read them and tick 'I have read the warnings'.")
            )
        if warned and (self.acknowledged_warnings or "") != self._l10n_se_warnings_signature():
            raise UserError(
                _(
                    "The warnings changed after 'I have read the warnings' was ticked. Read them "
                    "again, then untick and tick it."
                )
            )
        ordered = included.sorted(
            lambda line: (line.payment_date, line.move_id.invoice_date_due or date.max, line.id)
        )
        return self.env["l10n_se.payment.export"].create(
            {
                "company_id": self.company_id.id,
                "journal_id": self.journal_id.id,
                "line_ids": [
                    Command.create(
                        {
                            "sequence": sequence,
                            "move_id": line.move_id.id,
                            "partner_bank_id": line.partner_bank_id.id,
                            "amount": line.currency_id.round(line.amount),
                            "payment_date": line.payment_date,
                            "reference_type": line.reference_type,
                            "reference": line.reference,
                        }
                    )
                    for sequence, line in enumerate(ordered, start=1)
                ],
            }
        )

    def _l10n_se_open_batch(self, batch):
        return {
            "type": "ir.actions.act_window",
            "res_model": "l10n_se.payment.export",
            "res_id": batch.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_generate(self):
        """Create the batch and generate its CSV file."""
        batch = self._l10n_se_create_batch()
        batch.action_generate()
        return self._l10n_se_open_batch(batch)

    def action_save_draft(self):
        """Create the batch without a file; its bills are reserved until it is cancelled."""
        batch = self._l10n_se_create_batch()
        return self._l10n_se_open_batch(batch)


class L10nSePaymentExportWizardLine(models.TransientModel):
    _name = "l10n_se.payment.export.wizard.line"
    _description = "Vendor bill in an SEB payment file export"

    wizard_id = fields.Many2one(
        "l10n_se.payment.export.wizard", required=True, ondelete="cascade", index=True
    )
    move_id = fields.Many2one("account.move", string="Bill", required=True, readonly=True)
    partner_id = fields.Many2one(related="move_id.commercial_partner_id", string="Supplier")
    move_ref = fields.Char(related="move_id.ref", string="Bill reference")
    invoice_date_due = fields.Date(related="move_id.invoice_date_due", string="Due date")
    currency_id = fields.Many2one(related="move_id.currency_id")
    amount_residual = fields.Monetary(related="move_id.amount_residual", string="Amount due")
    partner_bank_id = fields.Many2one("res.partner.bank", string="Bank account")
    clearing_bank = fields.Char(
        related="partner_bank_id.l10n_se_clearing_bank", string="Bank (from clearing number)"
    )
    bank_from_partner = fields.Boolean(string="Bank account from supplier", readonly=True)
    amount = fields.Monetary(string="Amount")
    payment_date = fields.Date(string="Payment date")
    reference_type = fields.Selection(
        REFERENCE_TYPES,
        string="Reference type",
        compute="_compute_reference",
        store=True,
        readonly=False,
        help="What the payee sees. OCR only with a valid OCR number to a Bankgiro or Plusgiro "
        "number; never both OCR and invoice number.",
    )
    reference = fields.Char(
        string="Reference", compute="_compute_reference", store=True, readonly=False
    )
    status = fields.Selection(
        [("ok", "Ready"), ("warning", "Warning"), ("blocked", "Blocked")],
        compute="_compute_status",
    )
    problems = fields.Text(string="Why blocked", compute="_compute_status")
    warnings = fields.Text(string="Warnings", compute="_compute_status")
    include = fields.Boolean(
        string="Pay",
        compute="_compute_include",
        store=True,
        readonly=False,
        help="Bills that are not blocked when the export is opened are paid; untick to leave a "
        "bill out. A bill that becomes blocked while it is ticked stops the export until it is "
        "unticked or fixed.",
    )

    @api.depends("move_id", "partner_bank_id")
    def _compute_reference(self):
        for line in self:
            if line.move_id:
                line.reference_type, line.reference = line.move_id._l10n_se_seb_default_reference(
                    line.partner_bank_id
                )
            else:
                line.reference_type = line.reference = False

    def _l10n_se_check(self):
        """(blocks, warnings) for this line, as translated texts."""
        self.ensure_one()
        blocks, warnings = self.move_id._l10n_se_seb_check(
            self.wizard_id.journal_id,
            self.partner_bank_id,
            self.amount,
            self.payment_date,
            self.reference_type,
            self.reference,
        )
        if self.bank_from_partner and self.partner_bank_id and not self.move_id.partner_bank_id:
            warnings.append(
                self.env._(
                    "The bill has no bank account; the supplier's only trusted account "
                    "%(account)s is used.",
                    account=self.partner_bank_id.acc_number,
                )
            )
        return blocks, warnings

    @api.depends(
        "move_id",
        "partner_bank_id",
        "amount",
        "payment_date",
        "reference_type",
        "reference",
        "wizard_id.journal_id",
    )
    def _compute_status(self):
        for line in self:
            if not line.move_id:
                line.status, line.problems, line.warnings = "blocked", False, False
                continue
            blocks, warnings = line._l10n_se_check()
            line.problems = "\n".join(blocks) or False
            line.warnings = "\n".join(warnings) or False
            line.status = "blocked" if blocks else "warning" if warnings else "ok"

    @api.depends("move_id")
    def _compute_include(self):
        """Set once, when the line is made: later edits (amount, date, bank account, the date mode
        of the wizard) must not tick a bill the user left out. _l10n_se_create_batch refuses
        ticked bills that are blocked."""
        for line in self:
            line.include = bool(line.move_id) and not line._l10n_se_check()[0]
