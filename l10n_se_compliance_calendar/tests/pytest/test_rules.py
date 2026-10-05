"""pytest for rules.py, without Odoo.

Run from the repository root:
``python -m pytest -p no:cacheprovider --confcutdir=l10n_se_compliance_calendar/tests/pytest
l10n_se_compliance_calendar/tests/pytest`` (``--confcutdir`` keeps pytest from importing the
Odoo package ``__init__.py`` above it).
The rule engine is loaded from its file so the Odoo package (and its ``odoo`` import) is never
imported.

The expected dates are written out by hand. Sources, fetched 2026-10-01:

* [AGI] Skatteverket, "När ska jag lämna arbetsgivardeklarationen", the two date tables for 2026
  (turnover up to / over SEK 40 million):
  https://www.skatteverket.se/foretag/arbetsgivare/lamnaarbetsgivardeklaration/narskajaglamnaarbetsgivardeklaration.4.361dc8c15312eff6fd13c11.html
* [MOMS] Skatteverket, "När ska jag deklarera moms" (rules per period, the annual table for
  limited companies and economic associations, digital column):
  https://www.skatteverket.se/foretag/moms/deklareramoms/narskajagdeklareramoms.4.6d02084411db6e252fe80008988.html
* [INK2] Skatteverket, "Deklarera åt ett aktiebolag eller en ekonomisk förening" (deadlines for
  financial years ending in 2026, e.g. 2 August 2027):
  https://www.skatteverket.se/foretag/inkomstdeklaration/deklareraatettaktiebolagellerenekonomiskforening.4.46ae6b26141980f1e2d1261.html
* [INK4] Skatteverket, "Deklarera åt ett handelsbolag" (same dates as INK2):
  https://www.skatteverket.se/foretag/inkomstdeklaration/deklareraatetthandelsbolag.4.309a41aa1672ad0c837988b.html
* [VD] Skatteverket, "Viktiga datum" (a date on a Saturday, Sunday, public holiday, Midsummer Eve,
  Christmas Eve or New Year's Eve moves to the next weekday):
  https://www.skatteverket.se/foretag/etjansterochblanketter/viktigadatum.106.5a85666214dbad743ff1737a.html
  The page builds its date lists in the browser and they could not be fetched as text, so the VAT
  lists below are Skatteverket's published rules from [MOMS] applied to the 2026-2027 calendar by
  hand; the employer lists are Skatteverket's own tables [AGI].
* [ÅR] Bolagsverket deadlines as described in the swedish-financial-reporting skill
  (ÅRL 8 kap. 3 §: seven months; ABL 7 kap. 10 §: annual general meeting within six months).
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_RULES = Path(__file__).resolve().parents[2] / "rules.py"
_spec = importlib.util.spec_from_file_location("l10n_se_cc_rules", _RULES)
rules = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rules
_spec.loader.exec_module(rules)

Y2026 = (date(2026, 1, 1), date(2026, 12, 31))
Y2026_27 = (date(2026, 1, 1), date(2027, 12, 31))


def d(*isos):
    return [date.fromisoformat(i) for i in isos]


def dates(profile, kind, start_end=Y2026_27, key_prefix=None):
    return [
        x.date
        for x in rules.compute_deadlines(profile, *start_end)
        if x.kind == kind and (key_prefix is None or x.key.startswith(key_prefix))
    ]


def profile(**kw):
    return rules.Profile(**kw)


# ---------------------------------------------------------------------------
# Employer declaration: Skatteverket's own 2026 tables [AGI]
# ---------------------------------------------------------------------------

# Salaries paid December 2025 ... December 2026, turnover up to SEK 40 million.
AGI_2026_LE40 = d(
    "2026-01-19", "2026-02-12", "2026-03-12", "2026-04-13", "2026-05-12", "2026-06-12",
    "2026-07-13", "2026-08-17", "2026-09-14", "2026-10-12", "2026-11-12", "2026-12-14",
    "2027-01-18",
)
# Same, turnover over SEK 40 million (declaration date; payment is due on the 12th / 17th).
AGI_2026_GT40 = d(
    "2026-01-26", "2026-02-26", "2026-03-26", "2026-04-27", "2026-05-26", "2026-06-26",
    "2026-07-27", "2026-08-26", "2026-09-28", "2026-10-26", "2026-11-26", "2026-12-28",
    "2027-01-26",
)


def test_agi_2026_up_to_40m_matches_skatteverket_table():
    got = dates(profile(employer=True), "employer", (date(2026, 1, 1), date(2027, 1, 31)))
    assert got == AGI_2026_LE40


def test_agi_2026_over_40m_matches_skatteverket_table():
    got = dates(
        profile(employer=True, turnover="gt40"), "employer",
        (date(2026, 1, 1), date(2027, 1, 31)), key_prefix="employer:",
    )
    assert got == AGI_2026_GT40


def test_agi_over_40m_payment_on_12th_17th_january_not_august():
    got = dates(
        profile(employer=True, turnover="gt40"), "employer", Y2026, key_prefix="employer_payment:"
    )
    assert got == d(
        "2026-01-19", "2026-02-12", "2026-03-12", "2026-04-13", "2026-05-12", "2026-06-12",
        "2026-07-13", "2026-08-12", "2026-09-14", "2026-10-12", "2026-11-12", "2026-12-14",
    )


def test_agi_2027_up_to_40m():
    got = dates(profile(employer=True), "employer", (date(2027, 1, 1), date(2027, 12, 31)))
    assert got == d(
        "2027-01-18", "2027-02-12", "2027-03-12", "2027-04-12", "2027-05-12", "2027-06-14",
        "2027-07-12", "2027-08-17", "2027-09-13", "2027-10-12", "2027-11-12", "2027-12-13",
    )


def test_agi_2027_over_40m():
    got = dates(
        profile(employer=True, turnover="gt40"), "employer",
        (date(2027, 1, 1), date(2027, 12, 31)), key_prefix="employer:",
    )
    assert got == d(
        "2027-01-26", "2027-02-26", "2027-03-30", "2027-04-26", "2027-05-26", "2027-06-28",
        "2027-07-26", "2027-08-26", "2027-09-27", "2027-10-26", "2027-11-26", "2027-12-27",
    )


# ---------------------------------------------------------------------------
# VAT [MOMS] + [VD]
# ---------------------------------------------------------------------------


def test_vat_monthly_up_to_40m_2026_2027():
    # 12th of the second month after the period, 17th in January and August.
    assert dates(profile(vat_period="month"), "vat") == d(
        "2026-01-19", "2026-02-12", "2026-03-12", "2026-04-13", "2026-05-12", "2026-06-12",
        "2026-07-13", "2026-08-17", "2026-09-14", "2026-10-12", "2026-11-12", "2026-12-14",
        "2027-01-18", "2027-02-12", "2027-03-12", "2027-04-12", "2027-05-12", "2027-06-14",
        "2027-07-12", "2027-08-17", "2027-09-13", "2027-10-12", "2027-11-12", "2027-12-13",
    )


def test_vat_monthly_over_40m_2026_2027():
    # 26th of the month after the period, 27th in December.
    assert dates(profile(vat_period="month", turnover="gt40"), "vat") == d(
        "2026-01-26", "2026-02-26", "2026-03-26", "2026-04-27", "2026-05-26", "2026-06-26",
        "2026-07-27", "2026-08-26", "2026-09-28", "2026-10-26", "2026-11-26", "2026-12-28",
        "2027-01-26", "2027-02-26", "2027-03-30", "2027-04-26", "2027-05-26", "2027-06-28",
        "2027-07-26", "2027-08-26", "2027-09-27", "2027-10-26", "2027-11-26", "2027-12-27",
    )


def test_vat_quarterly_2026_2027():
    # 12th of the second month after the quarter, 17th in August.
    assert dates(profile(vat_period="quarter"), "vat") == d(
        "2026-02-12", "2026-05-12", "2026-08-17", "2026-11-12",
        "2027-02-12", "2027-05-12", "2027-08-17", "2027-11-12",
    )


def test_vat_annual_limited_company_calendar_year():
    # Financial year Sep-Dec end, digital: 17 August.
    assert dates(profile(vat_period="year"), "vat") == d("2026-08-17", "2027-08-17")


@pytest.mark.parametrize("last_month,last_day,expected", [
    (4, 30, "2026-12-14"),  # Jan-Apr: 12 December (2026-12-12 is a Saturday)
    (6, 30, "2027-01-18"),  # May-Jun: 17 January (2027-01-17 is a Sunday)
    (8, 31, "2027-04-12"),  # Jul-Aug: 12 April
    (12, 31, "2027-08-17"),  # Sep-Dec: 17 August
])
def test_vat_annual_limited_company_by_end_month(last_month, last_day, expected):
    p = profile(vat_period="year", fy_last_month=last_month, fy_last_day=last_day)
    got = [x.date for x in rules.compute_deadlines(p, date(2026, 1, 1), date(2027, 12, 31))
           if x.key == f"vat:fy2026-{last_month:02d}-{last_day:02d}"]
    assert got == d(expected)


def test_vat_annual_economic_association_like_limited_company():
    assert dates(profile(company_form="ek_forening", vat_period="year"), "vat") == d(
        "2026-08-17", "2027-08-17"
    )


def test_vat_annual_sole_trader_without_eu_trade():
    assert dates(profile(company_form="ef", vat_period="year"), "vat") == d(
        "2026-05-12", "2027-05-12"
    )


@pytest.mark.parametrize("form", ["ab", "ef", "ek_forening"])
def test_vat_annual_with_eu_trade(form):
    # 26th of the second month after the year: 26 February for a calendar year.
    p = profile(company_form=form, vat_period="year", eu_trade=True)
    assert dates(p, "vat") == d("2026-02-26", "2027-02-26")


def test_vat_annual_trading_partnership():
    assert dates(profile(company_form="hb", vat_period="year"), "vat") == d(
        "2026-02-26", "2027-02-26"
    )


def test_vat_none():
    assert dates(profile(vat_period="none"), "vat") == []


# ---------------------------------------------------------------------------
# F-tax, EC sales list
# ---------------------------------------------------------------------------


def test_f_tax_up_to_40m_2026():
    assert dates(profile(f_tax=True), "f_tax", Y2026) == d(
        "2026-01-19", "2026-02-12", "2026-03-12", "2026-04-13", "2026-05-12", "2026-06-12",
        "2026-07-13", "2026-08-17", "2026-09-14", "2026-10-12", "2026-11-12", "2026-12-14",
    )


def test_f_tax_over_40m_august_is_12th():
    got = dates(profile(f_tax=True, turnover="gt40"), "f_tax", Y2026)
    assert date(2026, 8, 12) in got and date(2026, 8, 17) not in got
    assert got[0] == date(2026, 1, 19)  # 17 January 2026 is a Saturday


def test_employer_and_f_tax_are_separate_entries():
    items = rules.compute_deadlines(
        profile(employer=True, f_tax=True), date(2026, 3, 1), date(2026, 3, 31)
    )
    assert [(x.key, x.date) for x in items] == [
        ("employer:2026-02", date(2026, 3, 12)),
        ("f_tax:2026-03", date(2026, 3, 12)),
    ]
    assert items[0].title != items[1].title


def test_ec_sales_list_needs_eu_trade():
    assert dates(profile(ec_sales_list="month"), "ec_sales_list") == []


def test_ec_sales_list_quarterly_25th_not_moved():
    got = dates(profile(eu_trade=True, ec_sales_list="quarter"), "ec_sales_list", Y2026)
    # 2026-04-25 and 2026-07-25 are Saturdays: kept (an earlier date is never late).
    assert got == d("2026-01-25", "2026-04-25", "2026-07-25", "2026-10-25")


# ---------------------------------------------------------------------------
# Income tax return [INK2] [INK4]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("last_month,last_day,expected", [
    (4, 30, "2026-12-01"),
    (6, 30, "2027-01-15"),
    (8, 31, "2027-04-01"),
    (12, 31, "2027-08-02"),  # 1 August 2027 is a Sunday; Skatteverket shows 2 August 2027
])
@pytest.mark.parametrize("form", ["ab", "ek_forening", "hb"])
def test_income_tax_return_by_end_month(form, last_month, last_day, expected):
    p = profile(company_form=form, fy_last_month=last_month, fy_last_day=last_day)
    got = [x.date for x in rules.compute_deadlines(p, date(2026, 1, 1), date(2027, 12, 31))
           if x.key == f"income_tax:fy2026-{last_month:02d}-{last_day:02d}"]
    assert got == d(expected)


def test_income_tax_form_names():
    def title(form):
        return next(x.title for x in rules.compute_deadlines(
            profile(company_form=form), *Y2026_27) if x.kind == "income_tax")
    assert "INK2" in title("ab")
    assert "INK2" in title("ek_forening")
    assert "INK4" in title("hb")
    assert "INK1" in title("ef")


def test_income_tax_sole_trader_2_may_moved():
    # 2 May 2026 is a Saturday -> 4 May; 2 May 2027 is a Sunday -> 3 May.
    assert dates(profile(company_form="ef"), "income_tax") == d("2026-05-04", "2027-05-03")


@pytest.mark.parametrize("form", ["ideell_forening", "samfallighet"])
def test_associations_have_no_statutory_company_law_or_ink3_dates(form):
    items = rules.compute_deadlines(profile(company_form=form), *Y2026_27)
    assert items == []


# ---------------------------------------------------------------------------
# Limited company: annual general meeting and annual report [ÅR]
# ---------------------------------------------------------------------------


def test_agm_and_annual_report_calendar_year():
    items = rules.compute_deadlines(profile(), *Y2026)
    got = {x.kind: x.date for x in items if x.kind in ("agm", "annual_report")}
    assert got == {"agm": date(2026, 6, 30), "annual_report": date(2026, 7, 31)}


def test_agm_and_annual_report_broken_year():
    p = profile(fy_last_month=6, fy_last_day=30)
    items = rules.compute_deadlines(p, date(2026, 7, 1), date(2027, 3, 31))
    got = {(x.kind, x.date) for x in items if x.kind in ("agm", "annual_report")}
    assert got == {("agm", date(2026, 12, 31)), ("annual_report", date(2027, 1, 31))}
    p = profile(fy_last_month=8, fy_last_day=31)
    items = rules.compute_deadlines(p, date(2026, 9, 1), date(2027, 4, 30))
    got = {(x.kind, x.date) for x in items if x.kind in ("agm", "annual_report")}
    assert got == {("agm", date(2027, 2, 28)), ("annual_report", date(2027, 3, 31))}


def test_annual_report_not_moved_from_saturday():
    # 31 July 2027 is a Saturday; the statutory date is shown as is.
    items = rules.compute_deadlines(profile(), date(2027, 7, 1), date(2027, 8, 31))
    assert [x.date for x in items if x.kind == "annual_report"] == [date(2027, 7, 31)]


def test_kinds_switch_off():
    p = profile(vat_period="month", employer=True, kinds=frozenset({"employer"}))
    kinds = {x.kind for x in rules.compute_deadlines(p, *Y2026)}
    assert kinds == {"employer"}


# ---------------------------------------------------------------------------
# Public holidays and moving to the next weekday
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("year,easter", [
    (2024, "2024-03-31"), (2025, "2025-04-20"), (2026, "2026-04-05"),
    (2027, "2027-03-28"), (2028, "2028-04-16"), (2038, "2038-04-25"),
])
def test_easter(year, easter):
    assert rules.easter_sunday(year) == date.fromisoformat(easter)


def test_holidays_2026_2027():
    expected = {
        2026: d("2026-01-01", "2026-01-06", "2026-04-03", "2026-04-05", "2026-04-06",
                "2026-05-01", "2026-05-14", "2026-05-24", "2026-06-06", "2026-06-19",
                "2026-06-20", "2026-10-31", "2026-12-24", "2026-12-25", "2026-12-26",
                "2026-12-31"),
        2027: d("2027-01-01", "2027-01-06", "2027-03-26", "2027-03-28", "2027-03-29",
                "2027-05-01", "2027-05-06", "2027-05-16", "2027-06-06", "2027-06-25",
                "2027-06-26", "2027-11-06", "2027-12-24", "2027-12-25", "2027-12-26",
                "2027-12-31"),
    }
    for year, days in expected.items():
        assert sorted(rules.swedish_holidays(year)) == days


@pytest.mark.parametrize("nominal,expected", [
    ("2027-03-26", "2027-03-30"),  # Good Friday -> Saturday, Easter Sunday, Easter Monday
    ("2026-04-03", "2026-04-07"),  # Good Friday 2026
    ("2026-05-14", "2026-05-15"),  # Ascension Day
    ("2027-06-25", "2027-06-28"),  # Midsummer Eve (Friday)
    ("2026-06-19", "2026-06-22"),  # Midsummer Eve 2026
    ("2026-12-24", "2026-12-28"),  # Christmas Eve, Christmas Day, Boxing Day, Sunday
    ("2026-12-31", "2027-01-04"),  # New Year's Eve, New Year's Day, weekend
    ("2027-01-06", "2027-01-07"),  # Epiphany
    ("2025-08-17", "2025-08-18"),  # 17 August on a Sunday
    ("2030-08-17", "2030-08-19"),  # 17 August on a Saturday
    ("2026-08-17", "2026-08-17"),  # a Monday stays
])
def test_next_business_day(nominal, expected):
    assert rules.next_business_day(date.fromisoformat(nominal)) == date.fromisoformat(expected)


def test_17_august_on_weekend_in_rules():
    # Quarterly VAT for April-June 2025 and July salaries: 17 August 2025 is a Sunday.
    p = profile(vat_period="quarter", employer=True, f_tax=True,
                kinds=frozenset({"vat", "employer", "f_tax"}))
    items = rules.compute_deadlines(p, date(2025, 8, 1), date(2025, 8, 31))
    assert {x.kind for x in items} == {"vat", "employer", "f_tax"}
    assert {x.date for x in items} == {date(2025, 8, 18)}
    assert {x.nominal_date for x in items} == {date(2025, 8, 17)}
    assert all("2025-08-17" in x.description for x in items)


def test_keys_are_stable_and_unique():
    p = profile(vat_period="month", employer=True, f_tax=True, eu_trade=True,
                ec_sales_list="month", turnover="gt40")
    first = rules.compute_deadlines(p, date(2026, 1, 1), date(2028, 12, 31))
    second = rules.compute_deadlines(p, date(2026, 1, 1), date(2028, 12, 31))
    keys = [x.key for x in first]
    assert len(keys) == len(set(keys))
    assert keys == [x.key for x in second]


def test_custom_rule_helpers():
    assert rules.clamped_date(2028, 2, 31) == date(2028, 2, 29)
    assert rules.clamped_date(2027, 2, 31) == date(2027, 2, 28)
    anchor = date(2027, 4, 24)
    assert rules.offset_date(anchor, 6, "weeks", "before") == date(2027, 3, 13)
    assert rules.offset_date(anchor, 14, "days", "before") == date(2027, 4, 10)
    assert rules.offset_date(anchor, 1, "months", "after") == date(2027, 5, 24)
    assert rules.adjust_non_business_day(date(2027, 3, 13), "previous") == date(2027, 3, 12)
    assert rules.adjust_non_business_day(date(2027, 3, 13), "next") == date(2027, 3, 15)
    assert rules.adjust_non_business_day(date(2027, 3, 13), "keep") == date(2027, 3, 13)


def test_translation_callable_is_used():
    seen = []

    def fake(source, *args, **kwargs):
        seen.append(source)
        return "X:" + (source % (args or kwargs) if (args or kwargs) else source)

    items = rules.compute_deadlines(profile(vat_period="quarter"), *Y2026, _=fake)
    assert items and all(x.title.startswith("X:") for x in items)
    assert "VAT return for %(period)s" in seen
