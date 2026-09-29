"""SEB payment file in CSV: the internet bank's upload template for domestic payments.

Pure Python without Odoo imports, so it can be tested with pytest and reused by any caller (the
export batch in this module, an OCA payment-order glue module later).

What is documented and what is not
----------------------------------
SEB publishes no specification of its CSV payment upload. The only hard facts come from the
template the internet bank offers for download (a byte-identical copy is in
``tests/data/Domestic.csv``):

* UTF-8 without BOM, LF line endings with a final newline, comma separated, no quoting;
* a header row with 37 columns (``HEADER``, compared byte for byte by the tests);
* one example row: ``sverige inrikes`` in *Betaltyp*, the other 36 columns empty.

Everything else - how amounts, dates, account numbers and format codes are written - is inferred
from SEB's ISO 20022 guide (MIG pain.001.001.03) and collected in the block **VERIFY WITH A TEST
UPLOAD** below. When a test upload in the internet bank shows that SEB wants something else,
change the constant there; nothing else in the module hard-codes these choices.
"""

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import NamedTuple

# --- Documented: the template ----------------------------------------------------------------

HEADER = (
    "Betaltyp",
    "Från konto",
    "Till konto",
    "Till konto - format (IBAN/BBAN/BG/PG)",
    "Mottagarens namn",
    "Belopp",
    "Betaldatum",
    "OCR",
    "Fakturanummer",
    "RF",
    "Meddelande",
    "Egen anteckning",
    "Skattekod",
    "Fondrelaterat syfte - kod",
    "Fondrelaterat syfte - text",
    "Standard eller Express",
    "Land",
    "Adress",
    "Husnummer",
    "Stad",
    "Postnummer",
    "Syfte med betalning",
    "Avsändarens referens",
    "Mottagarens id-typ",
    "Mottagarens id-nummer",
    "Mottagarens ID nummer: Organisation eller Personlig",
    "Slutliga mottagarens namn",
    "Slutliga mottagarens id-typ",
    "Slutliga mottagarens id-nummer",
    "Slutliga mottagarens ID nummer: Organisation eller Personlig",
    "Avsändarens id-typ",
    "Avsändarens id",
    "Avsändarens ID nummer: Organisation eller Personlig",
    "Ursprungliga avsändarens namn",
    "Ursprungliga avsändarens id-typ",
    "Ursprungliga avsändarens id-nummer",
    "Ursprungliga avsändarens ID nummer: Organisation eller Personlig",
)

# The columns this writer fills; all others are written empty.
COL_PAYMENT_TYPE = "Betaltyp"
COL_FROM_ACCOUNT = "Från konto"
COL_TO_ACCOUNT = "Till konto"
COL_TO_ACCOUNT_FORMAT = "Till konto - format (IBAN/BBAN/BG/PG)"
COL_PAYEE_NAME = "Mottagarens namn"
COL_AMOUNT = "Belopp"
COL_PAYMENT_DATE = "Betaldatum"
COL_OCR = "OCR"
COL_INVOICE_NUMBER = "Fakturanummer"
COL_RF = "RF"
COL_MESSAGE = "Meddelande"
COL_OWN_NOTE = "Egen anteckning"
COL_PRIORITY = "Standard eller Express"
COL_SENDER_REFERENCE = "Avsändarens referens"

ENCODING = "utf-8"  # without BOM
LINE_END = "\n"
SEPARATOR = ","
QUOTE = '"'
PAYMENT_TYPE_DOMESTIC = "sverige inrikes"  # the template's example row

HEADER_LINE = SEPARATOR.join(HEADER)

# --- VERIFY WITH A TEST UPLOAD ---------------------------------------------------------------
#
# SEB does not document any of the values below for the CSV upload. They are the defaults
# inferred from the template and SEB's ISO 20022 guide (see docs in the README). Each one must be
# confirmed with a test upload in the internet bank (upload, read the preview or the error, do
# not sign). If SEB wants something else, change it here - and only here.

# "Belopp": decimal separator. VERIFY with a test upload. A point is the safe inference because a
# decimal comma would collide with the field separator; if SEB wants "1234,50", set "," AND add
# COL_AMOUNT to QUOTED_COLUMNS so the field is written as "1234,50" in double quotes.
DECIMAL_SEPARATOR = "."

# Columns written inside double quotes. VERIFY with a test upload. The template quotes nothing.
QUOTED_COLUMNS = frozenset()

# "Betaldatum": date format (strftime). VERIFY with a test upload.
DATE_FORMAT = "%Y-%m-%d"

# "Till konto - format (IBAN/BBAN/BG/PG)": code per Swedish account type (the keys are the
# account types of l10n_se_bank_account). VERIFY spelling and case with a test upload; the codes
# are taken from the column name.
ACCOUNT_FORMAT_CODES = {
    "bankgiro": "BG",
    "plusgiro": "PG",
    "bban": "BBAN",
    "iban": "IBAN",
}

# "Till konto" for Bankgiro/Plusgiro: digits only (False) or with the usual hyphen, 5551-2347 /
# 123456-6 (True). VERIFY with a test upload; SEB's ISO examples write Bankgiro without hyphen.
GIRO_WITH_HYPHEN = False

# "Till konto" for bank accounts (BBAN): "annex5" writes the account the way SEB's ISO 20022 guide
# prescribes (MIG Annex 5: Handelsbanken without clearing number, Swedbank 8-series with a 5-digit
# clearing number zero-padded to 15 digits, ...; see l10n_se_bank_account); "clearing_account"
# writes clearing number + account number as given, for every bank. VERIFY with a test upload
# to a Handelsbanken and a Swedbank 8-series account.
BBAN_FORM = "annex5"

# "Standard eller Express": the value for a normal Bankgirot payment. VERIFY spelling with a
# test upload (Express goes via RIX and only to bank accounts).
PRIORITY_STANDARD = "Standard"

# "Från konto": the payer's own SEB account as "bban" (clearing + account, 11 digits) or as
# "iban". VERIFY with a test upload.
FROM_ACCOUNT_FORM = "bban"

# Without a valid OCR/RF a bank account payment (INVOICE_NUMBER_ACCOUNT_TYPES) gets the supplier's
# invoice number in "Fakturanummer" ("invoice_number") or as free text in "Meddelande"
# ("message"); Bankgiro and Plusgiro always get a message. VERIFY with a test payment that the
# payee of a bank account payment sees "Fakturanummer" on its statement; if not, use "message".
REFERENCE_WITHOUT_OCR = "invoice_number"

# Maximum lengths. The ISO 20022 limits are documented in SEB's MIG; whether the CSV applies the
# same limits is not. VERIFY with a test upload.
PAYEE_NAME_MAX = 35  # ISO allows 70; 35 is conservative. Names are cut, never refused.
INVOICE_NUMBER_MAX = 35  # MIG: RfrdDocInf/Nb
MESSAGE_MAX = 100  # SEB's payment form, Bankgiro message (2026-09-29); MIG Ustrd allows 140
SENDER_REFERENCE_MAX = 35  # MIG: EndToEndId
OWN_NOTE_MAX = 35  # unknown; cut, never refused

# "Avsändarens referens" only for account payments. Verified with a test upload (2026-09-28): SEB refuses
# the file when it is filled for a Bankgiro/Plusgiro payment ("not needed for bg and pg payments").
SENDER_REFERENCE_ACCOUNT_TYPES = ("bban", "iban")

# "Fakturanummer" only for account payments. Verified with an upload (2026-09-29): SEB refuses the
# file when it is filled for a Bankgiro payment ("not needed for bg and pg payments"), as for
# "Avsändarens referens"; there the invoice number goes in "Meddelande", which SEB accepts for
# Bankgiro. That account payments accept "Fakturanummer" is not verified yet.
INVOICE_NUMBER_ACCOUNT_TYPES = ("bban", "iban")

# --- End of VERIFY block ---------------------------------------------------------------------

OCR_MAX = 25  # Bankgirot OCR: 2-25 digits (documented)

REFERENCE_OCR = "ocr"
REFERENCE_RF = "rf"
REFERENCE_INVOICE = "invoice"
REFERENCE_MESSAGE = "message"
REFERENCE_TYPES = (REFERENCE_OCR, REFERENCE_RF, REFERENCE_INVOICE, REFERENCE_MESSAGE)


class CsvFieldError(ValueError):
    """A value that cannot be written to the SEB CSV file (programming or data error)."""


class Payment(NamedTuple):
    """One row of the file. Account numbers are already in payment-file form (see
    l10n_se_bank_account's ``payment_account_number``); texts are cleaned by the writer.

    ``reference_type`` is one of REFERENCE_TYPES and ``reference`` its value: an OCR number goes
    to "OCR", an RF reference to "RF", an invoice number to "Fakturanummer" (account payments
    only), a text to "Meddelande". Exactly one reference per payment, so OCR and an invoice number
    are never both written.
    """

    from_account: str
    to_account: str
    account_type: str  # bankgiro, plusgiro, bban, iban
    payee_name: str
    amount: object  # Decimal, str, int or float; SEK
    payment_date: date
    reference_type: str
    reference: str
    own_note: str = ""
    sender_reference: str = ""


# --- Cleaning --------------------------------------------------------------------------------

_SEPARATORS_RE = re.compile(r'[,;"]')
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")  # C0, C1, line and paragraph separators


# A spreadsheet opening the file would read a field starting with one of these as a formula.
_FORMULA_START = "=+-@"


def _finish(text, max_length):
    """Collapse whitespace, drop a leading formula character, cut at ``max_length``."""
    text = " ".join(text.split()).lstrip(_FORMULA_START + " ")
    if max_length:
        text = text[:max_length].rstrip()
    return text


def clean_text(value, max_length=None):
    """Free text for the file: commas, semicolons, quotes, line breaks and other control
    characters become spaces, runs of whitespace collapse, a leading ``=``, ``+``, ``-`` or ``@``
    is dropped, and the text is cut at ``max_length``. Letters such as å, ä, ö are kept."""
    return _finish(_CONTROL_RE.sub(" ", _SEPARATORS_RE.sub(" ", str(value or ""))), max_length)


def clean_name(value, max_length=PAYEE_NAME_MAX):
    """A name for the file: commas, semicolons and quotes are removed (``Bolag, AB`` ->
    ``Bolag AB``), line breaks become spaces, whitespace collapses, a leading formula character
    is dropped, cut at ``max_length``."""
    return _finish(_CONTROL_RE.sub(" ", _SEPARATORS_RE.sub("", str(value or ""))), max_length)


# --- Field formats ---------------------------------------------------------------------------


def to_decimal(amount):
    """The amount as a Decimal rounded to öre (half up). Floats go through their shortest repr."""
    try:
        value = Decimal(str(amount)) if not isinstance(amount, Decimal) else amount
    except InvalidOperation:
        raise CsvFieldError(f"Invalid amount: {amount!r}") from None
    if not value.is_finite():
        raise CsvFieldError(f"Invalid amount: {amount!r}")
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_amount(amount):
    """``1234.5`` -> ``1234.50``: two decimals, no thousands separator, DECIMAL_SEPARATOR.
    Amounts below 0.01 are refused (giro payments must be positive)."""
    value = to_decimal(amount)
    if value < Decimal("0.01"):
        raise CsvFieldError(f"The amount must be at least 0.01, got {value}")
    return f"{value:.2f}".replace(".", DECIMAL_SEPARATOR)


def format_date(value):
    if not isinstance(value, date):
        raise CsvFieldError(f"The payment date must be a date, got {value!r}")
    return value.strftime(DATE_FORMAT)


def _giro_with_hyphen(digits, account_type):
    if account_type == "bankgiro":
        return f"{digits[:-4]}-{digits[-4:]}"
    return f"{digits[:-1]}-{digits[-1]}"


def format_to_account(number, account_type):
    """"Till konto" per account type; checks the form (digits, length), not the check digits,
    which the caller has verified (l10n_se_bank_account)."""
    number = (number or "").strip()
    if account_type in ("bankgiro", "plusgiro"):
        low = 7 if account_type == "bankgiro" else 2
        if not (number.isascii() and number.isdigit() and low <= len(number) <= 8):
            raise CsvFieldError(f"{account_type} number must be {low}-8 digits, got {number!r}")
        return _giro_with_hyphen(number, account_type) if GIRO_WITH_HYPHEN else number
    if account_type == "bban":
        if not (number.isascii() and number.isdigit() and 6 <= len(number) <= 15):
            raise CsvFieldError(f"A bank account (BBAN) must be 6-15 digits, got {number!r}")
        return number
    if account_type == "iban":
        if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", number):
            raise CsvFieldError(f"Invalid IBAN form: {number!r}")
        return number
    raise CsvFieldError(f"Unknown account type {account_type!r}")


def format_from_account(number):
    number = (number or "").strip()
    if not re.fullmatch(r"[A-Z0-9]{6,34}", number):
        raise CsvFieldError(f"Invalid from account: {number!r}")
    return number


def _reference_columns(payment):
    """The reference column and value for a payment; exactly one reference."""
    kind, raw = payment.reference_type, payment.reference
    if kind == REFERENCE_OCR:
        ref = re.sub(r"\s", "", raw or "")
        if payment.account_type not in ("bankgiro", "plusgiro"):
            raise CsvFieldError("OCR is only used for Bankgiro and Plusgiro payments")
        if not (ref.isascii() and ref.isdigit() and 2 <= len(ref) <= OCR_MAX):
            raise CsvFieldError(f"An OCR reference is 2-{OCR_MAX} digits, got {raw!r}")
        return COL_OCR, ref
    if kind == REFERENCE_RF:
        ref = re.sub(r"\s", "", raw or "").upper()
        if not re.fullmatch(r"RF[0-9]{2}[A-Z0-9]{1,21}", ref):
            raise CsvFieldError(f"Invalid RF reference form: {raw!r}")
        return COL_RF, ref
    if kind == REFERENCE_INVOICE:
        if payment.account_type not in INVOICE_NUMBER_ACCOUNT_TYPES:
            raise CsvFieldError(
                "SEB does not accept 'Fakturanummer' for Bankgiro and Plusgiro payments; send the "
                "invoice number as a message"
            )
        ref = clean_text(raw)
        if not ref or len(ref) > INVOICE_NUMBER_MAX:
            raise CsvFieldError(f"An invoice number is 1-{INVOICE_NUMBER_MAX} characters, got {raw!r}")
        return COL_INVOICE_NUMBER, ref
    if kind == REFERENCE_MESSAGE:
        ref = clean_text(raw)
        if not ref or len(ref) > MESSAGE_MAX:
            raise CsvFieldError(f"A message is 1-{MESSAGE_MAX} characters, got {raw!r}")
        return COL_MESSAGE, ref
    raise CsvFieldError(f"Unknown reference type {kind!r}")


def payment_values(payment):
    """The filled columns of a payment row as a dict {column name: value}."""
    if payment.account_type not in ACCOUNT_FORMAT_CODES:
        raise CsvFieldError(f"Unknown account type {payment.account_type!r}")
    name = clean_name(payment.payee_name)
    if not name:
        raise CsvFieldError("The payee name is empty")
    sender_reference = clean_text(payment.sender_reference)
    if len(sender_reference) > SENDER_REFERENCE_MAX:
        raise CsvFieldError(
            f"The sender's reference has at most {SENDER_REFERENCE_MAX} characters: {sender_reference!r}"
        )
    ref_column, ref_value = _reference_columns(payment)
    return {
        COL_PAYMENT_TYPE: PAYMENT_TYPE_DOMESTIC,
        COL_FROM_ACCOUNT: format_from_account(payment.from_account),
        COL_TO_ACCOUNT: format_to_account(payment.to_account, payment.account_type),
        COL_TO_ACCOUNT_FORMAT: ACCOUNT_FORMAT_CODES[payment.account_type],
        COL_PAYEE_NAME: name,
        COL_AMOUNT: format_amount(payment.amount),
        COL_PAYMENT_DATE: format_date(payment.payment_date),
        ref_column: ref_value,
        COL_OWN_NOTE: clean_text(payment.own_note, OWN_NOTE_MAX),
        COL_PRIORITY: PRIORITY_STANDARD,
        COL_SENDER_REFERENCE: sender_reference if payment.account_type in SENDER_REFERENCE_ACCOUNT_TYPES else "",
    }


def payment_row(payment):
    """The 37 fields of a payment, in HEADER order."""
    values = payment_values(payment)
    return [values.get(column, "") for column in HEADER]


# --- File ------------------------------------------------------------------------------------


def render_line(fields):
    """One CSV line (without line end) from exactly 37 field values.

    No field may contain a line break or a quote; an unquoted field may not contain the separator
    either (the template quotes nothing). Columns in QUOTED_COLUMNS are written in double quotes.
    """
    if len(fields) != len(HEADER):
        raise CsvFieldError(f"A row has {len(HEADER)} fields, got {len(fields)}")
    out = []
    for column, value in zip(HEADER, fields, strict=True):
        value = "" if value is None else str(value)
        if QUOTE in value or "\n" in value or "\r" in value:
            raise CsvFieldError(f"{column}: quotes and line breaks are not allowed: {value!r}")
        if column in QUOTED_COLUMNS:
            out.append(f"{QUOTE}{value}{QUOTE}")
        elif SEPARATOR in value:
            raise CsvFieldError(f"{column}: the separator {SEPARATOR!r} is not allowed: {value!r}")
        else:
            out.append(value)
    return SEPARATOR.join(out)


def render_file(rows):
    """The file as bytes: HEADER, then one line per row of 37 values; UTF-8 without BOM, LF,
    final newline."""
    lines = [HEADER_LINE] + [render_line(row) for row in rows]
    return (LINE_END.join(lines) + LINE_END).encode(ENCODING)


def build_file(payments):
    """The SEB CSV file for a list of Payment; raises CsvFieldError on a value it cannot write."""
    payments = list(payments)
    if not payments:
        raise CsvFieldError("A payment file needs at least one payment")
    return render_file(payment_row(payment) for payment in payments)
