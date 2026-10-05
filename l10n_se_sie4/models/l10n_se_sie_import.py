import base64
import logging
import time
from collections import defaultdict
from decimal import Decimal

from markupsafe import Markup

from odoo import Command, api, fields, models
from odoo.addons.base.models.ir_cron import MIN_TIME_PER_JOB
from odoo.addons.l10n_se_sie4.lib import sie
from odoo.exceptions import UserError

from . import sie_analysis as sa

_logger = logging.getLogger(__name__)

#: Entries created and posted per batch.
BATCH_SIZE = 200
#: Entries per batch in the background.
CRON_BATCH_SIZE = 100
#: Up to this many entries the import runs at once; above it runs in the background.
SYNC_LIMIT = 1000
#: Up to this many entries an undo runs at once; above it runs in the background (still in one
#: transaction: all or nothing).
UNDO_SYNC_LIMIT = 3000
#: Background undo attempts before giving up (an attempt that hits the server's time limit is
#: rolled back and leaves nothing deleted).
UNDO_ATTEMPTS = 2
#: Seconds a background run may use before it hands over to the next run (limit_time_real of a
#: cron is 120 s by default).
CRON_BUDGET = 60


class L10nSeSieImport(models.Model):
    """One SIE import: the files, the options, the entries it created and the check of the
    result against the files' closing balances. Can be undone while none of its entries is
    locked, reconciled, reversed or changed."""

    _name = "l10n_se.sie.import"
    _description = "SIE Import"
    _inherit = ["mail.thread"]
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    user_id = fields.Many2one(
        "res.users", string="Imported by", readonly=True, default=lambda self: self.env.user
    )
    mode = fields.Selection(
        [("import", "Import"), ("reconcile", "Reconciliation only")],
        default="import",
        required=True,
        readonly=True,
    )
    check_date = fields.Date(
        string="Compared As Of", readonly=True,
        help="Reconciliation on this date instead of the end of the financial year.",
    )
    state = fields.Selection(
        [
            ("queued", "Waiting"),
            ("running", "Importing"),
            ("done", "Done"),
            ("reconciled", "Reconciliation"),
            ("failed", "Failed"),
            ("undoing", "Undoing"),
            ("undone", "Undone"),
        ],
        default="queued",
        required=True,
        readonly=True,
        tracking=True,
    )
    attachment_ids = fields.Many2many(
        "ir.attachment", string="Files", readonly=True, copy=False,
        help="The SIE files as they were uploaded.",
    )
    # -- options (as chosen in the wizard)
    import_opening = fields.Boolean(string="Opening Balance", readonly=True)
    import_vouchers = fields.Boolean(string="Vouchers", readonly=True)
    opening_differences = fields.Boolean(string="Book Opening Balance Differences", readonly=True)
    post_moves = fields.Boolean(string="Post Entries", readonly=True)
    journal_mode = fields.Selection(
        [("single", "One journal"), ("series", "A journal per series")],
        readonly=True,
        default="single",
    )
    journal_id = fields.Many2one(
        "account.journal", readonly=True, check_company=True,
        help="The journal of all entries (one journal), and of the opening balance.",
    )
    series_ids = fields.One2many("l10n_se.sie.import.series", "import_id", readonly=True)
    missing_accounts = fields.Selection(
        [("create", "Create them"), ("abort", "Stop the import")], readonly=True
    )
    import_analytics = fields.Boolean(string="Dimensions as Analytics", readonly=True)
    skip_invalid = fields.Boolean(string="Leave Out Vouchers That Do Not Balance", readonly=True)
    year_ids = fields.One2many("l10n_se.sie.import.year", "import_id", string="Financial Years",
                               readonly=True)
    # -- results
    move_ids = fields.One2many("account.move", "l10n_se_sie_import_id", string="Entries",
                               readonly=True)
    move_count = fields.Integer(compute="_compute_move_count")
    entry_total = fields.Integer(string="Entries to Create", readonly=True)
    entry_done = fields.Integer(string="Entries Created", readonly=True)
    undone_count = fields.Integer(string="Entries Deleted", readonly=True)
    undo_attempts = fields.Integer(readonly=True)
    created_account_ids = fields.Many2many(
        "account.account", "l10n_se_sie_import_account_rel", string="Created Accounts",
        readonly=True,
    )
    created_analytic_ids = fields.Many2many(
        "account.analytic.account", "l10n_se_sie_import_analytic_rel",
        string="Created Analytic Accounts", readonly=True,
    )
    created_journal_ids = fields.Many2many(
        "account.journal", "l10n_se_sie_import_journal_rel", string="Created Journals",
        readonly=True,
    )
    check_ids = fields.One2many("l10n_se.sie.import.check", "import_id",
                                string="Reconciliation", readonly=True)
    deviation_ids = fields.One2many("l10n_se.sie.import.check", "import_id",
                                    string="Deviations", readonly=True,
                                    domain=[("is_deviation", "=", True)])
    deviation_count = fields.Integer(readonly=True)
    checked_count = fields.Integer(string="Accounts Checked", readonly=True)
    #: Later imports that were checked again after this one: [{id, name, years, checked,
    #: deviations, unchecked}]
    later_checks = fields.Json(readonly=True)
    # Stored, so it is sanitized: it is a snapshot of the preview at import time.
    preview_html = fields.Html(string="Analysis", readonly=True)
    report_html = fields.Html(string="Reconciliation Report", compute="_compute_report_html",
                              sanitize=False)
    error_message = fields.Text(readonly=True)
    date_done = fields.Datetime(string="Finished", readonly=True)

    @api.depends("move_ids")
    def _compute_move_count(self):
        for rec in self:
            rec.move_count = len(rec.move_ids)

    # -- inputs ------------------------------------------------------------------------------------

    def _files(self):
        files = []
        for att in self.attachment_ids.sorted("id"):
            files.append((att.name, sa.parse_cached(base64.b64decode(att.datas or b""))))
        return files

    def _options(self):
        series = None
        if self.journal_mode == "series":
            series = {s.series: s.journal_id for s in self.series_ids}
        return {
            "years": {y.key for y in self.year_ids},
            "import_opening": self.import_opening,
            "import_vouchers": self.import_vouchers,
            "opening_differences": self.opening_differences,
            "skip_invalid": self.skip_invalid,
            "analytics": self.import_analytics,
            "missing": self.missing_accounts,
            "journal": self.journal_id,
            "series_journals": series,
            "import_id": self.id,
        }

    def _env(self):
        company = self.company_id
        env, _hidden = sa.company_env(self.env, company)
        return self.with_env(env).with_company(company).with_context(
            tracking_disable=True,
            mail_create_nolog=True,
            mail_notrack=True,
        )

    # -- running -----------------------------------------------------------------------------------

    def _analysis(self):
        return sa.analyse(self.env, self.company_id, self._files(), self._options())

    def _run(self, chunk=None, deadline=None):
        """Create (and post) the entries, ``chunk`` at a time, until all are created or the
        ``deadline`` (time.monotonic) has passed. Returns how many are left.

        Everything is checked once at the start of the run (lock dates, order of the years,
        duplicates), so a run that resumes after a crash, or after someone changed a lock date,
        only writes what is allowed. The opening balance differences are checked again against
        the books right before they are booked."""
        self.ensure_one()
        chunk = chunk or BATCH_SIZE
        rec = self._env()
        analysis = rec._analysis()
        if analysis.blocked:
            raise UserError(rec.env._("The import cannot continue:") + "\n- "
                            + "\n- ".join(str(e) for e in analysis.errors))
        if rec.state == "queued":
            rec.state = "running"
        rec._prepare_master_data(analysis)
        items = analysis.items
        done = 0
        while done < len(items):
            batch = items[done:done + chunk]
            rec._create_entries(analysis, batch)
            done += len(batch)
            rec.entry_done += len(batch)
            if deadline is not None and time.monotonic() > deadline:
                break
        left = len(items) - done
        if not left:
            rec._finish()
        return left

    def _run_all(self):
        self._run()

    def _journal_for(self, item):
        if item.journal:
            return item.journal
        if self.journal_mode == "series" and item.kind == "voucher":
            line = self.series_ids.filtered(lambda s: s.series == item.voucher.series)[:1]
            if line and line.journal_id:
                return line.journal_id
            journal = self._create_journal(
                self.env._("SIE series %(series)s", series=item.voucher.series or "-"),
                "S" + (item.voucher.series or "X"),
            )
            if line:
                line.journal_id = journal
            return journal
        if not self.journal_id:
            Journal = self.env["account.journal"]
            journal = Journal.search([
                *Journal._check_company_domain(self.company_id),
                ("type", "=", "general"), ("code", "=", "SIE"),
            ], limit=1)
            self.journal_id = journal or self._create_journal(self.env._("SIE Import"), "SIE")
        return self.journal_id

    def _create_journal(self, name, code):
        Journal = self.env["account.journal"].with_context(active_test=False)
        taken = set(Journal.search(Journal._check_company_domain(self.company_id)).mapped("code"))
        base = "".join(c for c in code.upper() if c.isalnum() and c.isascii())[:5] or "SIE"
        candidate, n = base, 1
        while candidate in taken:
            n += 1
            candidate = f"{base[:5 - len(str(n))]}{n}"
        # The hash setting is left to Odoo: OCA account_journal_restrict_mode requires it on
        # every general journal. The preview says when entries will be hashed.
        journal = Journal.create({
            "name": name,
            "code": candidate,
            "type": "general",
            "company_id": self.company_id.id,
        })
        self.created_journal_ids = [Command.link(journal.id)]
        return journal

    def _prepare_master_data(self, analysis):
        """Accounts, analytic plans and analytic accounts the entries need. Idempotent."""
        company = self.company_id
        Account = self.env["account.account"].with_company(company)
        for code, name, _ktyp, account_type in analysis.missing_accounts:
            vals = {
                "code": code,
                "name": name or self.env._("Account %(code)s", code=code),
                "account_type": account_type,
                "company_ids": [Command.link(sa.root(company).id)],
            }
            if account_type in ("asset_receivable", "liability_payable"):
                vals["reconcile"] = True
            account = Account.create(vals)
            analysis.accounts[code] = account
            self.created_account_ids = [Command.link(account.id)]
        for account in analysis.archived_accounts:
            account.active = True
        analysis.missing_accounts = []
        analysis.archived_accounts = []
        if not self.import_analytics:
            return
        # Shared configuration: created even when the user lacks the analytic rights.
        Plan = self.env["account.analytic.plan"].sudo()
        Analytic = self.env["account.analytic.account"].sudo().with_context(active_test=False)
        used = defaultdict(set)
        for item in analysis.items:
            if item.voucher:
                for t in item.voucher.transactions:
                    for dim, obj in t.objects:
                        used[dim].add(obj)
        for dim in used:
            plan = Plan.search([("l10n_se_sie_dimension", "=", int(dim))], limit=1)
            if not plan:
                plan = Plan.create({
                    "name": sa.dimension_name(self.env, analysis.selected, dim),
                    "l10n_se_sie_dimension": int(dim),
                })
            existing = {
                a.code: a for a in Analytic.search([
                    ("plan_id", "=", plan.id), ("company_id", "in", [company.id, False])])
            }
            for code in sorted(used[dim]):
                account = existing.get(code)
                if not account:
                    account = Analytic.create({
                        "name": sa.object_name(analysis.selected, dim, code),
                        "code": code,
                        "plan_id": plan.id,
                        "company_id": company.id,
                    })
                    self.created_analytic_ids = [Command.link(account.id)]
                analysis.analytic[(dim, code)] = account.id

    def _distribution(self, analysis, objects):
        ids = []
        seen = set()
        for dim, code in objects:
            if dim in seen:
                continue  # Odoo has one account per plan in a distribution
            account_id = analysis.analytic.get((dim, code))
            if account_id:
                seen.add(dim)
                ids.append(account_id)
        if not ids:
            return None
        return {",".join(str(i) for i in sorted(ids)): 100}

    def _line(self, account, amount, name, distribution=None):
        amount = float(amount)
        return {
            "account_id": account.id,
            "name": name,
            "debit": amount if amount > 0 else 0.0,
            "credit": -amount if amount < 0 else 0.0,
            # set even when empty: Odoo would otherwise apply analytic distribution models
            "analytic_distribution": distribution or False,
        }

    def _create_entries(self, analysis, items):
        Move = self.env["account.move"].with_context(
            check_move_validity=False, skip_invoice_sync=True)
        pending = []

        def flush():
            if not pending:
                return
            moves = Move.create(pending)
            pending.clear()
            if self.post_moves:
                moves.with_context(check_move_validity=True)._post(soft=False)
            # what the entry looked like when imported: an entry changed afterwards blocks undo
            for move in moves:
                move.l10n_se_sie_fingerprint = move._l10n_se_sie_fingerprint()

        for item in items:
            if item.kind == "opening_diff":
                flush()
                vals = self._opening_diff_vals(analysis, item)
            elif item.kind == "opening":
                vals = self._opening_vals(analysis, item)
            else:
                vals = self._voucher_vals(analysis, item)
            if vals:
                pending.append(vals)
            if len(pending) >= 100:
                flush()
        flush()

    def _move_vals(self, item, lines):
        """An entry's values; refused when the rounded lines do not balance (with
        check_move_validity off, Odoo would accept an unbalanced draft)."""
        digits = self.company_id.currency_id.decimal_places
        total = sum((sa.round2(Decimal(repr(line["debit"])) - Decimal(repr(line["credit"])),
                               digits) for line in lines), Decimal(0))
        if total:
            raise UserError(self.env._(
                "%(entry)s does not balance after rounding to %(digits)s decimals (difference "
                "%(diff)s).", entry=item.ref, digits=digits, diff=sie.format_amount(total)))
        return {
            "move_type": "entry",
            "date": item.date,
            "ref": item.ref[:1000],
            "journal_id": self._journal_for(item).id,
            "company_id": self.company_id.id,
            "l10n_se_sie_key": item.key,
            "l10n_se_sie_import_id": self.id,
            "line_ids": [Command.create(line) for line in lines],
        }

    def _voucher_vals(self, analysis, item):
        v = item.voucher
        lines = []
        for t in v.transactions:
            if not t.amount:
                continue
            distribution = (self._distribution(analysis, t.objects)
                            if self.import_analytics else None)
            lines.append(self._line(analysis.accounts[t.account],
                                    sa.round2(t.amount, analysis.digits),
                                    t.text or v.text or item.ref, distribution))
        return self._move_vals(item, lines)

    def _opening_vals(self, analysis, item):
        name = self.env._("Opening balance %(year)s", year=item.year.label)
        lines = []
        for code, amount in sorted(item.year.opening.items()):
            amount = sa.round2(amount, analysis.digits)
            if amount:
                lines.append(self._line(analysis.accounts[code], amount, name))
        return self._move_vals(item, lines) if lines else None

    def _opening_diff_vals(self, analysis, item):
        """The differences the preview showed, booked only if the books before the year are
        still what they were compared with (the previous year's #UB, or Odoo's balances when the
        preview was made)."""
        year = item.year
        current = {c: b for c, b in sa.odoo_balances(self.env, self.company_id, year.start).items()
                   if sie.account_class(c) == "balance"}
        expected = item.expected_before
        mismatches = []
        for code in sorted(set(current) | set(expected)):
            have = sa.round2(current.get(code, Decimal(0)), analysis.digits)
            want = sa.round2(expected.get(code, Decimal(0)), analysis.digits)
            if have != want:
                mismatches.append(self.env._("%(account)s: Odoo %(odoo)s, %(prev)s %(file)s",
                                             account=code, odoo=sie.format_amount(have),
                                             prev=item.expected_label,
                                             file=sie.format_amount(want)))
        if mismatches:
            raise UserError(self.env._(
                "The books before %(date)s do not agree with %(prev)s (the closing balance #UB "
                "of the year before), so the opening balance differences of %(year)s cannot be "
                "booked. Accounts that differ:\n%(accounts)s",
                date=year.start, prev=item.expected_label, year=year.label,
                accounts="\n".join(mismatches[:30])))
        name = self.env._("Opening balance difference %(year)s", year=year.label)
        accounts = dict(analysis.accounts)
        lines = []
        for code, diff in sorted(item.diffs.items()):
            account = accounts.get(code) or sa.account_map(self.env, self.company_id).get(code)
            lines.append(self._line(account, diff, name))
        return self._move_vals(item, lines) if lines else None

    # -- the check against the files ---------------------------------------------------------------

    def _odoo_balances(self, date_to, date_from=None):
        """{code: balance} of the company's (and its branches') entries up to ``date_to``, from
        ``date_from`` if given. Posted entries, plus drafts when the import does not post."""
        states = ("posted",) if self.post_moves or self.mode == "reconcile" else (
            "posted", "draft")
        domain = [*sa.tree(self.company_id), ("parent_state", "in", states),
                  ("date", "<=", date_to)]
        if date_from:
            domain.append(("date", ">=", date_from))
        groups = self.env["account.move.line"]._read_group(
            domain, ["account_id"], ["balance:sum"])
        out = {}
        for account, balance in groups:
            code = account.with_company(self.company_id).code
            out[code] = out.get(code, Decimal(0)) + sa.to_decimal(balance)
        return out

    def _finish(self):
        self._check()
        self._check_later_imports()
        self.write({"state": "done", "date_done": fields.Datetime.now()})
        self.message_post(body=self.env._(
            "Import done: %(entries)s entries, %(accounts)s accounts checked, %(deviations)s "
            "deviations.", entries=len(self.move_ids), accounts=self.checked_count,
            deviations=self.deviation_count))

    def _check_later_imports(self):
        """Adding a year changes the balances of every later year: imports and reconciliations
        of later years are checked again, and the result is shown here."""
        if not self.year_ids:
            return
        first = min(self.year_ids.mapped("date_start"))
        others = self.search([
            *sa.tree(self.company_id), ("id", "!=", self.id),
            ("state", "in", ("done", "reconciled")),
        ]).filtered(lambda r: any(y.date_start > first for y in r.year_ids))
        result = []
        for other in others.sorted("id"):
            other._env()._check()
            result.append({
                "id": other.id,
                "name": other.name,
                "years": ", ".join(other.year_ids.mapped("label")),
                "checked": other.checked_count,
                "deviations": other.deviation_count,
                "unchecked": len(other.year_ids.filtered(lambda y: y.status == "nobalances")),
            })
        self.later_checks = result

    def _run_reconcile(self):
        """Reconciliation only: compare the books with the files, create nothing."""
        self.ensure_one()
        self._env()._check()
        self.write({"state": "reconciled", "date_done": fields.Datetime.now()})
        self.message_post(body=self.env._(
            "Reconciliation: %(accounts)s accounts checked, %(deviations)s deviations.",
            accounts=self.checked_count, deviations=self.deviation_count))

    def action_check_again(self):
        for rec in self:
            if rec.state not in ("done", "reconciled"):
                raise UserError(self.env._("Only a finished import or reconciliation can be "
                                           "checked again."))
            rec._env()._check()
        return True

    def _check(self):
        """Compare Odoo with the files, per selected financial year: balance sheet accounts up to
        the date (the year's end: #UB), result accounts from the first day of the year (#RES).
        On a date within the year the file's figures are its opening balance plus #PSALDO up to
        that month end, or plus its vouchers up to that date."""
        self.ensure_one()
        years, _problems = sa.collect_years(self.env, self.company_id, self._files())
        by_key = {y.key: y for y in years}
        names = {code: acc.name for code, acc in sa.account_map(self.env, self.company_id).items()}
        self.check_ids.unlink()
        vals = []
        for year_rec in self.year_ids:
            year = by_key.get(year_rec.key)
            as_of = self.check_date if (
                self.check_date and year_rec.date_start <= self.check_date <= year_rec.date_end
            ) else None
            if not year:
                year_rec.write({"status": "nobalances", "checked_count": 0,
                                "deviation_count": 0, "note": False})
                continue
            try:
                balances, results, source = sa.expected_balances(self.env, year, as_of)
            except UserError as exc:
                year_rec.write({"status": "nobalances", "checked_count": 0,
                                "deviation_count": 0, "note": sa.safe_text(str(exc))})
                continue
            day = as_of or year.end
            to_date = self._odoo_balances(day)
            in_year = self._odoo_balances(day, date_from=year.start)
            codes = set(balances) | set(results) | set(to_date) | set(in_year)
            year_vals = []
            for code in sorted(codes):
                if code in results:
                    kind = "result"
                elif code in balances:
                    kind = "balance"
                else:
                    kind = "balance" if sie.account_class(code) == "balance" else "result"
                expected = sa.round2((results if kind == "result" else balances).get(
                    code, Decimal(0)))
                actual = sa.round2((in_year if kind == "result" else to_date).get(
                    code, Decimal(0)))
                if not expected and not actual:
                    continue
                sie_account = year.parsed.accounts.get(code)
                year_vals.append({
                    "import_id": self.id,
                    "year": year.label,
                    "date": day,
                    "kind": kind,
                    "account_code": code,
                    "account_name": sa.safe_text(
                        names.get(code) or (sie_account.name if sie_account else "")),
                    "sie_amount": float(expected),
                    "odoo_amount": float(actual),
                })
            deviations = sum(1 for v in year_vals if v["sie_amount"] != v["odoo_amount"])
            year_rec.write({
                "status": "deviations" if deviations else "ok",
                "checked_count": len(year_vals),
                "deviation_count": deviations,
                "check_date": day,
                "source": source,
                "note": False,
                "created_count": len(self.move_ids.filtered(
                    lambda m, y=year: y.start <= m.date <= y.end)),
            })
            vals.extend(year_vals)
        self.env["l10n_se.sie.import.check"].create(vals)
        self.write({
            "checked_count": len(vals),
            "deviation_count": sum(1 for v in vals if v["sie_amount"] != v["odoo_amount"]),
        })

    @api.depends("state", "checked_count", "deviation_count", "later_checks", "year_ids.status",
                 "year_ids.check_date", "year_ids.source", "year_ids.checked_count",
                 "year_ids.deviation_count", "year_ids.note")
    def _compute_report_html(self):
        _ = self.env._
        sources = dict(self.env["l10n_se.sie.import.year"]._fields["source"]._description_selection(
            self.env))
        for rec in self:
            if rec.state not in ("done", "reconciled"):
                rec.report_html = False
                continue
            later = rec.later_checks or []
            unchecked = rec.year_ids.filtered(lambda y: y.status == "nobalances")
            later_deviations = sum(c["deviations"] for c in later)
            later_unchecked = sum(c["unchecked"] for c in later)
            h = Markup()
            if rec.deviation_count or later_deviations:
                h += Markup("<div class='alert alert-warning' role='alert'>%s</div>") % _(
                    "%(count)s of %(total)s accounts differ from the files. The Reconciliation "
                    "tab lists them (filter 'Deviations').", count=rec.deviation_count,
                    total=rec.checked_count)
            elif rec.checked_count and not unchecked and not later_unchecked:
                h += Markup("<div class='alert alert-success' role='alert'>%s</div>") % _(
                    "All %(count)s accounts agree with the files.", count=rec.checked_count)
            elif rec.checked_count:
                h += Markup("<div class='alert alert-info' role='alert'>%s</div>") % _(
                    "%(count)s accounts agree with the files, but not every year could be "
                    "checked: %(years)s.", count=rec.checked_count,
                    years=", ".join(unchecked.mapped("label")) or "-")
            else:
                h += Markup("<div class='alert alert-info' role='alert'>%s</div>") % _(
                    "The files have no balances to compare with.")
            h += Markup("<ul>")
            for year in rec.year_ids:
                if year.status == "nobalances":
                    h += Markup("<li>%s</li>") % _("%(year)s: not checked. %(note)s",
                                                  year=year.label, note=year.note or "")
                else:
                    h += Markup("<li>%s</li>") % _(
                        "%(year)s, as of %(date)s (%(basis)s): %(checked)s accounts, "
                        "%(deviations)s deviations.", year=year.label, date=year.check_date,
                        basis=sources.get(year.source, year.source),
                        checked=year.checked_count, deviations=year.deviation_count)
            h += Markup("</ul>")
            if later:
                h += Markup("<p>%s</p><ul>") % _(
                    "Later years already in Odoo, checked again after this import:")
                for c in later:
                    h += Markup("<li>%s</li>") % _(
                        "%(name)s (%(years)s): %(checked)s accounts, %(deviations)s deviations, "
                        "%(unchecked)s years not checked.", name=c["name"], years=c["years"],
                        checked=c["checked"], deviations=c["deviations"],
                        unchecked=c["unchecked"])
                h += Markup("</ul>")
            h += Markup("<p class='text-muted'>%s</p>") % _(
                "Balance sheet accounts are compared with their balance on the date, result "
                "accounts with their total from the first day of the financial year, using posted "
                "entries. A year-end closing in the source program (e.g. 8999/2099) is a voucher "
                "like any other, so Odoo, which does not close years, agrees with the files either "
                "way.")
            rec.report_html = h

    # -- background --------------------------------------------------------------------------------

    @api.model
    def _cron_process(self):
        """Background imports and undos. An import does batches until CRON_BUDGET seconds have
        passed since the job started (the scheduler calls again while work is left, at least ten
        times per run, see ir.cron), then hands over. An undo is one transaction."""
        in_cron = bool(self.env.context.get("ir_cron_progress_id"))
        Cron = self.env["ir.cron"]

        def progress(done, remaining):
            if in_cron:
                Cron._commit_progress(done, remaining=remaining)

        busy = ("queued", "running", "undoing")
        rec = self.search([("state", "in", busy)], order="id", limit=1)
        if not rec:
            progress(0, 0)
            return
        pending = self.search_count([("state", "in", busy)])
        end = self.env.context.get("cron_end_time")
        started = time.monotonic() if end is None else end - MIN_TIME_PER_JOB
        if end is not None and time.monotonic() - started > CRON_BUDGET:
            progress(0, pending)
            return
        if rec.state == "undoing":
            rec._cron_undo(in_cron)
            progress(1, pending - 1)
            return
        try:
            with self.env.cr.savepoint():
                left = rec.with_user(rec.user_id)._run(CRON_BATCH_SIZE, started + CRON_BUDGET)
        except Exception as exc:  # noqa: BLE001 - the run is marked failed, not retried
            _logger.exception("SIE import %s failed", rec.id)
            rec.write({"state": "failed", "error_message": str(exc)})
            rec.message_post(body=self.env._("The run failed: %(error)s", error=str(exc)))
            left = 0
        progress(1, (pending - 1) + (1 if left else 0))

    def _cron_undo(self, in_cron):
        """The whole undo in one transaction. The attempt is counted (and committed) first: an
        attempt killed by the server's time limit is rolled back, deletes nothing, and after
        UNDO_ATTEMPTS the undo is marked failed instead of being retried forever."""
        self.undo_attempts += 1
        if in_cron:
            self.env.cr.commit()
        if self.undo_attempts > UNDO_ATTEMPTS:
            self.write({"state": "failed", "error_message": self.env._(
                "The undo did not finish within the server's time limit for scheduled actions "
                "(limit_time_real_cron). Nothing was deleted. Raise the limit and undo again.")})
            return
        try:
            with self.env.cr.savepoint():
                self.with_user(self.user_id)._env()._undo_all()
        except Exception as exc:  # noqa: BLE001 - nothing was deleted (savepoint rolled back)
            _logger.exception("SIE undo %s failed", self.id)
            self.write({"state": "failed", "error_message": str(exc)})
            self.message_post(body=self.env._(
                "The undo failed; nothing was deleted: %(error)s", error=str(exc)))

    def action_stop(self):
        for rec in self.filtered(lambda r: r.state in ("queued", "running", "undoing")):
            rec.write({"state": "failed",
                       "error_message": self.env._("Stopped by %(user)s.", user=self.env.user.name)})
        return True

    def action_refresh(self):
        return True

    # -- undo --------------------------------------------------------------------------------------

    def _undo_blockers(self):
        """Why the entries cannot be deleted. Empty when undo is possible. An entry blocks when
        it is in a locked period, secured by a hash, kept by the restrictive audit trail,
        reversed, a bank statement entry, reconciled, or changed since the import."""
        self.ensure_one()
        _ = self.env._
        moves = self._env().move_ids
        lock_dates = {}
        for move in moves:
            ck = (move.company_id.id, move.journal_id.id)
            if ck not in lock_dates:
                lock_dates[ck] = move.company_id._get_user_fiscal_lock_date(move.journal_id)
        reasons = []
        for move in moves:
            label = move.name if move.name and move.name != "/" else move.ref
            lines = move.line_ids
            if move.date <= lock_dates[(move.company_id.id, move.journal_id.id)]:
                reasons.append(_("%(entry)s (%(date)s) is in a locked period", entry=label,
                                 date=move.date))
            elif move.inalterable_hash:
                reasons.append(_("%(entry)s is secured by a hash", entry=label))
            elif move.posted_before and move.company_id.restrictive_audit_trail:
                reasons.append(_("%(entry)s is kept by the restrictive audit trail",
                                 entry=label))
            elif move.reversal_move_ids or move.reversed_entry_id:
                reasons.append(_("%(entry)s has been reversed", entry=label))
            elif move.statement_line_id or lines.statement_line_id:
                reasons.append(_("%(entry)s belongs to a bank statement line", entry=label))
            elif lines.matched_debit_ids or lines.matched_credit_ids or lines.full_reconcile_id:
                reasons.append(_("%(entry)s is reconciled", entry=label))
            elif move.l10n_se_sie_fingerprint != move._l10n_se_sie_fingerprint():
                reasons.append(_("%(entry)s has been changed since the import", entry=label))
        return reasons

    def action_undo(self):
        """Delete the entries of the import, and the accounts, analytic accounts and journals it
        created when nothing else uses them. All or nothing: everything is checked first, in the
        same transaction as the deletion."""
        self.ensure_one()
        if self.state not in ("done", "failed"):
            raise UserError(self.env._("Only a finished or failed import can be undone."))
        rec = self._env()
        rec._raise_undo_blockers()
        if len(rec.move_ids) > UNDO_SYNC_LIMIT:
            rec.write({"state": "undoing", "undo_attempts": 0, "error_message": False})
            rec.message_post(body=self.env._(
                "%(count)s entries are deleted in the background. Refresh the page to follow "
                "the progress.", count=len(rec.move_ids)))
            self.env.ref("l10n_se_sie4.ir_cron_l10n_se_sie_import")._trigger()
            return True
        rec._undo_all()
        return True

    def _raise_undo_blockers(self):
        blockers = self._undo_blockers()
        if blockers:
            raise UserError(self.env._(
                "The import cannot be undone because %(count)s of its entries cannot be deleted "
                "any more:\n%(list)s\n\nReverse the entries instead (Journal Entries, Reverse).",
                count=len(blockers), list="\n".join(blockers[:20])))

    def _undo_all(self):
        self._raise_undo_blockers()
        moves = self.move_ids
        count = len(moves)
        moves.filtered(lambda m: m.state in ("posted", "cancel")).button_draft()
        moves.unlink()
        removed = self._remove_created()
        self.check_ids.unlink()
        self.write({"state": "undone", "deviation_count": 0, "checked_count": 0,
                    "undone_count": count, "later_checks": False})
        self.message_post(body=self.env._(
            "Import undone by %(user)s: %(count)s entries deleted; accounts removed: "
            "%(accounts)s.", user=self.env.user.name, count=count,
            accounts=", ".join(removed) or "-"))

    def _remove_created(self):
        """Remove what the import created and nothing uses any more. Returns the removed
        account codes."""
        removed = []
        for account in self.created_account_ids:
            if not self._referenced(account):
                try:
                    with self.env.cr.savepoint():
                        code = account.code
                        account.unlink()
                        removed.append(code)
                except Exception:  # noqa: BLE001 - kept when Odoo refuses
                    _logger.info("SIE undo %s keeps account %s", self.id, account.id)
        for analytic in self.created_analytic_ids.sudo():
            if not self._referenced(analytic) and not self._in_distributions(analytic):
                try:
                    with self.env.cr.savepoint():
                        analytic.unlink()
                except Exception:  # noqa: BLE001 - kept when Odoo refuses
                    _logger.info("SIE undo %s keeps analytic account %s", self.id, analytic.id)
        for journal in self.created_journal_ids:
            if not self._referenced(journal):
                try:
                    with self.env.cr.savepoint():
                        journal.unlink()
                except Exception:  # noqa: BLE001 - kept when Odoo refuses
                    _logger.info("SIE undo %s keeps journal %s", self.id, journal.id)
        return removed

    def _referenced(self, record):
        """Whether any other record points to ``record``: every stored or company-dependent
        many2one/many2many field to its model, archived records included (this module's own
        records do not count)."""
        env = self.env(su=True)
        fields_ = env["ir.model.fields"].search([
            ("relation", "=", record._name), ("ttype", "in", ("many2one", "many2many"))])
        for field_rec in fields_:
            if field_rec.model.startswith("l10n_se.sie."):
                continue
            Model = env.get(field_rec.model)
            if Model is None or Model._transient or Model._abstract or not Model._auto:
                continue
            field = Model._fields.get(field_rec.name)
            if field is None or not (field.store or field.company_dependent):
                continue
            Model = Model.with_context(active_test=False)
            if Model.search_count([(field_rec.name, "in", record.ids)], limit=1):
                return True
        return False

    def _in_distributions(self, analytic):
        """Whether an analytic distribution (JSON, no foreign key) anywhere names ``analytic``."""
        env = self.env(su=True)
        for name in env.registry:
            Model = env[name]
            field = Model._fields.get("analytic_distribution")
            if (field is None or not field.store or Model._transient or Model._abstract
                    or not hasattr(Model, "_query_analytic_accounts")):
                continue
            if Model.with_context(active_test=False).search_count(
                    [("analytic_distribution", "in", analytic.ids)], limit=1):
                return True
        return False

    def action_open_moves(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Imported Entries"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("l10n_se_sie_import_id", "=", self.id)],
            "context": {"create": False},
        }

    def action_open_checks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Reconciliation: %(name)s", name=self.name),
            "res_model": "l10n_se.sie.import.check",
            "view_mode": "list",
            "domain": [("import_id", "=", self.id)],
            "context": {"search_default_deviation": 1 if self.deviation_count else 0,
                        "search_default_group_year": 1},
        }

    @api.ondelete(at_uninstall=False)
    def _unlink_only_without_entries(self):
        if self.filtered(lambda r: r.move_ids):
            raise UserError(self.env._("Undo the import before deleting it."))


class L10nSeSieImportYear(models.Model):
    _name = "l10n_se.sie.import.year"
    _description = "SIE Import: Financial Year"
    _order = "date_start"

    import_id = fields.Many2one("l10n_se.sie.import", required=True, ondelete="cascade",
                                index=True)
    key = fields.Char(required=True)
    label = fields.Char(string="Year", required=True)
    date_start = fields.Date(string="From", required=True)
    date_end = fields.Date(string="To", required=True)
    filename = fields.Char(string="File")
    voucher_count = fields.Integer(string="Vouchers in File")
    created_count = fields.Integer(string="Entries Created")
    existing_count = fields.Integer(string="Already in Odoo")
    checked_count = fields.Integer(string="Accounts Checked")
    deviation_count = fields.Integer(string="Deviations")
    status = fields.Selection(
        [("ok", "Agrees"), ("deviations", "Deviations"), ("nobalances", "Not checked")],
        string="Check",
    )
    check_date = fields.Date(string="Compared As Of")
    source = fields.Selection(
        [
            ("closing", "closing balances #UB/#RES"),
            ("psaldo", "opening balance and monthly balances #PSALDO"),
            ("vouchers", "opening balance and vouchers"),
        ],
        string="Compared With",
    )
    note = fields.Char()


class L10nSeSieImportSeries(models.Model):
    _name = "l10n_se.sie.import.series"
    _description = "SIE Import: Journal per Series"
    _order = "series"

    import_id = fields.Many2one("l10n_se.sie.import", required=True, ondelete="cascade",
                                index=True)
    series = fields.Char(required=True)
    voucher_count = fields.Integer(string="Vouchers")
    journal_id = fields.Many2one("account.journal")


class L10nSeSieImportCheck(models.Model):
    _name = "l10n_se.sie.import.check"
    _description = "SIE Import: Reconciliation Line"
    _order = "year, account_code"

    import_id = fields.Many2one("l10n_se.sie.import", required=True, ondelete="cascade",
                                index=True)
    company_id = fields.Many2one(related="import_id.company_id", store=True)
    currency_id = fields.Many2one(related="import_id.currency_id")
    year = fields.Char(required=True)
    date = fields.Date(string="As Of")
    kind = fields.Selection(
        [("balance", "Balance sheet"), ("result", "Result")], required=True
    )
    account_code = fields.Char(string="Account", required=True)
    account_name = fields.Char(string="Name")
    sie_amount = fields.Monetary(string="In the File")
    odoo_amount = fields.Monetary(string="In Odoo")
    difference = fields.Monetary(compute="_compute_difference", store=True,
                                 help="Odoo minus the file.")
    is_deviation = fields.Boolean(string="Deviation", compute="_compute_difference", store=True)

    @api.depends("sie_amount", "odoo_amount")
    def _compute_difference(self):
        for rec in self:
            rec.difference = rec.odoo_amount - rec.sie_amount
            rec.is_deviation = rec.currency_id.compare_amounts(
                rec.odoo_amount, rec.sie_amount) != 0 if rec.currency_id else bool(
                round(rec.difference, 2))
