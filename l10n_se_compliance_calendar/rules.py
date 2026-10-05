"""Statutory deadlines for Swedish companies and associations, in plain Python.

No Odoo import, no network: everything is computed from the law and a company profile, so the
module can be tested without a database (``tests/pytest``) and imported by the Odoo layer as
``odoo.addons.l10n_se_compliance_calendar.rules``.

Main entry point: :func:`compute_deadlines` (profile + date interval -> list of :class:`Deadline`).

Sources (checked 2026-10-01): Skatteverket, "När ska jag deklarera moms", "När ska jag lämna
arbetsgivardeklarationen" (with its date tables for 2026), "Deklarera åt ett aktiebolag eller en
ekonomisk förening", "Deklarera åt ett handelsbolag", and the notice on "Viktiga datum": a date for
declaring and paying VAT and employer contributions that falls on a Saturday, Sunday, public
holiday, Midsummer Eve, Christmas Eve or New Year's Eve moves to the next weekday.

Strings are English source strings wrapped in ``_()`` so that Odoo extracts them for ``i18n/``;
the Odoo layer passes ``env._`` as ``_``. Without it they are returned untranslated.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Profile and result
# ---------------------------------------------------------------------------

COMPANY_FORMS = ("ab", "ef", "hb", "ek_forening", "ideell_forening", "samfallighet")
VAT_PERIODS = ("none", "month", "quarter", "year")
TURNOVER_CLASSES = ("le40", "gt40")
EC_SALES_LIST_PERIODS = ("none", "month", "quarter")

#: Kinds of statutory deadline; each can be switched off in :attr:`Profile.kinds`.
KINDS = (
    "vat",
    "ec_sales_list",
    "employer",
    "f_tax",
    "income_tax",
    "agm",
    "annual_report",
)


@dataclass(frozen=True)
class Profile:
    """What the rules need to know about a company."""

    company_form: str = "ab"
    vat_period: str = "none"
    turnover: str = "le40"  # VAT base up to SEK 40 million ("le40") or above ("gt40")
    eu_trade: bool = False
    ec_sales_list: str = "none"
    employer: bool = False
    f_tax: bool = False
    fy_last_month: int = 12
    fy_last_day: int = 31
    kinds: frozenset = field(default_factory=lambda: frozenset(KINDS))

    def __post_init__(self):
        if self.company_form not in COMPANY_FORMS:
            raise ValueError(f"unknown company form {self.company_form!r}")
        if self.vat_period not in VAT_PERIODS:
            raise ValueError(f"unknown VAT period {self.vat_period!r}")
        if self.turnover not in TURNOVER_CLASSES:
            raise ValueError(f"unknown turnover class {self.turnover!r}")
        if self.ec_sales_list not in EC_SALES_LIST_PERIODS:
            raise ValueError(f"unknown EC sales list period {self.ec_sales_list!r}")
        if not 1 <= self.fy_last_month <= 12 or not 1 <= self.fy_last_day <= 31:
            raise ValueError("invalid end of financial year")


@dataclass(frozen=True)
class Deadline:
    """One date in the calendar. ``key`` is stable per rule and period."""

    key: str
    date: date
    title: str
    description: str
    legal_ref: str
    kind: str
    nominal_date: date  # before moving to the next weekday; equal to ``date`` if not moved


def _identity(source, *args, **kwargs):
    if args or kwargs:
        return source % (args or kwargs)
    return source


# ---------------------------------------------------------------------------
# Calendar: Swedish public holidays and weekdays
# ---------------------------------------------------------------------------


def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _saturday_between(year: int, month: int, first_day: int) -> date:
    """The Saturday in the 7-day window starting at ``year-month-first_day``."""
    start = date(year, month, first_day)
    return start + timedelta(days=(5 - start.weekday()) % 7)


def swedish_holidays(year: int) -> dict[date, str]:
    """Public holidays (lag 1989:253 om allmänna helgdagar) plus the eves on which a deadline
    also moves (Midsummer Eve, Christmas Eve, New Year's Eve). Sundays are not listed."""
    easter = easter_sunday(year)
    midsummer_day = _saturday_between(year, 6, 20)  # Saturday 20-26 June
    all_saints = _saturday_between(year, 10, 31)  # Saturday 31 October - 6 November
    return {
        date(year, 1, 1): "Nyårsdagen",
        date(year, 1, 6): "Trettondedag jul",
        easter - timedelta(days=2): "Långfredagen",
        easter: "Påskdagen",
        easter + timedelta(days=1): "Annandag påsk",
        date(year, 5, 1): "Första maj",
        easter + timedelta(days=39): "Kristi himmelsfärdsdag",
        easter + timedelta(days=49): "Pingstdagen",
        date(year, 6, 6): "Sveriges nationaldag",
        midsummer_day - timedelta(days=1): "Midsommarafton",
        midsummer_day: "Midsommardagen",
        all_saints: "Alla helgons dag",
        date(year, 12, 24): "Julafton",
        date(year, 12, 25): "Juldagen",
        date(year, 12, 26): "Annandag jul",
        date(year, 12, 31): "Nyårsafton",
    }


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in swedish_holidays(day.year)


def next_business_day(day: date) -> date:
    """``day`` itself if it is a weekday that is not a holiday or eve, otherwise the next one."""
    while not is_business_day(day):
        day += timedelta(days=1)
    return day


def previous_business_day(day: date) -> date:
    while not is_business_day(day):
        day -= timedelta(days=1)
    return day


# ---------------------------------------------------------------------------
# Date arithmetic
# ---------------------------------------------------------------------------


def _month_shift(year: int, month: int, months: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + months
    return index // 12, index % 12 + 1


def last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def clamped_date(year: int, month: int, day: int) -> date:
    """``day`` of the month, or its last day if the month is shorter (31 -> 30 April)."""
    return date(year, month, min(day, last_day_of_month(year, month)))


def add_months(day: date, months: int) -> date:
    """Calendar months; the last day of a month maps to the last day of the target month
    (31 December + 6 months = 30 June), other days are clamped to the target month."""
    year, month = _month_shift(day.year, day.month, months)
    if day.day == last_day_of_month(day.year, day.month):
        return date(year, month, last_day_of_month(year, month))
    return clamped_date(year, month, day.day)


def fiscal_year_end(profile: Profile, year: int) -> date:
    return clamped_date(year, profile.fy_last_month, profile.fy_last_day)


def offset_date(anchor: date, amount: int, unit: str, direction: str) -> date:
    """``amount`` days, weeks or months before or after ``anchor``."""
    if amount < 0:
        raise ValueError("offset must not be negative")
    sign = -1 if direction == "before" else 1
    if unit == "days":
        return anchor + timedelta(days=sign * amount)
    if unit == "weeks":
        return anchor + timedelta(weeks=sign * amount)
    if unit == "months":
        return add_months(anchor, sign * amount)
    raise ValueError(f"unknown unit {unit!r}")


def adjust_non_business_day(day: date, mode: str) -> date:
    """``keep``, ``next`` (next weekday) or ``previous`` (previous weekday)."""
    if mode == "next":
        return next_business_day(day)
    if mode == "previous":
        return previous_business_day(day)
    return day


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def _month_names(_):
    return (
        _("January"), _("February"), _("March"), _("April"), _("May"), _("June"),
        _("July"), _("August"), _("September"), _("October"), _("November"), _("December"),
    )


def _quarter_label(_, year: int, quarter: int) -> str:
    labels = (
        _("January–March %(year)s", year=year),
        _("April–June %(year)s", year=year),
        _("July–September %(year)s", year=year),
        _("October–December %(year)s", year=year),
    )
    return labels[quarter - 1]


def _month_label(_, year: int, month: int) -> str:
    return f"{_month_names(_)[month - 1]} {year}"


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

LAW_SFL = "skatteförfarandelagen (2011:1244)"
LAW_MOVE = "lag (1930:173) om beräkning av lagstadgad tid"


class _Collector:
    def __init__(self, profile, start, end, _):
        self.profile = profile
        self.start = start
        self.end = end
        self._ = _
        self.items = []

    def add(self, key, nominal, title, description, legal_ref, kind, move=True):
        actual = next_business_day(nominal) if move else nominal
        if not self.start <= actual <= self.end:
            return
        _ = self._
        if actual != nominal:
            moved = _(
                "The last day, %(nominal)s, is not a weekday, so the next weekday applies.",
                nominal=nominal.isoformat(),
            )
            description = f"{description} {moved}"
        self.items.append(Deadline(key, actual, title, description, legal_ref, kind, nominal))


def _years(start: date, end: date):
    # Periods ending up to two years before ``start`` can still have a deadline inside it.
    return range(start.year - 2, end.year + 1)


def _vat_deadlines(c: _Collector):
    p, _ = c.profile, c._
    if p.vat_period == "none":
        return
    for year in _years(c.start, c.end):
        if p.vat_period == "month":
            for month in range(1, 13):
                label = _month_label(_, year, month)
                if p.turnover == "gt40":
                    # 26th of the month after the period; for November the 27th of December.
                    dy, dm = _month_shift(year, month, 1)
                    nominal = date(dy, dm, 27 if dm == 12 else 26)
                    legal = f"26 kap. 30 § {LAW_SFL}"
                else:
                    # 12th of the second month after the period; 17th in January and August.
                    dy, dm = _month_shift(year, month, 2)
                    nominal = date(dy, dm, 17 if dm in (1, 8) else 12)
                    legal = f"26 kap. 26 § {LAW_SFL}"
                c.add(
                    f"vat:{year}-{month:02d}",
                    nominal,
                    _("VAT return for %(period)s", period=label),
                    _(
                        "The VAT return for %(period)s must have reached the Swedish Tax Agency "
                        "and the VAT must be paid.",
                        period=label,
                    ),
                    legal,
                    "vat",
                )
        elif p.vat_period == "quarter":
            for quarter in range(1, 5):
                label = _quarter_label(_, year, quarter)
                # 12th of the second month after the quarter; 17th in August.
                dy, dm = _month_shift(year, quarter * 3, 2)
                nominal = date(dy, dm, 17 if dm == 8 else 12)
                c.add(
                    f"vat:{year}-q{quarter}",
                    nominal,
                    _("VAT return for %(period)s", period=label),
                    _(
                        "The VAT return for %(period)s must have reached the Swedish Tax Agency "
                        "and the VAT must be paid.",
                        period=label,
                    ),
                    f"26 kap. 26 § {LAW_SFL}",
                    "vat",
                )
        else:
            fy_end = fiscal_year_end(p, year)
            nominal = _annual_vat_date(p, fy_end)
            c.add(
                f"vat:fy{fy_end.isoformat()}",
                nominal,
                _("Annual VAT return, financial year ending %(date)s", date=fy_end.isoformat()),
                _(
                    "The VAT return for the financial year ending %(date)s must have reached the "
                    "Swedish Tax Agency and the VAT must be paid.",
                    date=fy_end.isoformat(),
                ),
                f"26 kap. 33–33 b §§ {LAW_SFL}",
                "vat",
            )


def _annual_vat_date(p: Profile, fy_end: date) -> date:
    """Annual VAT return (redovisningsperiod = beskattningsår), Skatteverket's rules:

    * EU trade: the 26th of the second month after the year (the 27th if that is December);
    * sole trader (ef) without EU trade: 12 May the year after;
    * trading partnership (hb): the 26th of the second month after the year;
    * limited company / economic association without EU trade: by the end month of the
      financial year, the dates for a digital return (Jan-Apr: 12 Dec, May-Jun: 17 Jan,
      Jul-Aug: 12 Apr, Sep-Dec: 17 Aug);
    * non-profit association / joint property association: treated as a legal person that does
      not file an income tax return, i.e. the 26th of the second month after the year.
    """
    year, month = fy_end.year, fy_end.month
    if p.eu_trade or p.company_form in ("hb", "ideell_forening", "samfallighet"):
        dy, dm = _month_shift(year, month, 2)
        return date(dy, dm, 27 if dm == 12 else 26)
    if p.company_form == "ef":
        return date(year + 1, 5, 12)
    if month <= 4:
        return date(year, 12, 12)
    if month <= 6:
        return date(year + 1, 1, 17)
    if month <= 8:
        return date(year + 1, 4, 12)
    return date(year + 1, 8, 17)


def _ec_sales_list_deadlines(c: _Collector):
    p, _ = c.profile, c._
    if not p.eu_trade or p.ec_sales_list == "none":
        return
    for year in _years(c.start, c.end):
        if p.ec_sales_list == "month":
            periods = [(f"{year}-{m:02d}", m, _month_label(_, year, m)) for m in range(1, 13)]
        else:
            periods = [(f"{year}-q{q}", q * 3, _quarter_label(_, year, q)) for q in range(1, 5)]
        for key, end_month, label in periods:
            # Electronic filing: the 25th of the month after the period (paper: the 20th).
            dy, dm = _month_shift(year, end_month, 1)
            c.add(
                f"ec_sales_list:{key}",
                date(dy, dm, 25),
                _("EC sales list for %(period)s", period=label),
                _(
                    "The EC sales list (periodisk sammanställning) for %(period)s must have reached "
                    "the Swedish Tax Agency (electronic filing; on paper the 20th).",
                    period=label,
                ),
                f"35 kap. 9 § {LAW_SFL}",
                "ec_sales_list",
                move=False,
            )


def _payment_day(month: int, large: bool) -> int:
    """Due day for tax payments: the 12th, in January the 17th, in August the 17th only for
    those with a VAT base up to SEK 40 million."""
    if month == 1 or (month == 8 and not large):
        return 17
    return 12


def _employer_deadlines(c: _Collector):
    p, _ = c.profile, c._
    if not p.employer:
        return
    large = p.turnover == "gt40"
    for year in _years(c.start, c.end):
        for month in range(1, 13):
            label = _month_label(_, year, month)
            dy, dm = _month_shift(year, month, 1)
            if large:
                c.add(
                    f"employer:{year}-{month:02d}",
                    date(dy, dm, 27 if dm == 12 else 26),
                    _("Employer declaration for %(period)s", period=label),
                    _(
                        "The employer declaration (AGI) for salaries paid in %(period)s must have "
                        "reached the Swedish Tax Agency.",
                        period=label,
                    ),
                    f"26 kap. {LAW_SFL}",
                    "employer",
                )
                c.add(
                    f"employer_payment:{year}-{month:02d}",
                    date(dy, dm, _payment_day(dm, True)),
                    _("Employer contributions and tax for %(period)s", period=label),
                    _(
                        "Employer contributions and tax withheld for %(period)s must be paid.",
                        period=label,
                    ),
                    f"62 kap. 3 § {LAW_SFL}",
                    "employer",
                )
            else:
                c.add(
                    f"employer:{year}-{month:02d}",
                    date(dy, dm, _payment_day(dm, False)),
                    _("Employer declaration for %(period)s", period=label),
                    _(
                        "The employer declaration (AGI) for salaries paid in %(period)s must have "
                        "reached the Swedish Tax Agency, and employer contributions and tax "
                        "withheld must be paid.",
                        period=label,
                    ),
                    f"26 kap. och 62 kap. 3 § {LAW_SFL}",
                    "employer",
                )


def _f_tax_deadlines(c: _Collector):
    p, _ = c.profile, c._
    if not p.f_tax:
        return
    large = p.turnover == "gt40"
    for year in _years(c.start, c.end):
        for month in range(1, 13):
            c.add(
                f"f_tax:{year}-{month:02d}",
                date(year, month, _payment_day(month, large)),
                _("Preliminary tax (F-tax) for %(period)s", period=_month_label(_, year, month)),
                _(
                    "The preliminary tax (F-skatt) charged for the month must be on the tax "
                    "account. Amount and reference are in the preliminary tax decision."
                ),
                f"62 kap. 3 § {LAW_SFL}",
                "f_tax",
            )


_INCOME_TAX_FORMS = {
    "ab": "INK2",
    "ek_forening": "INK2",
    "hb": "INK4",
    "ef": "INK1/NE",
}


def _income_tax_deadlines(c: _Collector):
    p, _ = c.profile, c._
    form = _INCOME_TAX_FORMS.get(p.company_form)
    if not form:
        # Non-profit and joint property associations (INK3): not computed, see README.
        return
    for year in _years(c.start, c.end):
        fy_end = fiscal_year_end(p, year)
        if p.company_form == "ef":
            # Income tax return 1 with NE: 2 May the year after the tax year.
            nominal = date(year + 1, 5, 2)
            title = _("Income tax return (INK1 with NE) for %(year)s", year=year)
            description = _(
                "The income tax return with the NE appendix for %(year)s must have reached the "
                "Swedish Tax Agency.",
                year=year,
            )
        else:
            # Digital filing; on paper one month earlier.
            month = fy_end.month
            if month <= 4:
                nominal = date(year, 12, 1)
            elif month <= 6:
                nominal = date(year + 1, 1, 15)
            elif month <= 8:
                nominal = date(year + 1, 4, 1)
            else:
                nominal = date(year + 1, 8, 1)
            title = _(
                "Income tax return (%(form)s), financial year ending %(date)s",
                form=form,
                date=fy_end.isoformat(),
            )
            description = _(
                "The income tax return (%(form)s) for the financial year ending %(date)s must have "
                "reached the Swedish Tax Agency (digital filing; on paper one month earlier).",
                form=form,
                date=fy_end.isoformat(),
            )
        c.add(
            f"income_tax:fy{fy_end.isoformat()}",
            nominal,
            title,
            description,
            f"30 kap. {LAW_SFL}",
            "income_tax",
        )


def _company_law_deadlines(c: _Collector):
    p, _ = c.profile, c._
    if p.company_form != "ab":
        return
    for year in _years(c.start, c.end):
        fy_end = fiscal_year_end(p, year)
        if "agm" in p.kinds:
            c.add(
                f"agm:fy{fy_end.isoformat()}",
                add_months(fy_end, 6),
                _("Annual general meeting, financial year ending %(date)s", date=fy_end.isoformat()),
                _(
                    "The annual general meeting that adopts the annual report for the financial "
                    "year ending %(date)s must be held at the latest on this day (six months).",
                    date=fy_end.isoformat(),
                ),
                "7 kap. 10 § aktiebolagslagen (2005:551)",
                "agm",
                move=False,
            )
        if "annual_report" in p.kinds:
            c.add(
                f"annual_report:fy{fy_end.isoformat()}",
                add_months(fy_end, 7),
                _("Annual report to the Companies Registration Office, financial year ending %(date)s",
                  date=fy_end.isoformat()),
                _(
                    "The adopted annual report for the financial year ending %(date)s must have "
                    "reached Bolagsverket (seven months).",
                    date=fy_end.isoformat(),
                ),
                "8 kap. 3 § årsredovisningslagen (1995:1554)",
                "annual_report",
                move=False,
            )


def compute_deadlines(profile: Profile, start: date, end: date, _=None) -> list[Deadline]:
    """All statutory deadlines for ``profile`` with a date in ``[start, end]``, sorted.

    Tax deadlines (VAT, employer declaration, F-tax, income tax return) that fall on a Saturday,
    Sunday, public holiday, Midsummer Eve, Christmas Eve or New Year's Eve move to the next
    weekday. The EC sales list, the annual general meeting and the annual report keep their
    nominal date (the earlier date is never late).
    """
    c = _Collector(profile, start, end, _ or _identity)
    if "vat" in profile.kinds:
        _vat_deadlines(c)
    if "ec_sales_list" in profile.kinds:
        _ec_sales_list_deadlines(c)
    if "employer" in profile.kinds:
        _employer_deadlines(c)
    if "f_tax" in profile.kinds:
        _f_tax_deadlines(c)
    if "income_tax" in profile.kinds:
        _income_tax_deadlines(c)
    _company_law_deadlines(c)
    return sorted(c.items, key=lambda d: (d.date, d.key))
