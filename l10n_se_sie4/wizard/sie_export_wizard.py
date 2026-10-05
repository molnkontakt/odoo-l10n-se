import base64
import itertools
import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import api, fields, models, release
from odoo.addons.l10n_se_sie4.lib import sie
from odoo.exceptions import UserError

from ..models.sie_analysis import (
    company_env,
    is_number,
    orgnr_digits,
    root,
    round2,
    to_decimal,
    tree,
)

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
    tax return program. Posted entries only. The company is exported with its branches: they
    share its books."""

    _name = "l10n_se.sie.export.wizard"
    _description = "SIE Export"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", required=True, readonly=True,
        default=lambda self: root(self.env.company),
    )
    date_from = fields.Date(string="From", required=True,
                            default=lambda self: self._default_dates()["date_from"])
    date_to = fields.Date(string="To", required=True,
                          default=lambda self: self._default_dates()["date_to"])
    prev_date_from = fields.Date(
        string="Previous Year From", compute="_compute_prev_dates", store=True, readonly=False,
        help="The financial year before (#RAR -1). Proposed from the company's financial year "
        "settings; correct it when the previous year was shortened or extended.",
    )
    prev_date_to = fields.Date(string="Previous Year To", compute="_compute_prev_dates",
                               store=True, readonly=False)
    sie_type = fields.Selection(SIE_TYPES, string="SIE Type", default="4E", required=True)
    journal_ids = fields.Many2many(
        "account.journal", string="Only Journals",
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
    summary_html = fields.Html(string="Summary", readonly=True)

    @api.model
    def _default_dates(self):
        today = fields.Date.context_today(self)
        return root(self.env.company).compute_fiscalyear_dates(today - relativedelta(years=1))

    @api.model
    def _default_result_account(self):
        company = root(self.env.company)
        Account = self.env["account.account"].with_company(company)
        return Account.search([*Account._check_company_domain(company),
                               ("code", "=", "2099")], limit=1)

    @api.depends("date_from", "company_id")
    def _compute_prev_dates(self):
        for wizard in self:
            if not wizard.date_from or not wizard.company_id:
                wizard.prev_date_from = wizard.prev_date_to = False
                continue
            before = wizard.date_from - timedelta(days=1)
            dates = wizard.company_id.compute_fiscalyear_dates(before)
            wizard.prev_date_from = dates["date_from"]
            wizard.prev_date_to = before

    # -- collecting ------------------------------------------------------------------------------

    def _balances(self, domain, groupby=("account_id",)):
        return self.env["account.move.line"]._read_group(domain, list(groupby), ["balance:sum"])

    def _period(self):
        return self.date_from, self.date_to, self.prev_date_from, self.prev_date_to

    def _opening_moves(self, env):
        """Opening balance entries: the company's opening move and the opening balances (and
        their corrections) of SIE imports. On the first day of a period they belong to its
        opening balance (#IB), not to its movements or vouchers."""
        company = self.company_id
        moves = env["account.move"].search([
            *tree(company),
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

    def _check_period(self, warnings):
        company = self.company_id
        if self.date_to < self.date_from:
            raise UserError(self.env._("The period ends before it starts."))
        if not self.prev_date_from or not self.prev_date_to or \
                self.prev_date_to < self.prev_date_from or self.prev_date_to >= self.date_from:
            raise UserError(self.env._(
                "The previous financial year must end before %(date)s and start before it ends.",
                date=self.date_from))
        fy = company.compute_fiscalyear_dates(self.date_from)
        if (fy["date_from"], fy["date_to"]) != (self.date_from, self.date_to):
            warnings.append(self.env._(
                "%(start)s – %(end)s is not a financial year as the company's settings define "
                "it (%(fy_start)s – %(fy_end)s). The file gives it as #RAR 0; check that the "
                "previous year (#RAR -1, %(prev_start)s – %(prev_end)s) is right.",
                start=self.date_from, end=self.date_to, fy_start=fy["date_from"],
                fy_end=fy["date_to"], prev_start=self.prev_date_from,
                prev_end=self.prev_date_to))

    def _build(self):
        """The SieFile to write and the warnings for the summary."""
        self.ensure_one()
        company = self.company_id
        warnings = []
        self._check_period(warnings)
        # every entry of the company and its branches, account codes of the company
        env, hidden = company_env(self.env, company)
        self = self.with_env(env).with_company(company)
        env = self.env
        if hidden:
            warnings.append(self.env._(
                "You have no access to the branches %(branches)s: their entries are not in the "
                "file.", branches=", ".join(hidden.mapped("name"))))
        date_from, date_to, prev_from, prev_to = self._period()
        balances_wanted = self.sie_type != "4I"
        branches = env["res.company"].sudo().search(
            [*tree(company, "id"), ("id", "!=", company.id)])
        if branches:
            warnings.append(self.env._(
                "The branches %(branches)s are included: they share the company's books.",
                branches=", ".join(branches.mapped("name"))))
        # Account names in Swedish when the language is installed (the file is for Swedish
        # auditors and programs), else in the user's language.
        lang = "sv_SE" if env["res.lang"]._lang_get("sv_SE") else env.lang
        Account = env["account.account"].with_company(company).with_context(lang=lang)
        accounts = Account.with_context(active_test=False).search(
            Account._check_company_domain(company))
        code_of = {a.id: a.code for a in accounts}
        base = [*tree(company), ("parent_state", "=", "posted")]
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
            unclosed_by_year = {}
            prev_result = Decimal(0)
            for idx, start, end in ((0, date_from, date_to), (-1, prev_from, prev_to)):
                before = sums(self._before(start, opening_ids))
                moved = sums(self._in_period(start, end, opening_ids))
                unclosed = Decimal(0)
                opening, closing, results = defaultdict(Decimal), defaultdict(Decimal), {}
                for account in set(before) | set(moved):
                    kind = self._kind(account)
                    if kind is None:
                        if (before.get(account) or moved.get(account)) and idx == 0:
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
                if idx == -1:
                    prev_result = sum(results.values(), Decimal(0))
                unclosed_by_year[idx] = unclosed
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
            # More than the previous year's result unclosed: older years are not closed either
            if unclosed_by_year.get(0) and unclosed_by_year[0] != prev_result:
                warnings.append(self.env._(
                    "More than one year's result is not closed in Odoo (before %(date)s: "
                    "%(amount)s, of which the year before %(prev)s). All of it is in the opening "
                    "balance of %(account)s. In BAS, 2099 is the result of the year, 2098 the "
                    "result of the year before and 2091 retained earnings: book the year-end "
                    "closings (8999/2099) and the appropriations of the result (2099 to "
                    "2098/2091) in Odoo, so that the file's opening balance is right.",
                    date=date_from, amount=sie.format_amount(unclosed_by_year[0]),
                    prev=sie.format_amount(prev_result),
                    account=self.result_account_id.code or "2099"))
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

        dims = _Dimensions(env, company)
        if self.sie_type == "3":
            self._object_balances(env, out, base, dims, used, opening_ids)
        if self.sie_type in ("4E", "4I"):
            self._vouchers(env, out, dims, used, code_of, opening_ids)
            if self.journal_ids and balances_wanted:
                warnings.append(self.env._(
                    "Only the vouchers of the chosen journals are in the file; the balances "
                    "include all journals, so the vouchers do not add up to them."))

        # chart of accounts
        bad = sorted(c for c in used if not is_number(c))
        if bad:
            raise UserError(self.env._(
                "SIE needs numeric account numbers. Change these accounts: %(accounts)s.",
                accounts=", ".join(bad)))
        for account in accounts.sorted("code"):
            code = account.code
            if not is_number(code) or self._kind(account) is None:
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
        domain = [*tree(self.company_id), ("state", "=", "posted"),
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
    """SIE dimensions and objects for Odoo's analytic plans and accounts.

    Plans and analytic accounts are read with sudo: an accounting administrator may lack the
    analytic rights, but the file must still say which objects the company's own entries carry.
    Only accounts named in those entries are read, and only the company's (or shared) ones."""

    def __init__(self, env, company):
        self.env = env
        self.companies = set(env["res.company"].sudo().search(tree(company, "id")).ids)
        self.objects = {}  # (dim, code) -> (name, plan name)
        self._dims = {}  # root plan id -> dim
        self._accounts = {}  # analytic account id -> (dim, code, root plan id) or None
        taken = env["account.analytic.plan"].sudo().search([("l10n_se_sie_dimension", ">", 0)])
        self._taken = set(taken.mapped("l10n_se_sie_dimension"))
        self._next = 20

    def _dim(self, plan):
        root_plan = plan.root_id or plan
        if root_plan.id not in self._dims:
            number = root_plan.l10n_se_sie_dimension or plan.l10n_se_sie_dimension
            if not number:
                while self._next in self._taken:
                    self._next += 1
                number = self._next
                self._taken.add(number)
            self._dims[root_plan.id] = str(number)
        return self._dims[root_plan.id]

    def account(self, account_id):
        """(dim, code, root plan id) of an analytic account, or None."""
        if account_id not in self._accounts:
            account = self.env["account.analytic.account"].sudo().browse(account_id).exists()
            if not account or (account.company_id and account.company_id.id not in self.companies):
                self._accounts[account_id] = None
            else:
                dim = self._dim(account.plan_id)
                code = account.code or str(account.id)
                root_plan = account.plan_id.root_id or account.plan_id
                self.objects[(dim, code)] = (account.name, root_plan.name)
                self._accounts[account_id] = (dim, code, root_plan.id)
        return self._accounts[account_id]

    def split(self, amount, distribution):
        """[(objects, amount)] for a line, each part with at most one object per dimension and
        the line's amount counted exactly once.

        Odoo's percentages add up per analytic plan: ``{"a": 60, "b": 40, "x": 100}`` is 60/40
        on one plan and 100 on another. Keys naming the same plans form one group (what a group
        does not cover becomes a part without its objects); the groups are combined, so every
        object gets its own share: a 60, b 40, x 100. The rounding rest goes to the largest
        part."""
        if not distribution:
            return [((), amount)]
        groups = defaultdict(list)  # frozenset of root plans -> [(pairs, pct)]
        for key, pct in distribution.items():
            pct = Decimal(str(pct or 0))
            if not pct:
                continue
            pairs = {}
            for raw in str(key).split(","):
                info = self.account(int(raw)) if is_number(raw.strip()) else None
                if info and info[0] not in pairs:
                    pairs[info[0]] = info
            if not pairs:
                continue
            plans = frozenset(info[2] for info in pairs.values())
            objects = tuple(sorted(((d, info[1]) for d, info in pairs.items()),
                                   key=lambda p: int(p[0])))
            groups[plans].append((objects, pct / 100))
        if not groups:
            return [((), amount)]
        shares = []
        for parts in groups.values():
            covered = sum(f for _o, f in parts)
            if covered < 1:
                parts = parts + [((), 1 - covered)]
            shares.append(parts)
        combined = []
        for combo in itertools.product(*shares):
            objects = []
            seen = set()
            fraction = Decimal(1)
            for part_objects, part_fraction in combo:
                fraction *= part_fraction
                for pair in part_objects:
                    if pair[0] not in seen:
                        seen.add(pair[0])
                        objects.append(pair)
            objects.sort(key=lambda p: int(p[0]))
            combined.append([tuple(objects), fraction])
        merged = defaultdict(Decimal)
        for objects, fraction in combined:
            merged[objects] += fraction
        result = [[objects, round2(amount * fraction)] for objects, fraction in merged.items()]
        rest = amount - sum(part for _o, part in result)
        if rest and result:
            largest = max(result, key=lambda p: abs(p[1]))
            largest[1] += rest
        return [(objects, part) for objects, part in result if part]
