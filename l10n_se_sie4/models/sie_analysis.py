"""What an SIE import would do, worked out before anything is written.

The wizard shows it as the preview, and the import runs it again before writing (once per run), so
the checks (lock dates, the order of the years, missing accounts, duplicates) hold for what is
actually written. Plain classes and functions; the ORM work is done through the ``env`` passed in.

The company is always taken with its branches: a branch shares the books of its main company,
so its entries, SIE keys and opening entries count as the company's.
"""

import hashlib
import re
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from markupsafe import Markup

from odoo.addons.l10n_se_sie4.lib import sie
from odoo.exceptions import UserError

CENT = Decimal("0.01")
#: Lib issue codes that make one voucher invalid without making the file unreadable. Such a
#: voucher can be left out on request; any other error stops the import.
VOUCHER_ERRORS = {"unbalanced", "voucher_bad_line"}
#: How many entries a list in the preview shows.
LIST_LIMIT = 30
#: Spreadsheet programs run a cell that starts with one of these as a formula.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_LETTERS = re.compile(r"[^\W\d_]+")

_CACHE = OrderedDict()


def parse_cached(data):
    """Parse a file once per content: the preview recomputes on every change of an option."""
    key = hashlib.sha1(data).hexdigest()
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    parsed = sie.parse(data)
    _CACHE[key] = parsed
    while len(_CACHE) > 8:
        _CACHE.popitem(last=False)
    return parsed


def round2(amount, digits=2):
    return Decimal(amount).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)


def to_decimal(value):
    return round2(Decimal(repr(value))) if value else Decimal("0.00")


def is_number(value):
    return sie.is_number(value or "")


def safe_text(value):
    """A text for a list that can be exported to a spreadsheet: a leading = + - @ would make it
    a formula there, so it gets an apostrophe in front."""
    if value and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def root(company):
    return company.root_id or company


def tree(company, field_name="company_id"):
    """Domain: the company and its branches."""
    return [(field_name, "child_of", root(company).id)]


def company_env(env, company):
    """``env`` working in ``company`` with the whole company (main company and branches) in the
    allowed companies, so that the multi-company rules show every entry of the books. Branches
    the user may not access stay hidden (``hidden`` lists them)."""
    companies = env["res.company"].sudo().search(tree(company, "id"))
    allowed = companies & env.user.company_ids
    ids = [company.id] + [c for c in allowed.ids if c != company.id]
    return env(context=dict(env.context, allowed_company_ids=ids)), companies - allowed


def ref_code(series, number):
    """Series and number as in the reference: ``A12``, but ``1/12`` or ``A1/2`` when the series
    ends with a digit, so that series A1 number 2 is never read as series A number 12."""
    if not number:
        return series
    if series and series[-1].isdigit():
        return f"{series}/{number}"
    return f"{series}{number}"


def voucher_key(label, voucher, occurrence=1):
    """Unique per company: financial year, series and number. A voucher without a number (an
    import file, type 4I) is keyed on its content and, for identical vouchers, on which of them it
    is (``occurrence``, counted in file order)."""
    if voucher.number:
        return f"{label}|{voucher.series}|{voucher.number}"
    digest = content_hash(voucher)
    suffix = f"-{occurrence}" if occurrence > 1 else ""
    return f"{label}|{voucher.series}|#{digest}{suffix}"


def content_hash(voucher):
    content = repr((
        voucher.date, voucher.text,
        [(t.account, t.objects, str(t.amount), t.text) for t in voucher.transactions],
    ))
    return hashlib.sha1(content.encode()).hexdigest()[:12]


def voucher_ref(label, voucher):
    head = f"SIE {label} {ref_code(voucher.series, voucher.number)}".rstrip()
    return f"{head} — {voucher.text}" if voucher.text else head


def legacy_ref(label, voucher):
    """The reference earlier import scripts wrote (``SIE 2025 A12 — text``), without the text.
    Only for a series of letters, where series and number cannot be mixed up."""
    if voucher.number and voucher.series and _LETTERS.fullmatch(voucher.series):
        return f"SIE {label} {voucher.series}{voucher.number}"
    return None


def orgnr_digits(value):
    digits = "".join(c for c in (value or "") if c in "0123456789")
    if len(digits) == 12 and digits.endswith("01"):  # VAT number SE + 10 digits + 01
        digits = digits[:10]
    if len(digits) == 12 and digits.startswith(("16", "19", "20")):  # century prefix
        digits = digits[2:]
    return digits


@dataclass
class YearData:
    """One financial year to import: from a file's #RAR 0, or (a file without #RAR) from the
    company's financial year of the vouchers."""

    key: str
    label: str
    start: date
    end: date
    filename: str
    parsed: object
    vouchers: list
    opening: dict | None = None
    closing: dict | None = None
    results: dict | None = None
    outside: list = field(default_factory=list)  # vouchers of the file outside this year

    @property
    def has_balances(self):
        return bool(self.closing or self.results)


@dataclass
class Item:
    """One entry to create: ``kind`` is ``opening``, ``opening_diff`` or ``voucher``."""

    kind: str
    year: YearData
    key: str
    ref: str
    date: date
    voucher: object = None
    journal: object = None
    #: opening_diff: the amounts to book ({code: Decimal}), and what Odoo must have before the
    #: year for them to be right ({code: Decimal} and where that comes from)
    diffs: dict = field(default_factory=dict)
    expected_before: dict = field(default_factory=dict)
    expected_label: str = ""


@dataclass
class Analysis:
    files: list = field(default_factory=list)  # (name, parsed)
    years: list = field(default_factory=list)  # YearData, all years found
    selected: list = field(default_factory=list)  # YearData, chronological
    items: list = field(default_factory=list)  # Item, in creation order
    errors: list = field(default_factory=list)  # str: stop the import
    warnings: list = field(default_factory=list)
    infos: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)  # year key -> Counter
    series: Counter = field(default_factory=Counter)
    missing_accounts: list = field(default_factory=list)  # (code, name, ktyp, type)
    archived_accounts: list = field(default_factory=list)  # account records
    accounts: dict = field(default_factory=dict)  # code -> account record
    dimensions: list = field(default_factory=list)  # (dim, name, plan or None, objects, new)
    invalid_vouchers: set = field(default_factory=set)  # (filename, line)
    analytic: dict = field(default_factory=dict)  # (dimension, object) -> analytic account id
    existing: set = field(default_factory=set)
    digits: int = 2

    @property
    def blocked(self):
        return bool(self.errors)


# -- reading the files ------------------------------------------------------------------------------


def collect_years(env, company, files):
    """YearData for every financial year in the files, and the problems of putting them
    together (the same year in two files)."""
    years = OrderedDict()
    problems = []
    for name, parsed in files:
        fy = parsed.year(0)
        if fy:
            vouchers = parsed.vouchers_in(fy)
            inside = {id(v) for v in vouchers}
            year = YearData(
                key=f"{fy.start}/{fy.end}", label=fy.label, start=fy.start, end=fy.end,
                filename=name, parsed=parsed, vouchers=vouchers,
                opening=parsed.amounts("opening", 0), closing=parsed.amounts("closing", 0),
                results=parsed.amounts("results", 0),
                outside=[v for v in parsed.vouchers if id(v) not in inside],
            )
            found = [year]
        else:
            # An import file (4I) without #RAR: the company's financial years of the vouchers.
            groups = defaultdict(list)
            for v in parsed.vouchers:
                if v.date:
                    dates = company.compute_fiscalyear_dates(v.date)
                    groups[(dates["date_from"], dates["date_to"])].append(v)
            found = []
            for (start, end), vouchers in sorted(groups.items()):
                label = sie.FiscalYear(0, start, end).label
                found.append(YearData(f"{start}/{end}", label, start, end, name, parsed,
                                      vouchers))
        for year in found:
            if year.key in years:
                problems.append(env._(
                    "The financial year %(year)s is in two files (%(first)s and %(second)s). "
                    "Upload one file per financial year.",
                    year=year.label, first=years[year.key].filename, second=name,
                ))
                continue
            years[year.key] = year
    return sorted(years.values(), key=lambda y: y.start), problems


def issue_text(env, issue):
    """A lib issue in the user's language."""
    p = issue.params
    c = issue.code
    if c == "unbalanced":
        return env._(
            "Voucher %(ref)s of %(date)s does not balance: its lines add up to %(diff)s.",
            ref=f"{p['series']}{p['number']}", date=p["date"], diff=sie.format_amount(
                p["difference"]))
    if c == "voucher_bad_line":
        return env._("Voucher %(ref)s has a line that cannot be read.",
                     ref=f"{p['series']}{p['number']}")
    if c == "empty_voucher":
        return env._("Voucher %(ref)s has no lines; it is skipped.",
                     ref=f"{p['series']}{p['number']}")
    if c == "unclosed_quote":
        return env._("A text has no closing quote.")
    if c in ("unclosed_objects", "nested_objects", "bad_objects"):
        return env._("The object list {...} cannot be read.")
    if c == "trans_outside_ver":
        return env._("%(label)s outside a #VER block.", label=p.get("label", "#TRANS"))
    if c in ("missing_date", "bad_date"):
        return env._("%(what)s: '%(value)s' is not a date (YYYYMMDD).", what=p.get("what"),
                     value=p.get("value", ""))
    if c == "bad_amount":
        return env._("%(what)s: '%(value)s' is not an amount.", what=p.get("what"),
                     value=p.get("value", ""))
    if c == "bad_account":
        return env._("%(what)s: '%(value)s' is not an account number.", what=p.get("what"),
                     value=p.get("value", ""))
    if c == "bad_year":
        return env._("%(what)s: '%(value)s' is not a year number (0, -1, ...).",
                     what=p.get("what"), value=p.get("value", ""))
    if c == "bad_year_range":
        return env._("#RAR: the financial year ends before it starts.")
    if c == "bad_period":
        return env._("%(label)s: '%(value)s' is not a month (YYYYMM).", label=p.get("label"),
                     value=p.get("value", ""))
    if c in ("bad_dimension", "bad_object"):
        return env._("A dimension or object cannot be read.")
    if c in ("unclosed_block", "missing_block"):
        return env._("The #VER on line %(line)s has no complete {...} block.",
                     line=p.get("ver_line"))
    if c == "unexpected_brace":
        return env._("A brace { or } that does not belong to a #VER.")
    if c == "no_label":
        return env._("The line does not start with a #label.")
    if c == "line_too_long":
        return env._("The line is longer than %(limit)s characters and is not read.",
                     limit=p.get("limit"))
    if c == "rtrans_unmatched":
        return env._("#RTRANS is not followed by an identical #TRANS. Only #TRANS lines are "
                     "booked; check the voucher in the source program.")
    if c == "unknown_label":
        return env._("Unknown record %(label)s ignored (%(count)s times).", label=p["label"],
                     count=p["count"])
    if c == "duplicate":
        return env._("%(label)s %(key)s occurs more than once.", label=p["label"], key=p["key"])
    if c == "comma_decimal":
        return env._("Amounts are written with a decimal comma instead of a dot; read anyway.")
    if c == "missing_objects":
        return env._("Lines without an object list {}; read anyway.")
    if c == "no_flag":
        return env._("#FLAGGA is missing.")
    if c == "flag_not_first":
        return env._("#FLAGGA is not the first record.")
    if c == "flag_imported":
        return env._("#FLAGGA 1: the file is marked as already read into a program. Vouchers "
                     "already in Odoo are skipped anyway.")
    if c == "truncated":
        return env._("The file starts with #KSUMMA but the closing #KSUMMA is missing: the file "
                     "is probably cut off. Export it again.")
    if c == "checksum_mismatch":
        return env._("The checksum (#KSUMMA %(declared)s) does not match the content "
                     "(%(computed)s): the file may have been changed after the export.",
                     declared=p["declared"], computed=p["computed"])
    if c == "bad_checksum":
        return env._("#KSUMMA is not a number.")
    if c == "encoding":
        return env._("Character set: read as %(encoding)s (the file declares #FORMAT "
                     "%(declared)s).", encoding=p["encoding"], declared=p["declared"] or "-")
    if c == "no_sie_type":
        return env._("#SIETYP is missing: read as SIE type 1.")
    if c == "no_rar":
        return env._("No financial year (#RAR 0): the vouchers are placed in the company's "
                     "financial years.")
    if c == "outside_years":
        return env._("%(count)s vouchers are dated outside the file's financial years "
                     "(first: %(first)s).", count=p["count"], first=p["first"])
    if c == "undeclared_accounts":
        return env._("Accounts used without #KONTO: %(accounts)s.",
                     accounts=", ".join(p["accounts"][:20]))
    if c == "bad_account_type":
        return env._("#KTYP %(account)s: unknown account type '%(value)s'.",
                     account=p["account"], value=p["value"])
    return issue.message


def located(env, filename, line, text):
    if line:
        return env._("%(file)s, line %(line)s: %(text)s", file=filename, line=line, text=text)
    return env._("%(file)s: %(text)s", file=filename, text=text)


# -- the books --------------------------------------------------------------------------------------


def account_map(env, company):
    """{code: account} of the company, archived accounts included."""
    Account = env["account.account"].with_company(company).with_context(active_test=False)
    accounts = Account.search(Account._check_company_domain(company))
    return {a.code: a for a in accounts if a.code}


def existing_keys(env, company):
    """The SIE keys in the company's books, and the references of earlier import scripts.
    Cancelled entries do not count: such a voucher can be imported again."""
    Move = env["account.move"].with_context(active_test=False)
    live = [*tree(company), ("state", "!=", "cancel")]
    keys = set(Move.search([*live, ("l10n_se_sie_key", "!=", False)]).mapped("l10n_se_sie_key"))
    legacy = Move.search([*live, ("l10n_se_sie_key", "=", False), ("ref", "=like", "SIE %")])
    refs = {r.split(" — ")[0].strip() for r in legacy.mapped("ref") if r}
    return keys, refs


def odoo_balances(env, company, day, states=("posted", "draft")):
    """{code: balance} of the company's entries dated before ``day``, with the opening entries
    dated on ``day`` (they are the balance the day starts with)."""
    openings = opening_entries(env, company).filtered(lambda m: m.date == day).ids
    groups = env["account.move.line"]._read_group(
        [*tree(company), ("parent_state", "in", states),
         "|", ("date", "<", day), "&", ("date", "=", day), ("move_id", "in", openings)],
        ["account_id"], ["balance:sum"])
    out = {}
    for account, balance in groups:
        code = account.with_company(company).code
        out[code] = out.get(code, Decimal(0)) + to_decimal(balance)
    return {c: b for c, b in out.items() if b}


def entry_dates(env, company):
    """(first, last) date of the company's posted and draft entries, or (None, None)."""
    groups = env["account.move"]._read_group(
        [*tree(company), ("state", "in", ("posted", "draft"))], [], ["date:min", "date:max"])
    return groups[0] if groups else (None, None)


def opening_entries(env, company, exclude_import=None):
    """The opening entries in the books: SIE opening balances and their differences, and the
    company's opening move."""
    domain = [*tree(company), ("state", "in", ("posted", "draft")),
              "|", ("l10n_se_sie_key", "=like", "%|IB|"), ("l10n_se_sie_key", "=like", "%|IBDIFF|")]
    if exclude_import:
        domain.append(("l10n_se_sie_import_id", "!=", exclude_import))
    moves = env["account.move"].search(domain)
    opening = root(company).account_opening_move_id
    if opening and opening.state in ("posted", "draft"):
        moves |= opening
    return moves


def lock_violation(company, day, journal):
    """The lock dates an entry on ``day`` in ``journal`` violates (for the current user)."""
    return company._get_lock_date_violations(
        day,
        fiscalyear=True,
        sale=journal.type == "sale" if journal else False,
        purchase=journal.type == "purchase" if journal else False,
        tax=False,
        hard=True,
    )


def new_journal_is_hashed(env, company):
    """Whether a general journal created now would secure its entries with a hash (OCA
    account_journal_restrict_mode makes it so)."""
    probe = env["account.journal"].new({"type": "general", "company_id": company.id,
                                        "name": "SIE", "code": "SIE"})
    return bool(probe.restrict_mode_hash_table)


# -- the analysis -----------------------------------------------------------------------------------


def analyse(env, company, files, options):
    """Everything the import would do with ``options``:

    * ``years``: keys of the selected years (None = all);
    * ``import_opening``, ``import_vouchers``, ``opening_differences``, ``skip_invalid``,
      ``analytics``: booleans;
    * ``missing``: ``create`` or ``abort``;
    * ``journal``: the journal for all entries (None = the SIE journal, created when needed);
    * ``series_journals``: {series: journal} when entries go to a journal per series, else None;
    * ``reconcile``, ``check_date``: reconciliation only;
    * ``import_id``: the import being run (its own entries are not "earlier imports").
    """
    a = Analysis(files=files, digits=company.currency_id.decimal_places)
    a.years, problems = collect_years(env, company, files)
    a.errors.extend(problems)
    selected_keys = options.get("years")
    a.selected = [y for y in a.years if selected_keys is None or y.key in selected_keys]
    if not files:
        return a
    if not a.selected:
        a.errors.append(env._("Select at least one financial year."))
        return a
    used_files = {y.filename for y in a.selected}
    reconcile = bool(options.get("reconcile"))

    # -- the files themselves
    for name, parsed in files:
        if name not in used_files:
            continue
        for issue in parsed.issues:
            text = located(env, name, issue.line, issue_text(env, issue))
            if issue.severity == sie.ERROR and reconcile:
                # The balances can still be compared; the source program must fix the file.
                a.warnings.append(text)
            elif issue.severity == sie.ERROR:
                if issue.code in VOUCHER_ERRORS:
                    a.invalid_vouchers.add((name, issue.line))
                    if options.get("skip_invalid"):
                        a.warnings.append(text + " " + env._("(left out)"))
                    else:
                        a.errors.append(text)
                else:
                    a.errors.append(text)
            elif issue.severity == sie.WARNING:
                a.warnings.append(text)
            else:
                a.infos.append(text)
        # SIE amounts are in the file's currency, SEK when #VALUTA is missing
        currency = parsed.currency or "SEK"
        if currency != company.currency_id.name:
            a.errors.append(env._(
                "%(file)s is in %(currency)s, the company's currency is %(company_currency)s.",
                file=name, currency=currency, company_currency=company.currency_id.name))
        file_org = orgnr_digits(parsed.orgnr)
        company_org = {orgnr_digits(root(company).company_registry),
                       orgnr_digits(root(company).vat)} - {""}
        if file_org and company_org and file_org not in company_org:
            a.warnings.append(env._(
                "%(file)s is for organisation number %(orgnr)s (%(name)s), which is not the "
                "company's. Check that you import into the right company.",
                file=name, orgnr=parsed.orgnr, name=parsed.company_name or "-"))
        elif file_org and not company_org:
            a.infos.append(env._(
                "%(file)s is for organisation number %(orgnr)s; the company has no company "
                "registry or VAT number to compare it with.", file=name, orgnr=parsed.orgnr))

    # -- vouchers of a file outside its financial year (e.g. in #RAR -1) are never imported
    for year in a.selected:
        if year.outside:
            shown = ", ".join(f"{ref_code(v.series, v.number) or '-'} ({v.date})"
                              for v in year.outside[:10] if v.date)
            a.warnings.append(env._(
                "%(file)s: %(count)s vouchers are dated outside the financial year %(year)s and "
                "are not imported: %(vouchers)s.", file=year.filename, count=len(year.outside),
                year=year.label, vouchers=shown))
            a.stats.setdefault(year.key, Counter())["outside"] = len(year.outside)

    if reconcile:
        as_of = options.get("check_date")
        if as_of and not any(y.start <= as_of <= y.end for y in a.selected):
            a.errors.append(env._("The date %(date)s is not in a selected financial year.",
                                  date=as_of))
        for year in a.selected:
            day = as_of if as_of and year.start <= as_of <= year.end else None
            try:
                expected_balances(env, year, day)
            except UserError as exc:
                a.errors.append(str(exc))
        return a

    # -- what is already there
    a.accounts = account_map(env, company)
    if not a.accounts:
        a.errors.append(env._("The company has no chart of accounts. Install one (Accounting "
                              "settings) before importing."))
    keys, legacy = existing_keys(env, company)
    a.existing = keys
    lock_cache = {}

    def locked(day, journal):
        ck = (day, journal.id if journal else 0)
        if ck not in lock_cache:
            lock_cache[ck] = lock_violation(company, day, journal)
        return lock_cache[ck]

    series_journals = options.get("series_journals")
    main_journal = options.get("journal")
    first = a.selected[0]
    import_opening = options.get("import_opening")
    import_vouchers = options.get("import_vouchers")
    if not import_opening and not import_vouchers:
        a.errors.append(env._("Choose what to import: the opening balance, the vouchers or both."))

    # -- the order of the years: an opening balance after a year's start already contains that
    # year, so its vouchers (or its own opening balance) would count twice
    later_openings = opening_entries(env, company, exclude_import=options.get("import_id"))
    for year in a.selected:
        after = later_openings.filtered(lambda m, y=year: m.date > y.start)
        if after:
            a.errors.append(env._(
                "The books already have an opening balance dated %(date)s (%(entry)s), after the "
                "start of %(year)s: importing %(year)s now would count it twice. Import the years "
                "in order: undo the later import first.", date=min(after.mapped("date")),
                entry=after.sorted("date")[0].display_name, year=year.label))

    needed = defaultdict(set)  # code -> where
    violations = []
    seen_keys = {}  # key -> (file, line)
    prev = None
    for year in a.selected:
        st = a.stats.setdefault(year.key, Counter())
        st["vouchers"] = len(year.vouchers)
        st["lines"] = sum(len(v.transactions) for v in year.vouchers)
        # -- opening balance: only for the first year, and only into empty books
        opening_from_file = year is first and import_opening
        if opening_from_file:
            if year.opening:
                key = f"{year.label}|IB|"
                rounded = {c: round2(amount, a.digits) for c, amount in year.opening.items()}
                total = sum(rounded.values(), Decimal(0))
                first_day, last_day = entry_dates(env, company)
                if key in keys:
                    a.infos.append(env._("The opening balance %(year)s is already imported.",
                                         year=year.label))
                elif first_day:
                    a.errors.append(env._(
                        "The company already has entries (posted or draft, from %(first)s to "
                        "%(last)s). The opening balance %(year)s would count the same balances "
                        "twice: untick 'Opening balance' to import the vouchers only, or import "
                        "into a company without entries.", first=first_day, last=last_day,
                        year=year.label))
                elif total != 0:
                    unrounded = sum(year.opening.values(), Decimal(0))
                    a.errors.append(env._(
                        "The opening balance %(year)s (#IB 0) does not add up to zero after "
                        "rounding to %(digits)s decimals (difference %(diff)s, before rounding "
                        "%(raw)s).", year=year.label, digits=a.digits,
                        diff=sie.format_amount(total), raw=str(unrounded)))
                else:
                    item = Item("opening", year, key, f"SIE {year.label} IB — "
                                + env._("Opening balance"), year.start, journal=main_journal)
                    a.items.append(item)
                    st["opening"] = 1
                    for code, amount in rounded.items():
                        if amount:
                            needed[code].add(year.label)
                            if sie.account_class(code) != "balance":
                                a.warnings.append(env._(
                                    "The opening balance %(year)s has an amount on result "
                                    "account %(account)s; it is imported as it is.",
                                    year=year.label, account=code))
                    if locked(year.start, main_journal):
                        violations.append((year, year.start, locked(year.start, main_journal)))
            else:
                a.infos.append(env._("%(file)s has no opening balance (#IB 0) for %(year)s.",
                                     file=year.filename, year=year.label))
        # -- otherwise the opening balance follows from the earlier vouchers: the previous
        # selected year's closing balance, or what Odoo already has before the year
        elif f"{year.label}|IB|" in keys:
            a.infos.append(env._("The opening balance %(year)s is already imported.",
                                 year=year.label))
        elif year.opening is not None and import_vouchers:
            if prev is not None and prev.closing is not None:
                before, before_label = prev.closing, prev.label
            else:
                before = odoo_balances(env, company, year.start)
                before_label = env._("Odoo before %(date)s", date=year.start)
            diffs = {}
            for code in set(year.opening) | set(before):
                if sie.account_class(code) != "balance":
                    continue
                d = round2(year.opening.get(code, Decimal(0)) - before.get(code, Decimal(0)),
                           a.digits)
                if d:
                    diffs[code] = d
            unbalanced = sum(diffs.values(), Decimal(0))
            if diffs:
                listed = ", ".join(f"{c} ({sie.format_amount(d)})" for c, d in
                                   sorted(diffs.items())[:10])
                if options.get("opening_differences") and unbalanced:
                    a.errors.append(env._(
                        "The opening balance %(year)s differs from %(prev)s by %(amount)s in "
                        "total, so the differences cannot be booked as one entry. Usually the "
                        "year before has no year-end closing (result to 2099, e.g. 8999/2099): "
                        "import or book it first.", year=year.label, prev=before_label,
                        amount=sie.format_amount(unbalanced)))
                elif options.get("opening_differences"):
                    key = f"{year.label}|IBDIFF|"
                    if key not in keys:
                        a.items.append(Item(
                            "opening_diff", year, key,
                            f"SIE {year.label} IB — " + env._("Opening balance differences"),
                            year.start, journal=main_journal, diffs=diffs,
                            expected_before={c: b for c, b in before.items()
                                             if sie.account_class(c) == "balance"},
                            expected_label=before_label))
                        for code in diffs:
                            needed[code].add(year.label)
                        if locked(year.start, main_journal):
                            violations.append((year, year.start, locked(year.start, main_journal)))
                    a.warnings.append(env._(
                        "The opening balance %(year)s differs from the closing balance "
                        "%(prev)s on %(count)s accounts: %(accounts)s. The differences are "
                        "booked as an entry on %(date)s.", year=year.label, prev=before_label,
                        count=len(diffs), accounts=listed, date=year.start))
                else:
                    a.warnings.append(env._(
                        "The opening balance %(year)s differs from the closing balance "
                        "%(prev)s on %(count)s accounts: %(accounts)s. Odoo's balances follow "
                        "from the vouchers, so these accounts will differ from the file. Tick "
                        "'Book opening balance differences' to book them.",
                        year=year.label, prev=before_label, count=len(diffs), accounts=listed))
        prev = year
        # -- vouchers
        if not import_vouchers:
            continue
        occurrences = Counter()
        keyed = []
        for v in year.vouchers:  # file order, for the occurrence of identical vouchers
            occurrence = 1
            if not v.number:
                occurrences[(v.series, content_hash(v))] += 1
                occurrence = occurrences[(v.series, content_hash(v))]
            keyed.append((voucher_key(year.label, v, occurrence), v))
        for key, v in keyed:
            if key in seen_keys:
                first_file, first_line = seen_keys[key]
                a.errors.append(located(env, year.filename, v.line, env._(
                    "Voucher %(ref)s occurs twice in the financial year %(year)s (also %(file)s, "
                    "line %(line)s). Each voucher number may occur once per series and year.",
                    ref=ref_code(v.series, v.number), year=year.label, file=first_file,
                    line=first_line)))
            else:
                seen_keys[key] = (year.filename, v.line)
        for key, v in sorted(keyed, key=lambda kv: (kv[1].date, kv[1].series,
                                                    _num(kv[1].number))):
            legacy_head = legacy_ref(year.label, v)
            if key in keys or (legacy_head and legacy_head in legacy):
                st["existing"] += 1
                continue
            if (year.filename, v.line) in a.invalid_vouchers:
                st["invalid"] += 1
                continue
            lines = [t for t in v.transactions if t.amount]
            if not lines:
                st["empty"] += 1
                continue
            if sum(round2(t.amount, a.digits) for t in lines) != 0:
                text = env._("Voucher %(ref)s does not balance after rounding its amounts to "
                             "%(digits)s decimals.", ref=ref_code(v.series, v.number),
                             digits=a.digits)
                if options.get("skip_invalid"):
                    st["invalid"] += 1
                    a.warnings.append(located(env, year.filename, v.line, text))
                    continue
                a.errors.append(located(env, year.filename, v.line, text))
            journal = series_journals.get(v.series) if series_journals is not None else main_journal
            item = Item("voucher", year, key, voucher_ref(year.label, v), v.date, v, journal)
            a.items.append(item)
            a.series[v.series] += 1
            st["to_import"] += 1
            for t in lines:
                needed[t.account].add(year.label)
            if locked(v.date, journal):
                violations.append((year, v.date, locked(v.date, journal)))

    # -- lock dates
    if violations:
        per_year = OrderedDict()
        for year, _day, violated in violations:
            per_year.setdefault(year.key, [year, 0, set()])
            per_year[year.key][1] += 1
            per_year[year.key][2].update(violated)
        for year, count, violated in per_year.values():
            a.stats[year.key]["locked"] = count
            a.errors.append(env._(
                "%(count)s entries of %(year)s are dated in a locked period (%(locks)s). Odoo "
                "does not allow entries there: change the lock date in the accounting settings "
                "or leave the year out.", count=count, year=year.label,
                locks=env["res.company"]._format_lock_dates(sorted(violated))))
    tax_lock = company.tax_lock_date
    if tax_lock and any(i.date <= tax_lock for i in a.items):
        a.warnings.append(env._(
            "Some entries are dated on or before the tax return lock date %(date)s. They carry "
            "no taxes, so Odoo accepts them, but the VAT for those periods is already "
            "declared.", date=tax_lock))
    journals = {i.journal for i in a.items if i.journal}
    hashed = [j.name for j in journals if j.restrict_mode_hash_table]
    if any(not i.journal for i in a.items) and new_journal_is_hashed(env, company):
        hashed.append(env._("(new journals of the import)"))
    if hashed:
        a.warnings.append(env._(
            "The journal %(journals)s secures posted entries with a hash: the import cannot be "
            "undone once posted.", journals=", ".join(hashed)))

    # -- accounts
    missing = []
    for code in sorted(needed):
        account = a.accounts.get(code)
        if account is None:
            sie_account = _sie_account(a.selected, code)
            missing.append((code, sie_account.name if sie_account else "",
                            sie_account.type if sie_account else ""))
        elif not account.active:
            a.archived_accounts.append(account)
    if missing:
        for code, name, ktyp in missing:
            a.missing_accounts.append((code, name, ktyp, suggest_type(a.accounts, code, ktyp)))
        if options.get("missing") != "create":
            a.errors.append(env._(
                "%(count)s accounts are missing in the chart of accounts: %(accounts)s. Create "
                "them yourself or choose 'Create them' under missing accounts.",
                count=len(missing), accounts=", ".join(c for c, _n, _k in missing[:LIST_LIMIT])))
    if a.archived_accounts:
        codes = ", ".join(acc.code for acc in a.archived_accounts)
        if options.get("missing") == "create":
            a.warnings.append(env._("Archived accounts that are reactivated: %(accounts)s.",
                                    accounts=codes))
        else:
            a.errors.append(env._("The vouchers use archived accounts: %(accounts)s.",
                                  accounts=codes))

    # -- dimensions
    if options.get("analytics"):
        used = defaultdict(set)
        for item in a.items:
            if item.voucher:
                for t in item.voucher.transactions:
                    for dim, obj in t.objects:
                        used[dim].add(obj)
        # Analytic plans are shared configuration; an accounting administrator without the
        # analytic rights may still import them.
        Plan = env["account.analytic.plan"].sudo()
        Analytic = env["account.analytic.account"].sudo().with_context(active_test=False)
        for dim in sorted(used, key=int):
            name = dimension_name(env, a.selected, dim)
            plan = Plan.search([("l10n_se_sie_dimension", "=", int(dim))], limit=1)
            existing = set(Analytic.search([
                ("plan_id", "=", plan.id), ("company_id", "in", [company.id, False]),
            ]).mapped("code")) if plan else set()
            new = len(used[dim] - existing)
            a.dimensions.append((dim, name, plan or None, len(used[dim]), new))
            declared = _sie_dimension(a.selected, dim)
            if declared and declared.parent:
                a.infos.append(env._(
                    "Dimension %(dim)s is a sub-dimension in the file; it becomes an analytic "
                    "plan of its own.", dim=dim))
    return a


def expected_balances(env, year, as_of=None):
    """What the file says the accounts were on ``as_of`` (None = the end of the year).

    Returns ``(balances, results, source)``: balance sheet accounts cumulative from the opening
    balance, result accounts from the first day of the year; ``source`` is ``closing`` (#UB and
    #RES), ``psaldo`` (#IB plus the monthly changes #PSALDO up to a month end) or ``vouchers``
    (#IB plus the #TRANS lines up to the date). Raises UserError when the file cannot tell.
    """
    if as_of is None or as_of >= year.end:
        if not year.has_balances:
            raise UserError(env._(
                "%(file)s has no closing balances (#UB/#RES) for %(year)s to compare with.",
                file=year.filename, year=year.label))
        return dict(year.closing or {}), dict(year.results or {}), "closing"
    if as_of < year.start:
        raise UserError(env._("%(date)s is before the financial year %(year)s.", date=as_of,
                              year=year.label))

    def kind(code):
        if code in (year.results or {}):
            return "result"
        if code in (year.closing or {}) or code in (year.opening or {}):
            return "balance"
        return "balance" if sie.account_class(code) == "balance" else "result"

    balances = defaultdict(Decimal, year.opening or {})
    results = defaultdict(Decimal)
    month_end = (as_of.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    psaldo = [p for p in year.parsed.period_balances if p.year == 0 and not p.objects]
    if psaldo and as_of == month_end:
        first, last = year.start.strftime("%Y%m"), as_of.strftime("%Y%m")
        for p in psaldo:
            if first <= p.period <= last:
                (results if kind(p.account) == "result" else balances)[p.account] += p.amount
        return dict(balances), dict(results), "psaldo"
    if year.vouchers:
        for v in year.vouchers:
            if v.date and v.date <= as_of:
                for t in v.transactions:
                    (results if kind(t.account) == "result" else balances)[t.account] += t.amount
        return dict(balances), dict(results), "vouchers"
    if psaldo:
        raise UserError(env._(
            "%(file)s has monthly balances but no vouchers: choose the last day of a month.",
            file=year.filename))
    raise UserError(env._(
        "%(file)s has neither vouchers nor monthly balances (#PSALDO): it can only be compared "
        "at the end of the year.", file=year.filename))


def _num(number):
    return (0, int(number), "") if is_number(number) else (1, 0, number)


def _sie_account(years, code):
    for year in reversed(years):
        acc = year.parsed.accounts.get(code)
        if acc and acc.name:
            return acc
    for year in reversed(years):
        if code in year.parsed.accounts:
            return year.parsed.accounts[code]
    return None


def _sie_dimension(years, dim):
    for year in reversed(years):
        if dim in year.parsed.dimensions:
            return year.parsed.dimensions[dim]
    return None


def dimension_name(env, years, dim):
    declared = _sie_dimension(years, dim)
    if declared and declared.name:
        return declared.name
    reserved = {
        "1": env._("Cost centre"),
        "2": env._("Cost unit"),
        "6": env._("Project"),
        "7": env._("Employee"),
        "8": env._("Customer"),
        "9": env._("Supplier"),
        "10": env._("Invoice"),
    }
    return reserved.get(dim) or env._("Dimension %(dim)s", dim=dim)


def object_name(years, dim, code):
    for year in reversed(years):
        obj = year.parsed.objects.get((dim, code))
        if obj and obj.name:
            return obj.name
    return code


_GROUP = {
    "T": ("asset",),
    "S": ("liability", "equity"),
    "I": ("income",),
    "K": ("expense",),
}


def suggest_type(accounts, code, ktyp=""):
    """The account type for a new account: the most common type among the company's accounts with
    the same three, then two first digits (so the installed chart decides, as l10n_se classifies
    its accounts); the BAS class otherwise. #KTYP overrides a neighbour of another kind."""
    for size in (3, 2):
        types = Counter(
            acc.account_type for c, acc in accounts.items()
            if c[:size] == code[:size] and len(c) == len(code)
            and acc.account_type not in ("equity_unaffected", "off_balance")
        )
        if types:
            account_type = types.most_common(1)[0][0]
            group = account_type.split("_")[0]
            if not ktyp or group in _GROUP.get(ktyp.upper(), (group,)):
                return account_type
            break
    return sie.suggest_account_type(code, ktyp)


# -- rendering --------------------------------------------------------------------------------------


def _list(env, items, cls):
    if not items:
        return Markup()
    shown = items[:LIST_LIMIT]
    out = Markup("<ul class='mb-0'>") + Markup().join(
        Markup("<li>%s</li>") % item for item in shown)
    if len(items) > LIST_LIMIT:
        out += Markup("<li><em>%s</em></li>") % env._(
            "... and %(count)s more", count=len(items) - LIST_LIMIT)
    out += Markup("</ul>")
    return Markup("<div class='alert %s' role='alert'>") % cls + out + Markup("</div>")


def render(env, a, options):
    """The preview as HTML. Every value from the files is escaped by Markup."""
    _ = env._
    h = Markup()
    if not a.files:
        return h
    # files
    h += Markup("<h5>%s</h5>") % _("Files")
    h += Markup("<table class='table table-sm'><thead><tr>")
    for title in (_("File"), _("Program"), _("Character set"), _("SIE type"), _("Company"),
                  _("Org. no."), _("Checksum")):
        h += Markup("<th>%s</th>") % title
    h += Markup("</tr></thead><tbody>")
    for name, parsed in a.files:
        checksum = {True: _("correct"), False: _("does not match"), None: "-"}[parsed.checksum_ok]
        program = f"{parsed.program} {parsed.program_version}".strip() or "-"
        h += Markup("<tr>") + Markup().join(Markup("<td>%s</td>") % v for v in (
            name, program, parsed.encoding, parsed.sie_type or "1", parsed.company_name or "-",
            parsed.orgnr or "-", checksum)) + Markup("</tr>")
    h += Markup("</tbody></table>")
    # years: what happens to every voucher of the file
    h += Markup("<h5>%s</h5>") % _("Financial years")
    h += Markup("<table class='table table-sm'><thead><tr>")
    for title in (_("Year"), _("Period"), _("File"), _("Accounts"), _("Vouchers"), _("Lines"),
                  _("Series"), _("To import"), _("Already in Odoo"), _("Outside the year"),
                  _("Without lines"), _("Not balanced"), _("Locked"), _("Selected")):
        h += Markup("<th>%s</th>") % title
    h += Markup("</tr></thead><tbody>")
    selected = {y.key for y in a.selected}
    for year in a.years:
        st = a.stats.get(year.key, Counter())
        series = Counter(v.series for v in year.vouchers)
        series_text = ", ".join(f"{s or '-'}: {n}" for s, n in sorted(series.items())) or "-"
        cells = (
            year.label, f"{year.start} – {year.end}", year.filename, len(year.parsed.accounts),
            len(year.vouchers) + len(year.outside),
            sum(len(v.transactions) for v in year.vouchers), series_text,
            st.get("to_import", 0) + st.get("opening", 0), st.get("existing", 0),
            len(year.outside), st.get("empty", 0), st.get("invalid", 0), st.get("locked", 0),
            _("yes") if year.key in selected else _("no"),
        )
        h += Markup("<tr>") + Markup().join(Markup("<td>%s</td>") % c for c in cells)
        h += Markup("</tr>")
    h += Markup("</tbody></table>")
    # summary line
    reconcile = options.get("reconcile")
    if reconcile:
        h += Markup("<p><strong>%s</strong></p>") % _(
            "Reconciliation only: nothing is created. Odoo's posted balances are compared with "
            "the file.")
    elif a.selected:
        h += Markup("<p><strong>%s</strong></p>") % _(
            "%(count)s entries will be created.", count=len(a.items))
    if a.errors:
        h += Markup("<h5 class='text-danger'>%s</h5>") % _("Problems that stop the import")
        h += _list(env, a.errors, "alert-danger")
    if a.warnings:
        h += Markup("<h5>%s</h5>") % _("Warnings")
        h += _list(env, a.warnings, "alert-warning")
    if a.missing_accounts:
        h += Markup("<h5>%s</h5>") % _("Accounts missing in Odoo")
        h += Markup("<table class='table table-sm'><thead><tr><th>%s</th><th>%s</th><th>%s</th>"
                    "</tr></thead><tbody>") % (_("Account"), _("Name"), _("Created as"))
        type_names = dict(env["account.account"]._fields["account_type"]._description_selection(
            env))
        creating = options.get("missing") == "create"
        for code, name, _ktyp, account_type in a.missing_accounts:
            h += Markup("<tr><td>%s</td><td>%s</td><td>%s</td></tr>") % (
                code, name or "-",
                type_names.get(account_type, account_type) if creating else _("(not created)"))
        h += Markup("</tbody></table>")
    if a.dimensions:
        h += Markup("<h5>%s</h5>") % _("Dimensions")
        h += Markup("<table class='table table-sm'><thead><tr><th>%s</th><th>%s</th><th>%s</th>"
                    "<th>%s</th></tr></thead><tbody>") % (
            _("Dimension"), _("Analytic plan"), _("Objects used"), _("New analytic accounts"))
        for dim, name, plan, used, new in a.dimensions:
            plan_text = plan.name if plan else _("%(name)s (new)", name=name)
            h += Markup("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>") % (
                dim, plan_text, used, new)
        h += Markup("</tbody></table>")
    if not reconcile:
        h += Markup("<div class='alert alert-info' role='alert'>%s</div>") % _(
            "SIE files carry no VAT codes. The imported entries have no taxes, so Odoo's tax "
            "report cannot show the VAT of the imported periods: use the source program's VAT "
            "reports for them.")
    if a.infos:
        h += Markup("<h5>%s</h5>") % _("Notes")
        h += _list(env, a.infos, "alert-light")
    return h
