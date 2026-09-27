"""pytest for lib/seb_csv.py, without Odoo.

Run from the repository root:
``python -m pytest -p no:cacheprovider --confcutdir=l10n_se_payment_file_seb_csv/tests/pytest
l10n_se_payment_file_seb_csv/tests/pytest``.
The library is loaded from its file so the Odoo package (and its ``odoo`` import) is never
imported.

All account numbers, giro numbers and references are invented; they only satisfy the check digits.
"""

import csv
import importlib.util
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

_MODULE = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("seb_csv", _MODULE / "lib" / "seb_csv.py")
seb_csv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seb_csv)

TEMPLATE = (_MODULE / "tests" / "data" / "Domestic.csv").read_bytes()

FROM = "52031234560"  # invented SEB account, clearing 5203


def _payment(**kw):
    values = {
        "from_account": FROM,
        "to_account": "5551239",
        "account_type": "bankgiro",
        "payee_name": "Leverantör AB",
        "amount": Decimal("1250.00"),
        "payment_date": date(2026, 10, 7),
        "reference_type": "ocr",
        "reference": "1234567897",
        "own_note": "BILL/2026/10/0001",
        "sender_reference": "SEB2026-0001-001",
    }
    values.update(kw)
    return seb_csv.Payment(**values)


def _rows(data):
    return list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))


def _row(payment):
    """The written row as {column: value}."""
    rows = _rows(seb_csv.build_file([payment]))
    return dict(zip(rows[0], rows[1], strict=True))


# --- Envelope: the template ------------------------------------------------------------------


def test_header_is_the_template_header_byte_for_byte():
    assert (seb_csv.HEADER_LINE + "\n").encode("utf-8") == TEMPLATE.split(b"\n", 1)[0] + b"\n"
    assert len(seb_csv.HEADER) == 37


def test_template_example_row_is_reproduced_exactly():
    row = [seb_csv.PAYMENT_TYPE_DOMESTIC] + [""] * 36
    assert seb_csv.render_file([row]) == TEMPLATE


def test_file_envelope():
    data = seb_csv.build_file([_payment(), _payment(amount="1.00", sender_reference="X-2")])
    assert not data.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in data
    assert data.endswith(b"\n") and not data.endswith(b"\n\n")
    assert data.count(b"\n") == 3
    assert b'"' not in data
    rows = _rows(data)
    assert all(len(row) == 37 for row in rows)
    assert rows[0] == list(seb_csv.HEADER)
    assert [row[0] for row in rows[1:]] == ["sverige inrikes", "sverige inrikes"]
    data.decode("utf-8")  # valid UTF-8


def test_empty_file_is_refused():
    with pytest.raises(seb_csv.CsvFieldError):
        seb_csv.build_file([])


def test_row_width_is_checked():
    with pytest.raises(seb_csv.CsvFieldError):
        seb_csv.render_line(["sverige inrikes"])


# --- Columns ---------------------------------------------------------------------------------


def test_bankgiro_with_ocr():
    row = _row(_payment())
    assert row["Betaltyp"] == "sverige inrikes"
    assert row["Från konto"] == FROM
    assert row["Till konto"] == "5551239"
    assert row["Till konto - format (IBAN/BBAN/BG/PG)"] == "BG"
    assert row["Mottagarens namn"] == "Leverantör AB"
    assert row["Belopp"] == "1250.00"
    assert row["Betaldatum"] == "2026-10-07"
    assert row["OCR"] == "1234567897"
    assert row["Fakturanummer"] == row["RF"] == row["Meddelande"] == ""
    assert row["Egen anteckning"] == "BILL/2026/10/0001"
    assert row["Standard eller Express"] == "Standard"
    assert row["Avsändarens referens"] == "SEB2026-0001-001"
    filled = {k for k, v in row.items() if v}
    assert filled == {
        "Betaltyp", "Från konto", "Till konto", "Till konto - format (IBAN/BBAN/BG/PG)",
        "Mottagarens namn", "Belopp", "Betaldatum", "OCR", "Egen anteckning",
        "Standard eller Express", "Avsändarens referens",
    }


@pytest.mark.parametrize(
    "account_type, number, code",
    [
        ("bankgiro", "55512347", "BG"),
        ("plusgiro", "1234566", "PG"),
        ("plusgiro", "18", "PG"),
        ("bban", "52031234560", "BBAN"),  # SEB: clearing + account
        ("bban", "123456789", "BBAN"),  # Handelsbanken: account only
        ("bban", "832790123456782", "BBAN"),  # Swedbank 8-series, 15 digits
        ("bban", "1234567897", "BBAN"),  # Danske Bank 918x
        ("iban", "SE7280000832791234567897", "IBAN"),
    ],
)
def test_every_account_type(account_type, number, code):
    ref_type = "ocr" if account_type in ("bankgiro", "plusgiro") else "invoice"
    row = _row(_payment(account_type=account_type, to_account=number, reference_type=ref_type,
                        reference="1234567897" if ref_type == "ocr" else "F-4711"))
    assert row["Till konto"] == number
    assert row["Till konto - format (IBAN/BBAN/BG/PG)"] == code


@pytest.mark.parametrize(
    "account_type, number",
    [
        ("bankgiro", "555-1239"),
        ("bankgiro", "555123"),
        ("bankgiro", "555123456"),
        ("plusgiro", "1"),
        ("bban", "5203-1234567"),
        ("bban", "12345"),
        ("bban", "1234567890123456"),
        ("iban", "se7280000832791234567897"),
        ("other", "55512347"),
    ],
)
def test_bad_account_forms_are_refused(account_type, number):
    with pytest.raises(seb_csv.CsvFieldError):
        seb_csv.build_file([_payment(account_type=account_type, to_account=number,
                                     reference_type="invoice", reference="F-1")])


def test_giro_with_hyphen_switch(monkeypatch):
    monkeypatch.setattr(seb_csv, "GIRO_WITH_HYPHEN", True)
    assert _row(_payment(to_account="55512347"))["Till konto"] == "5551-2347"
    assert _row(_payment(to_account="5551239"))["Till konto"] == "555-1239"
    assert _row(_payment(account_type="plusgiro", to_account="1234566"))["Till konto"] == "123456-6"


@pytest.mark.parametrize(
    "amount, text",
    [
        (1234.5, "1234.50"),
        (Decimal("0.01"), "0.01"),
        (1000000, "1000000.00"),
        ("99.999", "100.00"),
        (Decimal("0.005"), "0.01"),  # half up
        (0.1 + 0.2, "0.30"),
    ],
)
def test_amount_format(amount, text):
    assert seb_csv.format_amount(amount) == text


@pytest.mark.parametrize("amount", [0, "0.004", -5, "-0.01", "abc", float("nan")])
def test_amount_must_be_positive(amount):
    with pytest.raises(seb_csv.CsvFieldError):
        seb_csv.format_amount(amount)


def test_date_format():
    assert seb_csv.format_date(date(2026, 1, 2)) == "2026-01-02"
    with pytest.raises(seb_csv.CsvFieldError):
        seb_csv.format_date("2026-01-02")


def test_decimal_comma_needs_quoting(monkeypatch):
    monkeypatch.setattr(seb_csv, "DECIMAL_SEPARATOR", ",")
    with pytest.raises(seb_csv.CsvFieldError):
        seb_csv.build_file([_payment()])
    monkeypatch.setattr(seb_csv, "QUOTED_COLUMNS", frozenset({seb_csv.COL_AMOUNT}))
    data = seb_csv.build_file([_payment(amount="1.25")])
    assert b',"1,25",' in data
    rows = _rows(data)
    assert len(rows[1]) == 37 and rows[1][5] == "1,25"


def test_date_format_switch(monkeypatch):
    monkeypatch.setattr(seb_csv, "DATE_FORMAT", "%Y%m%d")
    assert _row(_payment())["Betaldatum"] == "20261007"


# --- References: exactly one -----------------------------------------------------------------


def test_invoice_number_without_ocr():
    row = _row(_payment(reference_type="invoice", reference="4711-A"))
    assert row["Fakturanummer"] == "4711-A"
    assert row["OCR"] == row["RF"] == row["Meddelande"] == ""


def test_message():
    row = _row(_payment(reference_type="message", reference="Faktura 4711, maj"))
    assert row["Meddelande"] == "Faktura 4711 maj"
    assert row["OCR"] == row["Fakturanummer"] == ""


def test_rf_reference():
    row = _row(_payment(account_type="bban", to_account="52031234560", reference_type="rf",
                        reference="rf18 5390 0754 7034"))
    assert row["RF"] == "RF18539007547034"
    assert row["OCR"] == row["Fakturanummer"] == row["Meddelande"] == ""


def test_ocr_only_for_giro():
    with pytest.raises(seb_csv.CsvFieldError):
        seb_csv.build_file([_payment(account_type="bban", to_account="52031234560")])


@pytest.mark.parametrize(
    "kind, value",
    [
        ("ocr", "1"),
        ("ocr", "1" * 26),
        ("ocr", "12 34A"),
        ("rf", "RF1"),
        ("invoice", ""),
        ("invoice", "X" * 36),
        ("message", " , "),
        ("message", "X" * 141),
        ("both", "1234567897"),
        (None, "x"),
    ],
)
def test_bad_references_are_refused(kind, value):
    with pytest.raises(seb_csv.CsvFieldError):
        seb_csv.build_file([_payment(reference_type=kind, reference=value)])


def test_ocr_whitespace_is_removed():
    assert _row(_payment(reference="12345 67897"))["OCR"] == "1234567897"


# --- Cleaning --------------------------------------------------------------------------------


def test_names_are_cleaned_and_cut():
    row = _row(_payment(payee_name='  Åkeri, "Östra"\nÄngen;\tAB  '))
    assert row["Mottagarens namn"] == "Åkeri Östra Ängen AB"
    long_name = "Bolaget med ett mycket långt namn i Sverige AB"
    assert _row(_payment(payee_name=long_name))["Mottagarens namn"] == long_name[:35].rstrip()


def test_message_texts_are_cleaned():
    assert seb_csv.clean_text('a,b;c"d\r\ne\tf g\x00h') == "a b c d e f g h"
    assert seb_csv.clean_text("x" * 200, 140) == "x" * 140
    row = _row(_payment(reference_type="message", reference='Order 1,\n"2"'))
    assert row["Meddelande"] == "Order 1 2"


def test_leading_formula_characters_are_dropped():
    # a spreadsheet opening the file must not see a formula
    assert seb_csv.clean_name('=HYPERLINK("x")') == "HYPERLINK(x)"
    assert seb_csv.clean_name("+46 Konsult AB") == "46 Konsult AB"
    assert seb_csv.clean_text(" -@=1+1") == "1+1"
    assert seb_csv.clean_text("Faktura 12-3") == "Faktura 12-3"
    row = _row(_payment(payee_name="@Bolaget AB", reference_type="message", reference="=cmd"))
    assert row["Mottagarens namn"] == "Bolaget AB"
    assert row["Meddelande"] == "cmd"


def test_empty_name_is_refused():
    with pytest.raises(seb_csv.CsvFieldError):
        seb_csv.build_file([_payment(payee_name=' ,;" ')])


def test_own_note_is_cleaned_and_cut():
    row = _row(_payment(own_note="BILL/2026/10/0001, " + "x" * 40))
    assert "," not in row["Egen anteckning"]
    assert len(row["Egen anteckning"]) <= seb_csv.OWN_NOTE_MAX


def test_sender_reference_limit():
    with pytest.raises(seb_csv.CsvFieldError):
        seb_csv.build_file([_payment(sender_reference="X" * 36)])


def test_separator_quote_newline_never_reach_the_file():
    nasty = 'a,b"c\nd\re;f'
    data = seb_csv.build_file(
        [_payment(payee_name=nasty, reference_type="message", reference=nasty, own_note=nasty,
                  sender_reference="R-1")]
    )
    rows = _rows(data)
    assert len(rows) == 2 and len(rows[1]) == 37
    assert b'"' not in data and b"\r" not in data


def test_render_line_refuses_forbidden_characters():
    base = ["sverige inrikes"] + [""] * 36
    for bad in ("a,b", 'a"b', "a\nb", "a\rb"):
        fields = list(base)
        fields[4] = bad
        with pytest.raises(seb_csv.CsvFieldError):
            seb_csv.render_line(fields)


def test_bad_from_account():
    with pytest.raises(seb_csv.CsvFieldError):
        seb_csv.build_file([_payment(from_account="5203-1234567")])
