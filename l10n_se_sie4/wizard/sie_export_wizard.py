import base64
import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import api, fields, models, release
from odoo.addons.l10n_se_sie4.lib import sie
from odoo.exceptions import UserError

from ..models.sie_analysis import orgnr_digits, round2, to_decimal

SIE_TYPES = [
    ("4E", "Type 4E: balances and vouchers (standard)"),
    ("4I", "Type 4I: vouchers only, for import into another program"),
    ("1", "Type 1: closing balances"),
    ("2", "Type 2: closing and monthly balances"),
    ("3", "Type 3: balances per object as well"),
]
KTYP = {"asset": "T", "liability": "S", "equity": "S", "income": "I", "expense": "K"}


class L10nSeSieExportWizard(models.TransientModel):
    """The books of one financial year (or a date range) as an SIE file, for the auditor or a
    tax return program. Posted entries only."""

    _name = "l10n_se.sie.export.wizard"
    _description = "SIE Export"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, default=lambda self: self.env.company
    )
    date_from = fields.Date(string="From", required=True,
                            default=lambda self: self._default_dates()["date_from"])
    date_to = fields.Date(string="To", required=True,
                          default=lambda self: self._default_dates()["date_to"])
    sie_type = fields.Selection(SIE_TYPES, string="SIE Type", default="4E", required=True)
    journal_ids = fields.Many2many(
        "account.journal", string="Only Journals", check_company=True,
        help="Only vouchers of these journals. The balances always include every journal, so "
        "with a filter the vouchers do not add up to the balances.",
    )
    chart_type = fields.Selection(related="company_id.l10n_se_sie_chart_type", readonly=False)
    all_accounts = fields.Boolean(
        string="All Active Accounts", default=True,
        help="Write the whole chart of accounts, not only the accounts with amounts.",
    )
    checksum = fields.Boolean(
        string="Checksum (#KSUMMA)",
        help="Add the optional SIE checksum (CRC-32). Leave it off if the receiving program "
        "rejects the file.",
    )
    result_account_id = fields.Many2one(
        "account.account", string="Account for Earlier Years' Result", check_company=True,
        domain="[('account_type', '=', 'equity')]",
        default=lambda self: self._default_result_account(),
        help="Odoo does not close financial years: the result of earlier years is still on the "
        "income and expense accounts. The SIE file needs it on an equity account in the opening "
        "balance; it goes on this account (BAS 2099 by default).",
    )
    state = fields.Selection([("draft", "Draft"), ("done", "Done")], default="draft")
    file_data = fields.Binary(string="SIE File", readonly=True, attachment=False)
    file_name = fields.Char(readonly=True)
    summary_html = fields.Html(string="Summary", readonly=True, sanitize=False)

    @api.model
    def _default_dates(self):
        today = fields.Date.context_today(self)
        dates = self.env.company.compute_fiscalyear_dates(today - relativedelta(years=1))
        return dates

    @api.model
    def _default_result_account(self):
        Account = self.env["account.account"].with_company(self.env.company)
        return Account.search([*Account._check_company_domain(self.env.company),
                               ("code", "=", "2099")], limit=1)

    # -- collecting ------------------------------------------------------------------------------

    def _balances(self, domain, groupby=("account_id",)):
        groups = self.env["account.move.line"]._read_group(domain, list(groupby), ["balance:sum"])
        return groups

    def _period(self):
        return self.date_from, self.date_to, self.date_from - relativedelta(years=1), \
            self.date_from - timedelta(days=1)

    def _opening_moves(self, env):
        """Opening balance entries: the company's opening move and the opening balances (and
        their corrections) of SIE imports. On the first day of a period they belong to its
        opening balance (#IB), not to its movements or vouchers."""
        company = self.company_id
        moves = env["account.move"].search([
            ("company_id", "=", company.id),
            "|", ("l10n_se_sie_key", "=like", "%|IB|"), ("l10n_se_sie_key", "=like", "%|IBDIFF|"),
        ]) | company.account_opening_move_id
        return moves.ids

    def _in_period(self, start, end, opening_ids):
        """Domain: dated in the period, without the opening entries of its first day."""
        return [("date", ">=", start), ("date", "<=", end),
                "!", "&", ("date", "=", start), ("move_id", "in", opening_ids)]

    def _before(self, start, opening_ids):
        """Domain: before the period, with the opening entries of its first day."""
        return ["|", ("date", "<", start), "&", ("date", "=", start),
                ("move_id", "in", opening_ids)]

    def _kind(self, account):
        group = account.internal_group
        if group in ("asset", "liability", "equity"):
            return "balance"
        if group in ("income", "expense"):
            return "result"
        return None

    def _build(self):
        """The SieFile to write and the warnings for the summary."""
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(self.env._("The period ends before it starts."))
        company = self.company_id
        # company-dependent account codes are read in the exported company
        self = self.with_company(company).with_context(allowed_company_ids=company.ids)
        env = self.env
        warnings = []
        date_from, date_to, prev_from, prev_to = self._period()
        balances_wanted = self.sie_type != "4I"
        # Account names in Swedish when the language is installed (the file is for Swedish
        # auditors and programs), else in the user's language.
        lang = "sv_SE" if env["res.lang"]._lang_get("sv_SE") else env.lang
        Account = env["account.account"].with_company(company).with_context(lang=lang)
        accounts = Account.with_context(active_test=False).search(
            Account._check_company_domain(company))
        code_of = {a.id: a.code for a in accounts}
        base = [("company_id", "=", company.id), ("parent_state", "=", "posted")]
        opening_ids = self._opening_moves(env)

        def sums(domain):
            out = defaultdict(Decimal)
            for account, balance in self._balances(base + domain):
                out[account] += to_decimal(balance)
            return out

        out = sie.SieFile(
            flag="0", program="Odoo", program_version=release.major_version,
            generated=fields.Date.context_today(self), sie_type=self.sie_type[0],
            company_id=str(company.id), orgnr=self._orgnr(), company_name=company.name,
            address=self._address(), chart_type=self.chart_type or "EUBAS97",
            currency=company.currency_id.name,
        )
        out.fiscal_years[0] = sie.FiscalYear(0, date_from, date_to)
        if balances_wanted:
            out.fiscal_years[-1] = sie.FiscalYear(-1, prev_from, prev_to)
        if self.sie_type in ("2", "3"):
            out.balances_until = date_to
        used = set()

        if balances_wanted:
            for idx, start, end in ((0, date_from, date_to), (-1, prev_from, prev_to)):
                before = sums(self._before(start, opening_ids))
                moved = sums(self._in_period(start, end, opening_ids))
                unclosed = Decimal(0)
                opening, closing, results = defaultdict(Decimal), defaultdict(Decimal), {}
                for account in set(before) | set(moved):
                    kind = self._kind(account)
                    if kind is None:
                        if before.get(account) or moved.get(account):
                            warnings.append(self.env._(
                                "Account %(account)s is an off-balance account and is left "
                                "out.", account=account.display_name))
                        continue
                    if kind == "result":
                        unclosed += before.get(account, Decimal(0))
                        if moved.get(account):
                            results[account.code] = moved[account]
                    else:
                        opening[account.code] += before.get(account, Decimal(0))
                        closing[account.code] += before.get(account, Decimal(0)) + moved.get(
                            account, Decimal(0))
                if unclosed:
                    if not self.result_account_id:
                        raise UserError(self.env._(
                            "The result of the years before %(date)s (%(amount)s) is not closed "
                            "to equity in Odoo. Choose the account for earlier years' result.",
                            date=start, amount=sie.format_amount(unclosed)))
                    code = self.result_account_id.code
                    opening[code] += unclosed
                    closing[code] += unclosed
                    if idx == 0:
                        warnings.append(self.env._(
                            "The result of the years before %(date)s, %(amount)s, is not closed "
                            "to equity in Odoo; the file has it on account %(account)s in the "
                            "opening balance.", date=start, amount=sie.format_amount(unclosed),
                            account=code))
                for code, amount in sorted(opening.items()):
                    if amount:
                        out.opening.append(sie.Balance(idx, code, round2(amount)))
                        used.add(code)
                for code, amount in sorted(closing.items()):
                    if amount:
                        out.closing.append(sie.Balance(idx, code, round2(amount)))
                        used.add(code)
                for code, amount in sorted(results.items()):
                    if amount:
                        out.results.append(sie.Balance(idx, code, round2(amount)))
                        used.add(code)
            if self.sie_type in ("2", "3", "4E"):
                for idx, start, end in ((-1, prev_from, prev_to), (0, date_from, date_to)):
                    groups = self._balances(base + self._in_period(start, end, opening_ids),
                                            ("account_id", "date:month"))
                    rows = defaultdict(Decimal)
                    for account, month, balance in groups:
                        if self._kind(account) is None:
                            continue
                        rows[(month.strftime("%Y%m"), account.code)] += to_decimal(balance)
                    for (period, code), amount in sorted(rows.items()):
                        if amount:
                            out.period_balances.append(sie.PeriodBalance(
                                idx, period, code, round2(amount)))
                            used.add(code)

        dims = _Dimensions(env)
        if self.sie_type == "3":
            self._object_balances(env, out, base, dims, used, opening_ids)
        if self.sie_type in ("4E", "4I"):
            self._vouchers(env, out, dims, used, code_of, opening_ids)
            if self.journal_ids and balances_wanted:
                warnings.append(self.env._(
                    "Only the vouchers of the chosen journals are in the file; the balances "
                    "include all journals, so the vouchers do not add up to them."))

        # chart of accounts
        bad = sorted(c for c in used if not c.isdigit())
        if bad:
            raise UserError(self.env._(
                "SIE needs numeric account numbers. Change these accounts: %(accounts)s.",
                accounts=", ".join(bad)))
        for account in accounts.sorted("code"):
            code = account.code
            if not code or not code.isdigit() or self._kind(account) is None:
                continue
            if code in used or (self.all_accounts and account.active):
                out.accounts[code] = sie.Account(code, account.name, KTYP[account.internal_group])
        for (dim, code), (name, plan_name) in sorted(dims.objects.items()):
            out.dimensions.setdefault(dim, sie.Dimension(dim, plan_name))
            out.objects[(dim, code)] = sie.SieObject(dim, code, name)
        return out, warnings

    def _object_balances(self, env, out, base, dims, used, opening_ids):
        """Type 3: #OIB/#OUB for balance sheet accounts and #PSALDO per object."""
        date_from, date_to, prev_from, prev_to = self._period()
        lines = env["account.move.line"].search_fetch(
            base + [("date", "<=", date_to), ("analytic_distribution", "!=", False)],
            ["account_id", "date", "balance", "analytic_distribution", "move_id"])
        opening_balance = defaultdict(Decimal)
        closing = defaultdict(Decimal)
        period = defaultdict(Decimal)
        for line in lines:
            kind = self._kind(line.account_id)
            if kind is None:
                continue
            code = line.account_id.code
            opening = line.move_id.id in opening_ids
            for objects, amount in dims.split(to_decimal(line.balance),
                                              line.analytic_distribution):
                for pair in objects:
                    if kind == "balance":
                        closing[(code, pair)] += amount
                        if line.date < date_from or (opening and line.date == date_from):
                            opening_balance[(code, pair)] += amount
                    for idx, start, end in ((0, date_from, date_to), (-1, prev_from, prev_to)):
                        if start <= line.date <= end and not (opening and line.date == start):
                            period[(idx, line.date.strftime("%Y%m"), code, pair)] += amount
        for (code, pair), amount in sorted(opening_balance.items()):
            if amount:
                out.object_opening.append(sie.Balance(0, code, round2(amount), None, (pair,)))
                used.add(code)
        for (code, pair), amount in sorted(closing.items()):
            if amount:
                out.object_closing.append(sie.Balance(0, code, round2(amount), None, (pair,)))
                used.add(code)
        for (idx, month, code, pair), amount in sorted(period.items()):
            if amount:
                out.period_balances.append(sie.PeriodBalance(idx, month, code, round2(amount),
                                                             None, (pair,)))
                used.add(code)

    def _vouchers(self, env, out, dims, used, code_of, opening_ids):
        date_from, date_to = self.date_from, self.date_to
        domain = [("company_id", "=", self.company_id.id), ("state", "=", "posted"),
                  ("date", ">=", date_from), ("date", "<=", date_to)]
        if self.journal_ids:
            domain.append(("journal_id", "in", self.journal_ids.ids))
        domain += ["!", "&", ("date", "=", date_from), ("id", "in", opening_ids)]
        moves = env["account.move"].search(domain, order="date, name, id")
        lines = env["account.move.line"].search_fetch(
            [("move_id", "in", moves.ids), ("account_id", "!=", False)],
            ["move_id", "account_id", "balance", "name", "analytic_distribution"],
            order="move_id, id")
        by_move = defaultdict(list)
        for line in lines:
            by_move[line.move_id.id].append(line)
        per_series = defaultdict(list)
        for move in moves:
            series = re.sub(r"\s+", "", move.journal_id.code or "") or "A"
            per_series[series].append(move)
        for series in sorted(per_series):
            for number, move in enumerate(per_series[series], 1):
                ref = _clean_ref(move.ref)
                text = move.name or ""
                if ref and ref != text:
                    text = f"{text} {ref}".strip()
                same = {text, ref, move.name, move.ref}
                voucher = sie.Voucher(series, str(number), move.date, text,
                                      move.create_date.date() if move.create_date else None)
                for line in by_move[move.id]:
                    amount = to_decimal(line.balance)
                    if not amount:
                        continue
                    code = code_of.get(line.account_id.id) or line.account_id.code
                    line_text = line.name if line.name and line.name not in same else ""
                    for objects, part in dims.split(amount, line.analytic_distribution):
                        voucher.transactions.append(sie.Trans(
                            code, objects, part, move.date if line_text else None, line_text))
                    used.add(code)
                if voucher.transactions:
                    out.vouchers.append(voucher)

    def _orgnr(self):
        company = self.company_id
        digits = orgnr_digits(company.company_registry) or orgnr_digits(company.vat)
        if len(digits) == 10:
            return f"{digits[:6]}-{digits[6:]}"
        return company.company_registry or ""

    def _address(self):
        company = self.company_id
        street = " ".join(p for p in (company.street, company.street2) if p)
        city = " ".join(p for p in (company.zip, company.city) if p)
        address = ("", street, city, company.phone or "")
        return address if any(address) else ()

    # -- action ----------------------------------------------------------------------------------

    def action_export(self):
        self.ensure_one()
        data, warnings = self._build()
        content = sie.write(data, checksum_records=self.checksum)
        label = data.year(0).label.replace("/", "-")
        name = re.sub(r"[^\w\-]+", "_", self.company_id.name or "SIE").strip("_") or "SIE"
        ext = "si" if self.sie_type == "4I" else "se"
        _ = self.env._
        summary = Markup("<p>%s</p>") % _(
            "%(vouchers)s vouchers, %(accounts)s accounts, %(balances)s balances.",
            vouchers=len(data.vouchers), accounts=len(data.accounts),
            balances=len(data.opening) + len(data.closing) + len(data.results))
        if warnings:
            summary += Markup("<div class='alert alert-warning' role='alert'><ul class='mb-0'>")
            summary += Markup().join(Markup("<li>%s</li>") % w for w in warnings)
            summary += Markup("</ul></div>")
        self.write({
            "file_data": base64.b64encode(content),
            "file_name": f"{name}_{label}.{ext}",
            "summary_html": summary,
            "state": "done",
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "name": self.env._("SIE Export"),
        }


def _clean_ref(ref):
    """The text of an earlier SIE import's reference (``SIE 2025 A12 — text``)."""
    if not ref:
        return ""
    if ref.startswith("SIE ") and " — " in ref:
        return ref.split(" — ", 1)[1]
    return ref


class _Dimensions:
    """SIE dimensions and objects for Odoo's analytic plans and accounts."""

    def __init__(self, env):
        self.env = env
        self.objects = {}  # (dim, code) -> (name, plan name)
        self._dims = {}  # root plan id -> dim
        self._accounts = {}  # analytic account id -> (dim, code) or None
        taken = env["account.analytic.plan"].sudo().search([("l10n_se_sie_dimension", ">", 0)])
        self._taken = set(taken.mapped("l10n_se_sie_dimension"))
        self._next = 20

    def _dim(self, plan):
        root = plan.root_id or plan
        if root.id not in self._dims:
            number = root.l10n_se_sie_dimension or plan.l10n_se_sie_dimension
            if not number:
                while self._next in self._taken:
                    self._next += 1
                number = self._next
                self._taken.add(number)
            self._dims[root.id] = str(number)
        return self._dims[root.id]

    def pair(self, account_id):
        if account_id not in self._accounts:
            account = self.env["account.analytic.account"].sudo().browse(account_id).exists()
            if not account:
                self._accounts[account_id] = None
            else:
                dim = self._dim(account.plan_id)
                code = account.code or str(account.id)
                root = account.plan_id.root_id or account.plan_id
                self.objects[(dim, code)] = (account.name, root.name)
                self._accounts[account_id] = (dim, code)
        return self._accounts[account_id]

    def split(self, amount, distribution):
        """[(objects, amount)] for a line: one part per distribution key (amount by its
        percentage, the rounding rest on the last part), and a part without objects for what the
        distribution does not cover."""
        if not distribution:
            return [((), amount)]
        parts = []
        total_pct = Decimal(0)
        for key, pct in distribution.items():
            pct = Decimal(str(pct or 0))
            if not pct:
                continue
            pairs = []
            for raw in str(key).split(","):
                if raw.strip().isdigit():
                    pair = self.pair(int(raw))
                    if pair and pair[0] not in {p[0] for p in pairs}:
                        pairs.append(pair)
            pairs.sort(key=lambda p: int(p[0]))
            parts.append([tuple(pairs), round2(amount * pct / 100)])
            total_pct += pct
        if not parts:
            return [((), amount)]
        rest = amount - sum(p[1] for p in parts)
        if total_pct == 100:
            parts[-1][1] += rest
        elif rest:
            parts.append([(), rest])
        return [(objects, part) for objects, part in parts if part]
