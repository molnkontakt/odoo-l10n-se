import base64
import logging

from markupsafe import Markup

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from ..models import sie_analysis as sa
from ..models.l10n_se_sie_import import SYNC_LIMIT

_logger = logging.getLogger(__name__)

EXTENSIONS = (".se", ".si", ".sie")


class L10nSeSieImportWizard(models.TransientModel):
    """Read SIE 4 files (Fortnox, Visma/Spiris and others) into the books: upload, preview,
    import; the reconciliation report is on the import record it creates."""

    _name = "l10n_se.sie.import.wizard"
    _description = "SIE Import"
    _check_company_auto = True

    state = fields.Selection([("upload", "Upload"), ("preview", "Preview")], default="upload")
    mode = fields.Selection(
        [("import", "Import the bookkeeping"),
         ("reconcile", "Reconciliation only (create no entries)")],
        string="What to Do", default="import", required=True,
        help="Reconciliation only: compare Odoo's posted balances per account with the file "
        "(balance sheet accounts with #UB, result accounts with #RES), for example when the "
        "year is booked again by hand in Odoo and the file from the earlier program is the "
        "check.",
    )
    check_date = fields.Date(
        string="As Of",
        help="Empty: the end of the financial year (#UB and #RES). A date within the year: the "
        "file's opening balance plus its monthly balances (#PSALDO) up to that month end, or "
        "plus its vouchers up to that date.",
    )
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, default=lambda self: self.env.company
    )
    attachment_ids = fields.Many2many(
        "ir.attachment", string="SIE Files",
        help="One or more SIE 4 files (.se, .si, .sie), for example one per financial year. "
        "Fortnox: Settings, Export SIE, type 4. Visma/Spiris: Export, SIE file, type 4.",
    )
    year_ids = fields.One2many("l10n_se.sie.import.wizard.year", "wizard_id",
                               string="Financial Years")
    series_ids = fields.One2many("l10n_se.sie.import.wizard.series", "wizard_id",
                                 string="Journals per Series")
    import_opening = fields.Boolean(
        string="Opening Balance", default=True,
        help="Book the opening balance (#IB) of the first selected year as an entry on its first "
        "day. Later years get their opening balance from the vouchers before them.",
    )
    import_vouchers = fields.Boolean(string="Vouchers", default=True)
    opening_differences = fields.Boolean(
        string="Book Opening Balance Differences",
        help="When a year's opening balance in its file differs from the closing balance of the "
        "year before (for example a program that moves the result to retained earnings in the "
        "opening balance), book the difference as an entry on the first day of the year.",
    )
    post_moves = fields.Boolean(
        string="Post Entries", default=True,
        help="Post the entries. Untick to create them as drafts and post them yourself.",
    )
    journal_mode = fields.Selection(
        [("single", "One journal"), ("series", "A journal per series")],
        string="Journals", default="single", required=True,
    )
    journal_id = fields.Many2one(
        "account.journal", string="Journal", check_company=True,
        domain="[('type', '=', 'general'), ('company_id', 'in', (company_id, False))]",
        help="Empty: a journal 'SIE Import' (code SIE) is created when needed. Also used for "
        "the opening balance when there is a journal per series.",
    )
    missing_accounts = fields.Selection(
        [("create", "Create them"), ("abort", "Stop the import")],
        string="Missing Accounts", default="create", required=True,
        help="Accounts the files use that are not in the chart of accounts. Created with the "
        "name from the file and the type of the neighbouring accounts in your chart (BAS class "
        "otherwise).",
    )
    import_analytics = fields.Boolean(
        string="Dimensions as Analytics", default=True,
        help="Cost centres, projects and other SIE dimensions become analytic plans, their "
        "objects analytic accounts, and the lines get an analytic distribution.",
    )
    skip_invalid = fields.Boolean(
        string="Leave Out Vouchers That Do Not Balance",
        help="Import the other vouchers. The years with such vouchers will then differ from "
        "their closing balances.",
    )
    preview_html = fields.Html(string="Preview", compute="_compute_preview", sanitize=False)
    blocked = fields.Boolean(compute="_compute_preview")
    entry_count = fields.Integer(compute="_compute_preview")

    # -- reading ---------------------------------------------------------------------------------

    def _files(self):
        files = []
        for att in self.attachment_ids.sorted("id"):
            if not (att.name or "").lower().endswith(EXTENSIONS):
                raise UserError(self.env._(
                    "%(file)s is not an SIE file (.se, .si or .sie).", file=att.name))
            files.append((att.name, sa.parse_cached(base64.b64decode(att.datas or b""))))
        return files

    def _options(self):
        series = None
        if self.journal_mode == "series":
            series = {s.series: s.journal_id for s in self.series_ids}
        return {
            "years": {y.key for y in self.year_ids if y.selected},
            "import_opening": self.import_opening,
            "import_vouchers": self.import_vouchers,
            "opening_differences": self.opening_differences,
            "skip_invalid": self.skip_invalid,
            "analytics": self.import_analytics,
            "missing": self.missing_accounts,
            "journal": self.journal_id,
            "series_journals": series,
            "reconcile": self.mode == "reconcile",
            "check_date": self.check_date if self.mode == "reconcile" else None,
        }

    def _analysis(self):
        company = self.company_id
        env = self.with_company(company).with_context(allowed_company_ids=company.ids).env
        return sa.analyse(env, company, self._files(), self._options())

    @api.depends("attachment_ids", "year_ids.selected", "series_ids.journal_id",
                 "import_opening", "import_vouchers", "opening_differences", "journal_mode",
                 "journal_id", "missing_accounts", "import_analytics", "skip_invalid", "state",
                 "mode", "check_date")
    def _compute_preview(self):
        for wiz in self:
            if wiz.state != "preview" or not wiz.attachment_ids:
                wiz.preview_html = False
                wiz.blocked = True
                wiz.entry_count = 0
                continue
            try:
                analysis = wiz._analysis()
            except UserError as exc:
                wiz.preview_html = Markup("<div class='alert alert-danger'>%s</div>") % str(exc)
                wiz.blocked = True
                wiz.entry_count = 0
                continue
            wiz.preview_html = sa.render(wiz.env, analysis, wiz._options())
            wiz.blocked = analysis.blocked
            wiz.entry_count = len(analysis.items)

    # -- steps -----------------------------------------------------------------------------------

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "name": self.env._("SIE Import"),
        }

    def action_analyse(self):
        """Step 2: read the files, list the years and series, show the preview."""
        self.ensure_one()
        if not self.attachment_ids:
            raise UserError(self.env._("Upload at least one SIE file."))
        files = self._files()
        company = self.company_id
        years, _problems = sa.collect_years(self.env, company, files)
        if not years:
            raise UserError(self.env._(
                "The files contain no financial year and no vouchers. Is it an SIE file?"))
        self.year_ids = [Command.clear()] + [Command.create({
            "key": y.key, "label": y.label, "date_start": y.start, "date_end": y.end,
            "filename": y.filename, "voucher_count": len(y.vouchers), "selected": True,
        }) for y in years]
        series = {}
        for y in years:
            for v in y.vouchers:
                series[v.series] = series.get(v.series, 0) + 1
        Journal = self.env["account.journal"]
        journals = Journal.search([*Journal._check_company_domain(company),
                                   ("type", "=", "general")])
        by_code = {j.code.upper(): j for j in journals}
        self.series_ids = [Command.clear()] + [Command.create({
            "series": s, "voucher_count": n,
            "journal_id": by_code.get(s.upper(), Journal).id or False,
        }) for s, n in sorted(series.items())]
        has_dimensions = any(t.objects for y in years for v in y.vouchers for t in v.transactions)
        self.import_analytics = has_dimensions
        self.state = "preview"
        return self._reopen()

    def action_back(self):
        self.state = "upload"
        return self._reopen()

    def _create_record(self, analysis, vals):
        names = [name for name, _p in analysis.files]
        record = self.env["l10n_se.sie.import"].create({
            "name": names[0] if len(names) == 1 else self.env._(
                "%(count)s SIE files", count=len(names)),
            "company_id": self.company_id.id,
            "preview_html": sa.render(self.env, analysis, self._options()),
            "year_ids": [Command.create({
                "key": y.key, "label": y.label, "date_start": y.start, "date_end": y.end,
                "filename": y.filename, "voucher_count": len(y.vouchers),
                "existing_count": analysis.stats.get(y.key, {}).get("existing", 0),
            }) for y in analysis.selected],
            **vals,
        })
        attachments = self.env["ir.attachment"]
        for att in self.attachment_ids.sorted("id"):
            attachments |= att.copy({"res_model": record._name, "res_id": record.id})
        record.attachment_ids = attachments
        return record

    def _open(self, record):
        return {
            "type": "ir.actions.act_window",
            "res_model": record._name,
            "res_id": record.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_reconcile(self):
        """Reconciliation only: compare and report, create no entries."""
        self.ensure_one()
        analysis = self._analysis()
        if analysis.blocked:
            raise UserError(self.env._("The reconciliation cannot start:") + "\n- "
                            + "\n- ".join(str(e) for e in analysis.errors))
        record = self._create_record(analysis, {
            "mode": "reconcile",
            "check_date": self.check_date,
            "post_moves": True,
            "state": "running",
        })
        record._run_reconcile()
        return self._open(record)

    def action_import(self):
        """Step 3: import. Small imports run at once; large ones in the background."""
        self.ensure_one()
        if self.mode == "reconcile":
            return self.action_reconcile()
        analysis = self._analysis()
        if analysis.blocked:
            raise UserError(self.env._("The import cannot start:") + "\n- "
                            + "\n- ".join(str(e) for e in analysis.errors))
        if not analysis.items:
            raise UserError(self.env._(
                "There is nothing to import: everything selected is already in Odoo."))
        record = self._create_record(analysis, {
            "mode": "import",
            "import_opening": self.import_opening,
            "import_vouchers": self.import_vouchers,
            "opening_differences": self.opening_differences,
            "post_moves": self.post_moves,
            "journal_mode": self.journal_mode,
            "journal_id": self.journal_id.id,
            "missing_accounts": self.missing_accounts,
            "import_analytics": self.import_analytics,
            "skip_invalid": self.skip_invalid,
            "entry_total": len(analysis.items),
            "series_ids": [Command.create({
                "series": s.series, "voucher_count": s.voucher_count,
                "journal_id": s.journal_id.id,
            }) for s in self.series_ids] if self.journal_mode == "series" else [],
        })
        if len(analysis.items) <= SYNC_LIMIT:
            record._run_all()
        else:
            record.message_post(body=self.env._(
                "%(count)s entries are imported in the background. Refresh the page to follow "
                "the progress.", count=len(analysis.items)))
            self.env.ref("l10n_se_sie4.ir_cron_l10n_se_sie_import")._trigger()
        return self._open(record)


class L10nSeSieImportWizardYear(models.TransientModel):
    _name = "l10n_se.sie.import.wizard.year"
    _description = "SIE Import: Financial Year Choice"
    _order = "date_start"

    wizard_id = fields.Many2one("l10n_se.sie.import.wizard", required=True, ondelete="cascade")
    selected = fields.Boolean(string="Import", default=True)
    key = fields.Char(required=True)
    label = fields.Char(string="Year", readonly=True)
    date_start = fields.Date(string="From", readonly=True)
    date_end = fields.Date(string="To", readonly=True)
    filename = fields.Char(string="File", readonly=True)
    voucher_count = fields.Integer(string="Vouchers", readonly=True)


class L10nSeSieImportWizardSeries(models.TransientModel):
    _name = "l10n_se.sie.import.wizard.series"
    _description = "SIE Import: Series Journal Choice"
    _order = "series"

    wizard_id = fields.Many2one("l10n_se.sie.import.wizard", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="wizard_id.company_id")
    series = fields.Char(readonly=True)
    voucher_count = fields.Integer(string="Vouchers", readonly=True)
    journal_id = fields.Many2one(
        "account.journal", string="Journal",
        domain="[('type', '=', 'general'), ('company_id', 'in', (company_id, False))]",
        help="Empty: a journal 'SIE series <series>' is created.",
    )
