"""pytest for lib/sie.py, without Odoo.

Run from the repository root:
``python -m pytest -p no:cacheprovider --confcutdir=l10n_se_sie4/tests/pytest
l10n_se_sie4/tests/pytest``.
The library is loaded from its file so the Odoo package (and its ``odoo`` import) is never
imported.

The files in ``tests/data`` are synthetic: an invented company with an invented organisation
number (it fails the check digit on purpose) and invented vouchers. ``fortnox_2024.se`` and
``fortnox_2025.se`` follow the layout of a Fortnox export: #RTRANS/#BTRANS, #PSALDO, #PBUDGET,
#SRU, #DIM/#OBJEKT, two #RAR, code page 437 with å/ä/ö, CRLF.
"""

import importlib.util
import sys
import zlib
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

_MODULE = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("l10n_se_sie4_sie", _MODULE / "lib" / "sie.py")
sie = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sie
_spec.loader.exec_module(sie)

DATA = _MODULE / "tests" / "data"


def load(name, **kw):
    return sie.parse((DATA / name).read_bytes(), **kw)


def codes(parsed, severity=None):
    return [(i.code, i.line) for i in parsed.issues if severity in (None, i.severity)]


def lines(*records):
    return ("\r\n".join(records) + "\r\n").encode("cp437")


HEADER = ('#FLAGGA 0', '#PROGRAM "Test" 1', '#FORMAT PC8', '#GEN 20250101', '#SIETYP 4',
          '#FNAMN "Testbolaget"', '#RAR 0 20250101 20251231')  # fmt: skip


# -- tokenizer -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("#KONTO 1930 Bank", ["#KONTO", "1930", "Bank"]),
        ('#KONTO 1930 "Bank konto"', ["#KONTO", "1930", "Bank konto"]),
        ("#KONTO\t1930\t\t\"Bank\"", ["#KONTO", "1930", "Bank"]),
        ('#KONTO 1915 "Kassa \\"special\\""', ["#KONTO", "1915", 'Kassa "special"']),
        ('#VER "" "" 20080101 "Porto"', ["#VER", "", "", "20080101", "Porto"]),
        ("#TRANS 1910 {} -1000.00", ["#TRANS", "1910", (), "-1000.00"]),
        ("#TRANS 1910 { } -1000.00", ["#TRANS", "1910", (), "-1000.00"]),
        ('#TRANS 7010 {"1" "456" "7" "47"} 13200.00',
         ["#TRANS", "7010", ("1", "456", "7", "47"), "13200.00"]),
        ('#TRANS 7010 {1 "456" 7 "47"} 13200.00 20080101 "Lön" 2 "AN"',
         ["#TRANS", "7010", ("1", "456", "7", "47"), "13200.00", "20080101", "Lön", "2", "AN"]),
        ('#OBJEKT 1 "0123" "Avd {med} klammer"', ["#OBJEKT", "1", "0123", "Avd {med} klammer"]),
        ('#PSALDO 0 200801 4010 {20 "01"} 49655.00', ["#PSALDO", "0", "200801", "4010",
                                                      ("20", "01"), "49655.00"]),
        ('#KONTO 1930 "C:\\temp"', ["#KONTO", "1930", "C:\\temp"]),
    ],
)
def test_tokenize(text, expected):
    tokens = sie.tokenize(text)
    assert tokens == expected
    for tok, exp in zip(tokens, expected, strict=True):
        assert isinstance(tok, sie.ObjectList) == isinstance(exp, tuple)


@pytest.mark.parametrize(
    "text, code",
    [
        ('#KONTO 1930 "Bank', "unclosed_quote"),
        ("#TRANS 1930 {1 \"10\" -100", "unclosed_objects"),
        ("#TRANS 1930 {1 {2}} -100", "nested_objects"),
    ],
)
def test_tokenize_errors(text, code):
    with pytest.raises(sie.SieParseError) as exc:
        sie.tokenize(text, line=7)
    assert exc.value.code == code
    assert exc.value.line == 7
    assert "line 7" in str(exc.value)


# -- encoding --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, encoding",
    [
        ("fortnox_2025.se", "cp437"),
        ("utf8_bom.se", "utf-8-sig"),
        ("utf8.se", "utf-8"),
        ("cp1252.se", "cp1252"),
    ],
)
def test_encoding_detection(name, encoding):
    parsed = load(name)
    assert parsed.encoding == encoding
    if encoding != "cp437":
        assert ("encoding", None) in codes(parsed, sie.INFO)


@pytest.mark.parametrize("name", ["utf8_bom.se", "utf8.se", "cp1252.se"])
def test_same_text_in_every_encoding(name):
    parsed = load(name)
    assert parsed.accounts["6250"].name == "Postbefordran, å ä ö Å Ä Ö"
    assert parsed.vouchers[0].text == "Frimärken åäö"
    assert parsed.vouchers[0].transactions[0].amount == Decimal("120.00")
    assert not parsed.errors


def test_decode_ascii_is_cp437():
    text, encoding = sie.decode(b"#FLAGGA 0\r\n")
    assert encoding == "cp437"
    assert text == "#FLAGGA 0\r\n"


def test_cp437_letters_are_not_utf8():
    # å ä ö in code page 437 are continuation bytes in UTF-8: never valid UTF-8.
    data = '#FNAMN "Åkerö Bär"'.encode("cp437")
    assert sie.decode(data) == ('#FNAMN "Åkerö Bär"', "cp437")


# -- a Fortnox-like file ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def f2025():
    return load("fortnox_2025.se")


@pytest.fixture(scope="module")
def f2024():
    return load("fortnox_2024.se")


def test_header(f2025):
    assert not f2025.issues
    assert f2025.flag == "0"
    assert (f2025.program, f2025.program_version) == ("Fortnox", "3.57.11")
    assert f2025.format == "PC8"
    assert f2025.generated == date(2026, 1, 15)
    assert f2025.generated_by == "Exportör"
    assert f2025.sie_type == "4"
    assert f2025.company_id == "NORRSKEN1"
    assert f2025.orgnr == "559000-0000"
    assert f2025.company_name == "Exempelbolaget Norrsken AB"
    assert f2025.address == ("Ekonomiavdelningen", "Exempelgatan 1", "123 45 Exempelby",
                             "000-000 00")  # fmt: skip
    assert f2025.tax_year == "2026"
    assert f2025.balances_until == date(2025, 12, 31)
    assert f2025.chart_type == "BAS2024"
    assert f2025.currency == "SEK"


def test_years(f2025):
    assert sorted(f2025.fiscal_years) == [-1, 0]
    fy = f2025.year(0)
    assert (fy.start, fy.end, fy.label) == (date(2025, 1, 1), date(2025, 12, 31), "2025")
    assert f2025.year(-1).label == "2024"
    assert f2025.year_of(date(2024, 6, 1)).index == -1
    assert f2025.year_of(date(2026, 1, 1)) is None


def test_broken_year_label():
    fy = sie.FiscalYear(0, date(2024, 7, 1), date(2025, 6, 30))
    assert fy.label == "2024/25"


def test_chart(f2025):
    acc = f2025.accounts["2611"]
    assert acc.name == "Utgående moms på försäljning inom Sverige, 25 %"
    assert acc.type == "S"
    assert acc.sru == ["7369"]
    assert f2025.accounts["3001"].type == "I"
    assert f2025.accounts["1930"].name == "Företagskonto/checkkonto/affärskonto"
    assert f2025.dimensions["1"].name == "Kostnadsställe"
    assert f2025.dimensions["6"].name == "Projekt"
    assert f2025.objects[("1", "10")].name == "Butik Östra"
    assert f2025.objects[("6", "P1")].name == "Ombyggnad kontor"


def test_balances(f2025, f2024):
    ib = f2025.amounts("opening")
    assert ib["1930"] == Decimal("103900.50")
    assert sum(ib.values()) == 0
    # the year is not closed: the balance sheet balances together with the result
    ub, res = f2025.amounts("closing"), f2025.amounts("results")
    assert sum(ub.values()) + sum(res.values()) == 0
    assert sum(res.values()) != 0
    # previous year in the same file and in its own file
    assert f2025.amounts("closing", -1) == f2024.amounts("closing")
    assert f2025.amounts("results", -1) == f2024.amounts("results")
    # IB of 2025 = UB of 2024
    assert f2025.amounts("opening") == f2024.amounts("closing")


def test_period_records(f2025):
    psaldo = [p for p in f2025.period_balances if p.account == "3001"]
    assert {(p.period, p.objects, p.amount) for p in psaldo} == {
        ("202501", (), Decimal("-20000.00")),
        ("202501", (("1", "10"),), Decimal("-10000.00")),
    }
    assert len(f2025.period_budgets) == 2
    budget = [p for p in f2025.period_budgets if p.account == "5010"][0]
    assert (budget.period, budget.amount, budget.quantity) == ("202502", Decimal("8000.00"),
                                                               Decimal("1"))  # fmt: skip


def test_vouchers(f2025):
    assert len(f2025.vouchers) == 7
    assert f2025.series() == {"A": 5, "B": 2}
    first = f2025.vouchers[0]
    assert (first.series, first.number, first.date, first.text, first.regdate) == (
        "A", "1", date(2025, 1, 10), "Försäljning, Åtta kunder", date(2025, 1, 10))
    assert first.transactions[1].objects == (("1", "10"), ("6", "P1"))
    assert all(v.is_balanced for v in f2025.vouchers)


def test_rtrans_counted_once(f2025):
    """#RTRANS is followed by an identical #TRANS (SIE 4B, #RTRANS); only #TRANS is booked."""
    porto = f2025.vouchers[1]
    assert porto.text == "Porto"
    assert len(porto.added) == 3
    assert len(porto.transactions) == 6
    assert porto.is_balanced
    booked = defaultdict(Decimal)
    for t in porto.transactions:
        booked[t.account] += t.amount
    assert booked == {"1930": Decimal("-1000.00"), "2641": Decimal("200.00"),
                      "6250": Decimal("800.00")}  # fmt: skip
    # counting the #RTRANS lines as well would book them twice
    doubled = defaultdict(Decimal)
    for t in porto.transactions + porto.added:
        doubled[t.account] += t.amount
    assert doubled["1930"] == Decimal("-800.00")


def test_btrans_not_booked(f2025):
    payment = f2025.vouchers[2]
    assert len(payment.removed) == 2
    assert [(t.account, t.amount) for t in payment.transactions] == [
        ("1930", Decimal("25000.00")), ("1510", Decimal("-25000.00"))]


@pytest.mark.parametrize("name", ["fortnox_2024.se", "fortnox_2025.se"])
def test_vouchers_add_up_to_closing_balances(name):
    """IB + the booked lines = UB for balance sheet accounts; booked lines = RES for result
    accounts. Fails if #RTRANS or #BTRANS were booked."""
    parsed = load(name)
    ib = parsed.amounts("opening")
    ub = parsed.amounts("closing")
    res = parsed.amounts("results")
    moved = defaultdict(Decimal)
    for v in parsed.vouchers_in(parsed.year(0)):
        for t in v.transactions:
            moved[t.account] += t.amount
    for account in set(ib) | set(ub) | set(res) | set(moved):
        if sie.account_class(account) == "balance":
            assert ib.get(account, 0) + moved.get(account, 0) == ub.get(account, 0), account
        else:
            assert moved.get(account, 0) == res.get(account, 0), account


def test_escaped_quote_in_text(f2024):
    assert [v.text for v in f2024.vouchers if v.series == "B"] == ['Kontorsmaterial "special"']


def test_year_end_closing_voucher(f2024):
    closing = [v for v in f2024.vouchers if v.series == "J"][0]
    assert {t.account for t in closing.transactions} == {"8999", "2099"}
    assert sum(f2024.amounts("results").values()) == 0
    assert sum(f2024.amounts("closing").values()) == 0


# -- import file (4I) ------------------------------------------------------------------------------


def test_import_file_without_series_and_years():
    parsed = load("kassa.si")
    assert [(v.series, v.number, v.reference) for v in parsed.vouchers] == [
        ("", "", ""), ("", "", "")]
    assert parsed.vouchers[0].text == "Porto"
    assert codes(parsed, sie.WARNING) == [("no_rar", None)]
    undeclared = [i for i in parsed.issues if i.code == "undeclared_accounts"][0]
    assert undeclared.params["accounts"] == ["1930", "2641", "6250"]


# -- errors with line numbers ----------------------------------------------------------------------


def test_broken_file_reports_lines():
    parsed = load("broken.se")
    assert codes(parsed, sie.ERROR) == [
        ("unclosed_quote", 10),
        ("trans_outside_ver", 12),
        ("bad_date", 13),
        ("unbalanced", 18),
        ("bad_amount", 25),
        ("voucher_bad_line", 23),
        ("unexpected_brace", 34),
        ("no_label", 35),
        ("unclosed_block", 36),
    ]
    assert codes(parsed, sie.WARNING) == [("rtrans_unmatched", 30), ("unknown_label", 11)]
    unbalanced = [i for i in parsed.issues if i.code == "unbalanced"][0]
    assert unbalanced.params["difference"] == Decimal("1.00")
    assert unbalanced.params["series"] == "A" and unbalanced.params["number"] == "2"
    assert "line 18" in str(unbalanced)


def test_strict_raises_first_error():
    with pytest.raises(sie.SieParseError) as exc:
        load("broken.se", strict=True)
    assert exc.value.line == 10


def test_unbalanced_voucher_is_kept_and_reported():
    parsed = load("unbalanced.se")
    assert [v.is_balanced for v in parsed.vouchers] == [True, False]
    assert codes(parsed, sie.ERROR) == [("unbalanced", 16)]


def test_unknown_records_and_fields_are_ignored():
    parsed = sie.parse(lines(*HEADER, '#KONTO 1930 "Bank" framtida fält', "#NYPOST 1",
                             "#NYPOST 2", '#VER A 1 20250101 "x"', "{", "#NYPOST 3",
                             "#TRANS 1930 {} 1", "#TRANS 2091 {} -1", "}"))  # fmt: skip
    assert parsed.accounts["1930"].name == "Bank"
    assert codes(parsed) == [("unknown_label", 9), ("undeclared_accounts", None)]
    assert [i.params["count"] for i in parsed.issues if i.code == "unknown_label"] == [3]
    assert len(parsed.vouchers[0].transactions) == 2


def test_tolerated_deviations():
    parsed = sie.parse(lines(*HEADER, '#VER A 1 20250101 "x"', "{", "#TRANS 1930 100,50",
                             "#TRANS 2091 {} -100,50", "}"))  # fmt: skip
    assert [t.amount for t in parsed.vouchers[0].transactions] == [Decimal("100.50"),
                                                                    Decimal("-100.50")]
    assert ("comma_decimal", 10) in codes(parsed, sie.WARNING)
    assert ("missing_objects", 10) in codes(parsed, sie.INFO)
    assert not parsed.errors


def test_flag_one_is_reported():
    parsed = sie.parse(lines("#FLAGGA 1", *HEADER[1:]))
    assert ("flag_imported", None) in codes(parsed, sie.WARNING)


def test_missing_ver_block():
    parsed = sie.parse(lines(*HEADER, '#VER A 1 20250101 "x"', '#VER A 2 20250101 "y"', "{",
                             "#TRANS 1930 {} 1", "#TRANS 2091 {} -1", "}"))  # fmt: skip
    assert codes(parsed, sie.ERROR) == [("missing_block", 8)]
    assert len(parsed.vouchers) == 2


def test_duplicate_balance_is_reported():
    parsed = sie.parse(lines(*HEADER, "#IB 0 1930 10", "#IB 0 1930 10"))
    assert ("duplicate", 9) in codes(parsed, sie.WARNING)
    assert parsed.amounts("opening") == {"1930": Decimal("20")}


@pytest.mark.parametrize(
    "record, code",
    [
        ("#IB 0 19A0 10.00", "bad_account"),
        ("#IB x 1930 10.00", "bad_year"),
        ("#PSALDO 0 202513 1930 {} 10.00", "bad_period"),
        ("#RAR -1 20241231 20240101", "bad_year_range"),
        ('#OBJEKT x "1" "Namn"', "bad_object"),
        ('#DIM A "Namn"', "bad_dimension"),
        ('#TRANS 1930 {1} 10', "trans_outside_ver"),
    ],
)
def test_bad_records(record, code):
    parsed = sie.parse(lines(*HEADER, record))
    assert (code, 8) in codes(parsed, sie.ERROR)


def test_bad_object_list():
    parsed = sie.parse(lines(*HEADER, '#VER A 1 20250101 "x"', "{", '#TRANS 1930 {1} 1',
                             "#TRANS 2091 {} -1", "}"))  # fmt: skip
    assert ("bad_objects", 10) in codes(parsed, sie.ERROR)


# -- #KSUMMA ---------------------------------------------------------------------------------------


def _spec_crc32(data):
    """The table algorithm printed in the SIE specification (section 10, code example)."""
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
        table.append(crc)
    crc = 0xFFFFFFFF
    for byte in data:
        crc = ((crc >> 8) & 0x00FFFFFF) ^ table[(crc ^ byte) & 0xFF]
    return crc ^ 0xFFFFFFFF


def test_crc_is_the_specification_algorithm():
    data = "#KONTO1915Kassa \"special\"åäö".encode("cp437")
    assert _spec_crc32(data) == zlib.crc32(data)


def test_checksum_excludes_spaces_quotes_and_braces():
    # SIE 4B 10.15: only the label and the field contents count; an escaped quote counts as a quote
    assert sie.checksum(['#KONTO 1915 "Kassa \\"special\\""']) == zlib.crc32(
        b'#KONTO1915Kassa "special"')
    assert sie.checksum(["#TRANS 7010 {1 \"456\"} 13200.00", "{", "}"]) == zlib.crc32(
        b"#TRANS70101456" + b"13200.00")


def _with_checksum():
    parsed = load("fortnox_2025.se")
    return sie.write(parsed, checksum_records=True)


def test_checksum_ok():
    data = _with_checksum()
    text = data.decode("cp437")
    assert text.startswith("#FLAGGA 0\r\n#KSUMMA\r\n")
    assert text.splitlines()[-1].startswith("#KSUMMA ")
    parsed = sie.parse(data)
    assert parsed.checksum_declared
    assert parsed.checksum_ok is True
    assert not parsed.issues


def test_checksum_mismatch_is_a_warning():
    data = _with_checksum().replace(b"#TRANS 6570 {} 95.00", b"#TRANS 6570 {} 96.00")
    data = data.replace(b"#TRANS 1930 {} -95.00", b"#TRANS 1930 {} -96.00")
    parsed = sie.parse(data)
    assert parsed.checksum_ok is False
    assert codes(parsed) == [("checksum_mismatch", None)]
    assert parsed.issues[0].severity == sie.WARNING


def test_checksum_whitespace_does_not_matter():
    data = _with_checksum().replace(b"#TRANS 6570 {} 95.00", b"#TRANS\t6570  { }   95.00")
    assert sie.parse(data).checksum_ok is True


def test_checksum_without_label_hash_is_accepted():
    lines = ['#KONTO 1930 "Bank"', "#IB 0 1930 10.00", "#IB 0 2091 -10.00"]
    bare = 0
    for line in [*HEADER[1:], *lines]:
        tokens = sie.tokenize(line)
        bare = zlib.crc32(sie._checksum_bytes([tokens[0][1:]] + tokens[1:]), bare)
    data = "\r\n".join(["#FLAGGA 0", "#KSUMMA", *HEADER[1:], *lines, f"#KSUMMA {bare}"])
    parsed = sie.parse(data.encode("cp437"))
    assert parsed.checksum_ok is True


def test_truncated_file():
    data = _with_checksum()
    cut = data[: data.rindex(b"#KSUMMA ")]
    parsed = sie.parse(cut)
    assert ("truncated", None) in codes(parsed, sie.ERROR)


def test_no_checksum():
    parsed = load("fortnox_2025.se")
    assert parsed.checksum_ok is None
    assert not parsed.checksum_declared


# -- writer ----------------------------------------------------------------------------------------


def test_round_trip(f2025):
    data = sie.write(f2025)
    again = sie.parse(data)
    assert not again.issues
    for attr in ("flag", "program", "program_version", "sie_type", "orgnr", "company_name",
                 "address", "fiscal_years", "chart_type", "currency", "accounts", "dimensions",
                 "objects", "opening", "closing", "results", "period_balances",
                 "period_budgets"):  # fmt: skip
        mine = getattr(again, attr)
        theirs = getattr(f2025, attr)
        if isinstance(mine, dict):
            assert {k: _strip_lines(v) for k, v in mine.items()} == {
                k: _strip_lines(v) for k, v in theirs.items()}, attr
        elif isinstance(mine, list):
            assert [_strip_lines(x) for x in mine] == [_strip_lines(x) for x in theirs], attr
        else:
            assert mine == theirs, attr
    assert [_voucher_key(v) for v in again.vouchers] == [_voucher_key(v) for v in f2025.vouchers]


def _strip_lines(obj):
    if hasattr(obj, "line"):
        obj = type(obj)(**{k: v for k, v in vars(obj).items() if k != "line"})
    return obj


def _voucher_key(v):
    return (v.series, v.number, v.date, v.text, v.regdate,
            tuple((t.account, t.objects, t.amount, t.text) for t in v.transactions))  # fmt: skip


def test_writer_format():
    parsed = sie.SieFile(
        program="Odoo", program_version="19.0", generated=date(2026, 1, 2), sie_type="4",
        orgnr="559000-0000", company_name='Bolaget "Citat" € AB',
        fiscal_years={0: sie.FiscalYear(0, date(2025, 1, 1), date(2025, 12, 31))},
        accounts={"1930": sie.Account("1930", "Bank\nkonto", "T")},
        vouchers=[sie.Voucher("", "", date(2025, 3, 1), "Porto", transactions=[
            sie.Trans("1930", (), Decimal("-0.5")), sie.Trans("6250", (("1", "A 1"),),
                                                               Decimal("0.50"), text="x")])],
    )  # fmt: skip
    data = sie.write(parsed)
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    text = data.decode("cp437")
    assert '#FNAMN "Bolaget \\"Citat\\" ? AB"' in text
    assert '#KONTO 1930 "Bank konto"' in text
    assert '#VER "" "" 20250301 "Porto"' in text
    assert "#TRANS 1930 {} -0.50\r\n" in text
    assert '#TRANS 6250 {1 "A 1"} 0.50 "" "x"' in text
    assert text.startswith("#FLAGGA 0\r\n#PROGRAM \"Odoo\" 19.0\r\n#FORMAT PC8\r\n#GEN 20260102\r\n")
    back = sie.parse(data)
    assert back.company_name == 'Bolaget "Citat" ? AB'
    assert back.vouchers[0].transactions[1].objects == (("1", "A 1"),)
    assert back.vouchers[0].transactions[1].text == "x"
    assert not back.errors


def test_quote():
    assert sie.quote('a"b') == '"a\\"b"'
    assert sie.quote("tab\there") == '"tab here"'
    assert sie.quote("slut\\") == '"slut/"'
    assert sie.quote(None) == '""'


def test_format_amount():
    assert sie.format_amount(Decimal("-0.001")) == "0.00"
    assert sie.format_amount(Decimal("1234.5")) == "1234.50"
    assert sie.format_amount(Decimal("-2.675")) == "-2.68"


# -- account classes -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "number, ktyp, expected",
    [
        ("1010", "", "asset_non_current"),
        ("1220", "", "asset_fixed"),
        ("1460", "", "asset_current"),
        ("1710", "", "asset_prepayments"),
        ("1940", "", "asset_cash"),
        ("2081", "", "equity"),
        ("2150", "", "liability_current"),
        ("2350", "", "liability_non_current"),
        ("2890", "", "liability_current"),
        ("3001", "", "income"),
        ("3990", "", "income_other"),
        ("4010", "", "expense_direct_cost"),
        ("5010", "", "expense"),
        ("7832", "", "expense_depreciation"),
        ("8310", "", "income_other"),
        ("8410", "", "expense"),
        ("8310", "K", "expense"),
        ("8999", "", "expense"),
        ("9100", "I", "income_other"),
    ],
)
def test_suggest_account_type(number, ktyp, expected):
    assert sie.suggest_account_type(number, ktyp) == expected


def test_account_class():
    assert [sie.account_class(a) for a in ("1930", "2099", "3001", "8999", "9100")] == [
        "balance", "balance", "result", "result", "internal"]
