"""SIE 4 files: reader and writer.

Pure Python without Odoo imports, so it can be tested with pytest and reused by any caller.
Follows *SIE filformat* version 4B/4C from SIE-Gruppen (4C, 2025, changes no record format):

* one record per line, a label starting with ``#`` and fields separated by spaces or tabs;
* a field may be enclosed in double quotes (needed when it contains a space); a quote inside a
  field is written ``\\"``; an empty field before a later field is written ``""``;
* object lists ``{dimension "object" dimension "object"}`` (pairs, possibly empty ``{}``);
* the transactions of a ``#VER`` are sub-records between a ``{`` line and a ``}`` line;
* amounts with a dot and at most two decimals, credit negative; dates ``YYYYMMDD``;
* the character set is IBM PC 8-bit, code page 437 (``#FORMAT PC8``);
* readers ignore unknown labels and unknown trailing fields.

``#RTRANS`` (a line added to a voucher after it was registered) is always followed by an identical
``#TRANS``, and ``#BTRANS`` is a removed line. Only ``#TRANS`` lines are booked: they are the
voucher as it stands, and counting an ``#RTRANS`` as well would book the added line twice. The
other two are kept on the voucher for the audit trail.

``#KSUMMA``: CRC-32 (polynomial EDB88320H, pre-set FFFFFFFFH, inverted at the end; the same as
``zlib.crc32``) over the labels and field contents of every record after the opening ``#KSUMMA``
up to the closing one, without the spaces between fields, the quotes around fields and the
braces, on the code page 437 values of the characters.

Real files rarely follow the character set rule: cloud programs write UTF-8 and still declare
``#FORMAT PC8``. :func:`decode` detects the encoding from the bytes (see there).

:func:`parse` never raises for content errors: every problem becomes an :class:`Issue` with the
line number, so a caller can show all of them at once. ``strict=True`` raises
:class:`SieParseError` on the first error instead.
"""

from __future__ import annotations

import re
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ERROR = "error"
WARNING = "warning"
INFO = "info"

ZERO = Decimal(0)
CENT = Decimal("0.01")

#: Dimensions the SIE standard reserves; they need not be declared with #DIM.
RESERVED_DIMENSIONS = {
    "1": "Cost centre",
    "2": "Cost unit",
    "6": "Project",
    "7": "Employee",
    "8": "Customer",
    "9": "Supplier",
    "10": "Invoice",
}

KNOWN_LABELS = {
    "#FLAGGA", "#PROGRAM", "#FORMAT", "#GEN", "#SIETYP", "#PROSA", "#FTYP", "#FNR", "#ORGNR",
    "#BKOD", "#ADRESS", "#FNAMN", "#RAR", "#TAXAR", "#OMFATTN", "#KPTYP", "#VALUTA", "#KONTO",
    "#KTYP", "#ENHET", "#SRU", "#DIM", "#UNDERDIM", "#OBJEKT", "#IB", "#UB", "#OIB", "#OUB",
    "#RES", "#PSALDO", "#PBUDGET", "#VER", "#TRANS", "#RTRANS", "#BTRANS", "#KSUMMA",
}


class SieParseError(ValueError):
    """A record that cannot be read. ``line`` is the 1-based line number in the file."""

    def __init__(self, message, line=None, code="syntax", params=None):
        super().__init__(message if line is None else f"line {line}: {message}")
        self.message = message
        self.line = line
        self.code = code
        self.params = params or {}


@dataclass
class Issue:
    """A problem found while reading. ``code`` and ``params`` let a caller word it itself (e.g.
    translated); ``message`` is the English text."""

    severity: str
    code: str
    message: str
    line: int | None = None
    params: dict = field(default_factory=dict)

    def __str__(self):
        where = f"line {self.line}: " if self.line else ""
        return f"{self.severity}: {where}{self.message}"


class ObjectList(tuple):
    """The raw strings between ``{`` and ``}`` of a record (dimension, object, dimension, ...)."""


@dataclass
class Trans:
    """A #TRANS, #RTRANS or #BTRANS line."""

    account: str
    objects: tuple = ()  # ((dimension, object), ...)
    amount: Decimal = ZERO
    date: date | None = None
    text: str = ""
    quantity: Decimal | None = None
    sign: str = ""
    line: int | None = None


@dataclass
class Voucher:
    series: str
    number: str
    date: date
    text: str = ""
    regdate: date | None = None
    sign: str = ""
    #: The booked lines: only #TRANS.
    transactions: list = field(default_factory=list)
    #: #RTRANS: lines added after registration (each one is also among the #TRANS lines).
    added: list = field(default_factory=list)
    #: #BTRANS: lines removed after registration (not booked).
    removed: list = field(default_factory=list)
    line: int | None = None

    @property
    def balance(self):
        return sum((t.amount for t in self.transactions), ZERO)

    @property
    def is_balanced(self):
        return self.balance == 0

    @property
    def reference(self):
        """Series and number as people write them, e.g. ``A12``."""
        return f"{self.series}{self.number}"


@dataclass
class Account:
    number: str
    name: str = ""
    type: str = ""  # #KTYP: T (asset), S (liability/equity), K (cost), I (income)
    unit: str = ""
    sru: list = field(default_factory=list)
    line: int | None = None


@dataclass
class Dimension:
    number: str
    name: str = ""
    parent: str | None = None  # #UNDERDIM: the superior dimension
    line: int | None = None


@dataclass
class SieObject:
    dimension: str
    code: str
    name: str = ""
    line: int | None = None


@dataclass
class FiscalYear:
    index: int  # 0 = the file's current year, -1 the one before, ...
    start: date
    end: date
    line: int | None = None

    @property
    def label(self):
        """``2025`` for a calendar year, ``2024/25`` for a broken financial year."""
        if self.start.year == self.end.year:
            return str(self.start.year)
        return f"{self.start.year}/{self.end.year % 100:02d}"

    def __contains__(self, day):
        return self.start <= day <= self.end


@dataclass
class Balance:
    """#IB, #UB, #RES, #OIB or #OUB."""

    year: int
    account: str
    amount: Decimal
    quantity: Decimal | None = None
    objects: tuple = ()
    line: int | None = None


@dataclass
class PeriodBalance:
    """#PSALDO or #PBUDGET: the change on the account in one month (not cumulative)."""

    year: int
    period: str  # YYYYMM
    account: str
    amount: Decimal
    quantity: Decimal | None = None
    objects: tuple = ()
    line: int | None = None


@dataclass
class SieFile:
    encoding: str = "cp437"
    flag: str | None = None
    program: str = ""
    program_version: str = ""
    format: str = ""
    generated: date | None = None
    generated_by: str = ""
    sie_type: str = ""
    prosa: str = ""
    company_type: str = ""  # #FTYP
    company_id: str = ""  # #FNR
    orgnr: str = ""
    industry: str = ""  # #BKOD
    company_name: str = ""  # #FNAMN
    address: tuple = ()  # contact, street, postal address, phone
    fiscal_years: dict = field(default_factory=dict)  # index -> FiscalYear
    tax_year: str = ""  # #TAXAR
    balances_until: date | None = None  # #OMFATTN
    chart_type: str = ""  # #KPTYP
    currency: str = ""  # #VALUTA
    accounts: dict = field(default_factory=dict)  # number -> Account
    dimensions: dict = field(default_factory=dict)  # number -> Dimension
    objects: dict = field(default_factory=dict)  # (dimension, code) -> SieObject
    opening: list = field(default_factory=list)  # #IB
    closing: list = field(default_factory=list)  # #UB
    object_opening: list = field(default_factory=list)  # #OIB
    object_closing: list = field(default_factory=list)  # #OUB
    results: list = field(default_factory=list)  # #RES
    period_balances: list = field(default_factory=list)  # #PSALDO
    period_budgets: list = field(default_factory=list)  # #PBUDGET
    vouchers: list = field(default_factory=list)
    checksum_declared: bool = False
    checksum_value: int | None = None
    checksum_computed: int | None = None
    issues: list = field(default_factory=list)

    # -- convenience --------------------------------------------------------------------------

    @property
    def errors(self):
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self):
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def checksum_ok(self):
        """True/False when the file has a #KSUMMA value, None otherwise."""
        if self.checksum_value is None or self.checksum_computed is None:
            return None
        return self.checksum_value == self.checksum_computed

    def year(self, index=0):
        return self.fiscal_years.get(index)

    def year_of(self, day):
        """The #RAR year a date belongs to, or None."""
        for fy in self.fiscal_years.values():
            if day in fy:
                return fy
        return None

    def amounts(self, kind, year=0):
        """{account: amount} of #IB (``"opening"``), #UB (``"closing"``) or #RES (``"results"``)
        for one year index."""
        out = {}
        for b in getattr(self, kind):
            if b.year == year:
                out[b.account] = out.get(b.account, ZERO) + b.amount
        return out

    def vouchers_in(self, fy):
        return [v for v in self.vouchers if v.date and fy.start <= v.date <= fy.end]

    def series(self):
        return Counter(v.series for v in self.vouchers)

    def used_accounts(self):
        used = set()
        for v in self.vouchers:
            used.update(t.account for t in v.transactions)
        for kind in ("opening", "closing", "results"):
            used.update(b.account for b in getattr(self, kind))
        return used


# -- helpers --------------------------------------------------------------------------------------


def account_class(number):
    """``"balance"`` for BAS classes 1-2 (balance sheet), ``"result"`` for 3-8 (income
    statement), ``"internal"`` for class 9 and anything else."""
    first = number[:1]
    if first in ("1", "2"):
        return "balance"
    if first in ("3", "4", "5", "6", "7", "8"):
        return "result"
    return "internal"


def suggest_account_type(number, ktyp=""):
    """An Odoo ``account_type`` for a BAS account that does not exist yet, from the account class
    and #KTYP, following how Odoo's Swedish chart (l10n_se) classifies its accounts.

    The Odoo module first copies the type of existing accounts with the same prefix; this is the
    fallback when there are none.
    """
    ktyp = (ktyp or "").upper()
    head2 = number[:2]
    first = number[:1]
    if first == "1":
        if head2 in ("10", "13"):
            return "asset_non_current"
        if head2 in ("11", "12"):
            return "asset_fixed"
        if head2 == "17":
            return "asset_prepayments"
        if head2 == "19":
            return "asset_cash"
        return "asset_current"
    if first == "2":
        if head2 in ("20",) or number[:3] in ("210", "211", "212", "213", "214"):
            return "equity"
        if head2 in ("22", "23"):
            return "liability_non_current"
        return "liability_current"
    if first == "3":
        return "income_other" if head2 == "39" else "income"
    if first == "4":
        return "expense_direct_cost"
    if head2 == "78":
        return "expense_depreciation"
    if first in ("5", "6", "7"):
        return "expense"
    if first == "8":
        if ktyp == "I":
            return "income_other"
        if ktyp == "K":
            return "expense"
        return "income_other" if head2 in ("80", "81", "82", "83") else "expense"
    # class 9 and others: only #KTYP can tell
    return {
        "T": "asset_current",
        "S": "liability_current",
        "I": "income_other",
        "K": "expense",
    }.get(ktyp, "expense")


def format_amount(amount):
    """Two decimals, dot separator, minus sign in front, no plus sign."""
    value = Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)
    if value == 0:
        value = Decimal("0.00")
    return f"{value:.2f}"


def format_date(day):
    return day.strftime("%Y%m%d") if day else ""


# -- encoding ---------------------------------------------------------------------------------------

# Swedish letters in the two single-byte encodings (å ä ö Å Ä Ö é).
_CP437_LETTERS = set(b"\x86\x84\x94\x8f\x8e\x99\x82")
_LATIN1_LETTERS = set(b"\xe5\xe4\xf6\xc5\xc4\xd6\xe9")


def decode(data):
    """Decode a SIE file. Returns ``(text, encoding)`` where encoding is ``"utf-8-sig"`` (UTF-8 with
    BOM), ``"utf-8"``, ``"cp437"`` or ``"cp1252"``.

    1. A UTF-8 byte order mark: UTF-8.
    2. Valid UTF-8 with at least one multi-byte character: UTF-8. Code page 437 text with Swedish
       letters is never valid UTF-8 (0x84, 0x86, 0x94 are continuation bytes), so this cannot
       misread a correct file.
    3. More Latin-1 letters (0xE5 å, 0xE4 ä, 0xF6 ö ...) than code page 437 ones: Windows-1252,
       as some Windows programs write. In code page 437 those bytes are Greek letters and maths
       symbols that do not occur in Swedish bookkeeping.
    4. Otherwise code page 437, the standard (pure ASCII included).
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        if any(b > 0x7F for b in data):
            return text, "utf-8"
        return text, "cp437"
    high = Counter(b for b in data if b > 0x7F)
    cp437 = sum(n for b, n in high.items() if b in _CP437_LETTERS)
    latin1 = sum(n for b, n in high.items() if b in _LATIN1_LETTERS)
    if latin1 > cp437:
        return data.decode("cp1252", errors="replace"), "cp1252"
    return data.decode("cp437"), "cp437"


# -- tokenizer ---------------------------------------------------------------------------------------


def tokenize(text, line=None):
    """Split one record into its fields: a list of ``str`` and :class:`ObjectList`.

    Quoted fields lose their quotes and ``\\"`` becomes ``"``; a backslash before anything else
    is kept. Raises :class:`SieParseError` for an unterminated quote or object list.
    """
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t":
            i += 1
        elif c == '"':
            value, i = _read_quoted(text, i + 1, line)
            tokens.append(value)
        elif c == "{":
            items = []
            i += 1
            while True:
                while i < n and text[i] in " \t":
                    i += 1
                if i >= n:
                    raise SieParseError("object list without '}'", line, "unclosed_objects")
                c = text[i]
                if c == "}":
                    i += 1
                    break
                if c == "{":
                    raise SieParseError("'{' inside an object list", line, "nested_objects")
                if c == '"':
                    value, i = _read_quoted(text, i + 1, line)
                else:
                    j = i
                    while j < n and text[j] not in ' \t}"{':
                        j += 1
                    value, i = text[i:j], j
                items.append(value)
            tokens.append(ObjectList(items))
        else:
            j = i
            while j < n and text[j] not in " \t":
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def _read_quoted(text, i, line):
    out = []
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n and text[i + 1] == '"':
            out.append('"')
            i += 2
        elif c == '"':
            return "".join(out), i + 1
        else:
            out.append(c)
            i += 1
    raise SieParseError("text without a closing quote", line, "unclosed_quote")


def _checksum_bytes(tokens):
    parts = []
    for tok in tokens:
        if isinstance(tok, ObjectList):
            parts.extend(tok)
        else:
            parts.append(tok)
    return "".join(parts).encode("cp437", errors="replace")


def checksum(lines):
    """#KSUMMA value (unsigned 32 bits) of the records in ``lines`` (text lines, without the
    opening and closing #KSUMMA)."""
    crc = 0
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped in ("{", "}"):
            continue
        crc = zlib.crc32(_checksum_bytes(tokenize(stripped)), crc)
    return crc & 0xFFFFFFFF


# -- reader ------------------------------------------------------------------------------------------

# ASCII digits only: str.isdigit() accepts e.g. a superscript two (0xFD in code page 437), which
# int() then refuses, and \d accepts the digits of other scripts.
_DATE_RE = re.compile(r"^[0-9]{8}$")
_AMOUNT_RE = re.compile(r"^-?[0-9]+([.,][0-9]+)?$")
_ACCOUNT_RE = re.compile(r"^[0-9]+$")
_NUMBER_RE = re.compile(r"[0-9]+")

#: Longer lines are refused (a broken or hostile file should not exhaust memory).
MAX_LINE_LENGTH = 10000


def is_number(value):
    """True for a non-empty string of ASCII digits."""
    return bool(value) and _NUMBER_RE.fullmatch(value) is not None


class _Reader:
    def __init__(self, strict):
        self.strict = strict
        self.sie = SieFile()
        self.voucher = None
        self.voucher_has_block = False
        self.in_block = False
        self.last_trans_kind = None
        self.pending_rtrans = None
        self.voucher_failed = False
        self.unknown = {}
        self.comma_warned = False
        self.missing_objects_warned = False
        self.duplicates = defaultdict(list)

    # issues
    def issue(self, severity, code, message, line=None, **params):
        if severity == ERROR and self.strict:
            raise SieParseError(message, line, code, params)
        self.sie.issues.append(Issue(severity, code, message, line, params))

    # field conversion
    def text(self, fields, i):
        if i < len(fields) and not isinstance(fields[i], ObjectList):
            return fields[i]
        return ""

    def date(self, value, line, what, required=True):
        if not value:
            if required:
                self.issue(ERROR, "missing_date", f"{what}: date missing", line, what=what)
            return None
        if not _DATE_RE.match(value):
            self.issue(ERROR, "bad_date", f"{what}: '{value}' is not a date YYYYMMDD", line,
                       what=what, value=value)
            return None
        try:
            return date(int(value[:4]), int(value[4:6]), int(value[6:]))
        except ValueError:
            self.issue(ERROR, "bad_date", f"{what}: '{value}' is not a date YYYYMMDD", line,
                       what=what, value=value)
            return None

    def amount(self, value, line, what, required=True):
        if value == "" and not required:
            return None
        if not _AMOUNT_RE.match(value or ""):
            self.issue(ERROR, "bad_amount", f"{what}: '{value}' is not an amount", line,
                       what=what, value=value)
            return None
        if "," in value:
            if not self.comma_warned:
                self.issue(WARNING, "comma_decimal",
                           "amounts with a decimal comma instead of a dot (read anyway)", line)
                self.comma_warned = True
            value = value.replace(",", ".")
        try:
            return Decimal(value)
        except InvalidOperation:  # pragma: no cover - the regex already guards
            self.issue(ERROR, "bad_amount", f"{what}: '{value}' is not an amount", line,
                       what=what, value=value)
            return None

    def account(self, value, line, what):
        if not value or not _ACCOUNT_RE.match(value):
            self.issue(ERROR, "bad_account", f"{what}: '{value}' is not an account number", line,
                       what=what, value=value)
            return None
        return value

    def year_index(self, value, line, what):
        try:
            return int(value)
        except (TypeError, ValueError):
            self.issue(ERROR, "bad_year", f"{what}: '{value}' is not a year number (0, -1, ...)",
                       line, what=what, value=value)
            return None

    def objects(self, raw, line, what):
        if len(raw) % 2:
            self.issue(ERROR, "bad_objects",
                       f"{what}: the object list must hold dimension/object pairs", line,
                       what=what)
            return None
        pairs = tuple((raw[k].strip(), raw[k + 1]) for k in range(0, len(raw), 2))
        for dim, _obj in pairs:
            if not is_number(dim):
                self.issue(ERROR, "bad_objects",
                           f"{what}: '{dim}' is not a dimension number", line, what=what)
                return None
        return pairs

    def split_objects(self, fields, pos, line, what):
        """The object list at ``pos`` and the fields after it. Tolerates a missing list."""
        if pos < len(fields) and isinstance(fields[pos], ObjectList):
            return self.objects(fields[pos], line, what), fields[pos + 1:]
        if not self.missing_objects_warned:
            self.issue(INFO, "missing_objects",
                       f"{what} without an object list {{}} (read anyway)", line, what=what)
            self.missing_objects_warned = True
        return (), fields[pos:]

    # records
    def handle(self, label, fields, line):
        sie = self.sie
        t = lambda i: self.text(fields, i)  # noqa: E731
        if label not in KNOWN_LABELS:
            first = self.unknown.setdefault(label, [line, 0])
            first[1] += 1
            return
        if label in ("#TRANS", "#RTRANS", "#BTRANS"):
            return self.handle_trans(label, fields, line)
        if self.in_block:
            self.issue(ERROR, "unclosed_block",
                       f"#VER on line {self.voucher.line} has no closing '}}'", self.voucher.line,
                       ver_line=self.voucher.line)
            self.close_voucher()
        elif self.voucher is not None:
            self.issue(ERROR, "missing_block",
                       f"#VER on line {self.voucher.line} is not followed by a '{{' line",
                       self.voucher.line, ver_line=self.voucher.line)
            self.close_voucher()
        self.check_pending_rtrans(line)

        if label == "#FLAGGA":
            if sie.flag is not None or sie.program or sie.accounts:
                self.issue(INFO, "flag_not_first", "#FLAGGA is not the first record", line)
            sie.flag = t(0)
        elif label == "#PROGRAM":
            sie.program, sie.program_version = t(0), t(1)
        elif label == "#FORMAT":
            sie.format = t(0)
        elif label == "#GEN":
            sie.generated = self.date(t(0), line, "#GEN", required=False)
            sie.generated_by = t(1)
        elif label == "#SIETYP":
            sie.sie_type = t(0)
        elif label == "#PROSA":
            sie.prosa = t(0)
        elif label == "#FTYP":
            sie.company_type = t(0)
        elif label == "#FNR":
            sie.company_id = t(0)
        elif label == "#ORGNR":
            sie.orgnr = t(0)
        elif label == "#BKOD":
            sie.industry = t(0)
        elif label == "#ADRESS":
            sie.address = tuple(t(i) for i in range(4))
        elif label == "#FNAMN":
            sie.company_name = t(0)
        elif label == "#RAR":
            idx = self.year_index(t(0), line, "#RAR")
            start = self.date(t(1), line, "#RAR")
            end = self.date(t(2), line, "#RAR")
            if idx is not None and start and end:
                if end < start:
                    self.issue(ERROR, "bad_year_range", "#RAR: the year ends before it starts",
                               line)
                elif idx in sie.fiscal_years:
                    self.issue(WARNING, "duplicate", f"#RAR {idx} occurs twice; the last is used",
                               line, label="#RAR", key=str(idx))
                    sie.fiscal_years[idx] = FiscalYear(idx, start, end, line)
                else:
                    sie.fiscal_years[idx] = FiscalYear(idx, start, end, line)
        elif label == "#TAXAR":
            sie.tax_year = t(0)
        elif label == "#OMFATTN":
            sie.balances_until = self.date(t(0), line, "#OMFATTN", required=False)
        elif label == "#KPTYP":
            sie.chart_type = t(0)
        elif label == "#VALUTA":
            sie.currency = t(0).upper()
        elif label == "#KONTO":
            number = self.account(t(0), line, "#KONTO")
            if number:
                acc = sie.accounts.setdefault(number, Account(number, line=line))
                acc.name = t(1)
                acc.line = acc.line or line
        elif label in ("#KTYP", "#ENHET", "#SRU"):
            number = self.account(t(0), line, label)
            if number:
                acc = sie.accounts.setdefault(number, Account(number, line=line))
                if label == "#KTYP":
                    kind = t(1).upper()
                    if kind not in ("T", "S", "K", "I"):
                        self.issue(WARNING, "bad_account_type",
                                   f"#KTYP {number}: unknown account type '{t(1)}'", line,
                                   account=number, value=t(1))
                    else:
                        acc.type = kind
                elif label == "#ENHET":
                    acc.unit = t(1)
                elif t(1):
                    acc.sru.append(t(1))
        elif label in ("#DIM", "#UNDERDIM"):
            number = t(0)
            if not is_number(number):
                self.issue(ERROR, "bad_dimension", f"{label}: '{number}' is not a dimension number",
                           line, label=label, value=number)
            else:
                parent = t(2) if label == "#UNDERDIM" else None
                sie.dimensions[number] = Dimension(number, t(1), parent or None, line)
        elif label == "#OBJEKT":
            dim, code = t(0), t(1)
            if not is_number(dim) or code == "":
                self.issue(ERROR, "bad_object", "#OBJEKT needs a dimension number and an object",
                           line)
            else:
                sie.objects[(dim, code)] = SieObject(dim, code, t(2), line)
        elif label in ("#IB", "#UB", "#RES"):
            idx = self.year_index(t(0), line, label)
            number = self.account(t(1), line, label)
            amount = self.amount(t(2), line, label)
            if idx is not None and number and amount is not None:
                kind = {"#IB": "opening", "#UB": "closing", "#RES": "results"}[label]
                key = (kind, idx, number)
                self.duplicates[key].append(line)
                qty = self.amount(t(3), line, label, required=False)
                getattr(sie, kind).append(Balance(idx, number, amount, qty, (), line))
        elif label in ("#OIB", "#OUB"):
            idx = self.year_index(t(0), line, label)
            number = self.account(t(1), line, label)
            objects, rest = self.split_objects(fields, 2, line, label)
            amount = self.amount(self.text(rest, 0), line, label)
            if idx is not None and number and objects is not None and amount is not None:
                qty = self.amount(self.text(rest, 1), line, label, required=False)
                target = sie.object_opening if label == "#OIB" else sie.object_closing
                target.append(Balance(idx, number, amount, qty, objects, line))
        elif label in ("#PSALDO", "#PBUDGET"):
            idx = self.year_index(t(0), line, label)
            period = t(1)
            if not re.match(r"^[0-9]{6}$", period) or not 1 <= int(period[4:]) <= 12:
                self.issue(ERROR, "bad_period", f"{label}: '{period}' is not a period YYYYMM",
                           line, label=label, value=period)
                period = None
            number = self.account(t(2), line, label)
            objects, rest = self.split_objects(fields, 3, line, label)
            amount = self.amount(self.text(rest, 0), line, label)
            if None not in (idx, period, number, objects, amount):
                qty = self.amount(self.text(rest, 1), line, label, required=False)
                target = sie.period_balances if label == "#PSALDO" else sie.period_budgets
                target.append(PeriodBalance(idx, period, number, amount, qty, objects, line))
        elif label == "#VER":
            day = self.date(t(2), line, "#VER")
            self.voucher = Voucher(
                series=t(0),
                number=t(1),
                date=day,
                text=t(3),
                regdate=self.date(t(4), line, "#VER", required=False),
                sign=t(5),
                line=line,
            )
            self.voucher_has_block = False

    def handle_trans(self, label, fields, line):
        if not self.in_block:
            self.issue(ERROR, "trans_outside_ver", f"{label} outside a #VER block", line,
                       label=label)
            return
        number = self.account(self.text(fields, 0), line, label)
        objects, rest = self.split_objects(fields, 1, line, label)
        amount = self.amount(self.text(rest, 0), line, label)
        if number is None or objects is None or amount is None:
            self.voucher_failed = True
            self.check_pending_rtrans(line)
            return
        trans = Trans(
            account=number,
            objects=objects,
            amount=amount,
            date=self.date(self.text(rest, 1), line, label, required=False),
            text=self.text(rest, 2),
            quantity=self.amount(self.text(rest, 3), line, label, required=False),
            sign=self.text(rest, 4),
            line=line,
        )
        if label == "#TRANS":
            pending = self.pending_rtrans
            self.pending_rtrans = None
            if pending is not None and not _same_line(pending, trans):
                self.rtrans_unmatched(pending)
            self.voucher.transactions.append(trans)
        else:
            self.check_pending_rtrans(line)
            if label == "#RTRANS":
                self.voucher.added.append(trans)
                self.pending_rtrans = trans
            else:
                self.voucher.removed.append(trans)

    def rtrans_unmatched(self, rtrans):
        self.issue(WARNING, "rtrans_unmatched",
                   "#RTRANS is not followed by an identical #TRANS; only #TRANS lines are booked",
                   rtrans.line)

    def check_pending_rtrans(self, line):
        if self.pending_rtrans is not None:
            self.rtrans_unmatched(self.pending_rtrans)
            self.pending_rtrans = None

    def open_block(self, line):
        if self.voucher is None or self.voucher_has_block:
            self.issue(ERROR, "unexpected_brace", "'{' without a #VER before it", line)
            return
        self.in_block = True
        self.voucher_has_block = True
        self.voucher_failed = False

    def close_block(self, line):
        if not self.in_block:
            self.issue(ERROR, "unexpected_brace", "'}' without an open #VER block", line)
            return
        self.check_pending_rtrans(line)
        self.close_voucher()

    def close_voucher(self):
        v = self.voucher
        self.voucher = None
        self.in_block = False
        self.pending_rtrans = None
        if v is None:
            return
        if v.date is None:
            return  # already reported
        if self.voucher_failed:
            self.issue(ERROR, "voucher_bad_line",
                       f"voucher {v.reference} has a line that cannot be read", v.line,
                       series=v.series, number=v.number)
        elif not v.transactions:
            self.issue(WARNING, "empty_voucher", f"voucher {v.reference} has no #TRANS lines",
                       v.line, series=v.series, number=v.number)
        elif not v.is_balanced:
            self.issue(ERROR, "unbalanced",
                       f"voucher {v.reference} {format_date(v.date)} does not balance: the lines "
                       f"sum to {format_amount(v.balance)}", v.line,
                       series=v.series, number=v.number, date=v.date,
                       difference=v.balance)
        self.voucher_failed = False
        self.sie.vouchers.append(v)

    def finish(self, last_line):
        if self.in_block:
            self.issue(ERROR, "unclosed_block",
                       f"#VER on line {self.voucher.line} has no closing '}}'", self.voucher.line,
                       ver_line=self.voucher.line)
            self.check_pending_rtrans(last_line)
            self.close_voucher()
        elif self.voucher is not None:
            self.issue(ERROR, "missing_block",
                       f"#VER on line {self.voucher.line} is not followed by a '{{' line",
                       self.voucher.line, ver_line=self.voucher.line)
            self.close_voucher()
        for label, (line, count) in self.unknown.items():
            self.issue(WARNING, "unknown_label",
                       f"unknown record {label} ignored ({count} times, first on line {line})",
                       line, label=label, count=count)
        for (kind, idx, number), lines in self.duplicates.items():
            if len(lines) > 1:
                label = {"opening": "#IB", "closing": "#UB", "results": "#RES"}[kind]
                self.issue(WARNING, "duplicate",
                           f"{label} {idx} {number} occurs {len(lines)} times; they are added up",
                           lines[1], label=label, key=f"{idx} {number}")


def _same_line(a, b):
    return (a.account, a.objects, a.amount, a.date, a.text, a.quantity) == (
        b.account, b.objects, b.amount, b.date, b.text, b.quantity)


def parse(data, *, strict=False, max_line_length=MAX_LINE_LENGTH):
    """Read a SIE file (bytes). Returns a :class:`SieFile` with all problems in ``issues``."""
    text, encoding = decode(data)
    reader = _Reader(strict)
    sie = reader.sie
    sie.encoding = encoding
    lines = text.split("\n")
    checksum_open = False
    checksum_closed = False
    crc = 0
    crc_bare = 0  # the same without the '#' of the labels (see below)
    last = 0
    for no, raw in enumerate(lines, 1):
        stripped = raw.strip().lstrip("\ufeff")
        if not stripped:
            continue
        last = no
        if len(stripped) > max_line_length:
            reader.issue(ERROR, "line_too_long",
                         f"the line is longer than {max_line_length} characters", no,
                         limit=max_line_length)
            continue
        if stripped == "{":
            reader.open_block(no)
            continue
        if stripped == "}":
            reader.close_block(no)
            continue
        if not stripped.startswith("#"):
            reader.issue(ERROR, "no_label", "the line does not start with a #label", no)
            continue
        try:
            tokens = tokenize(stripped, no)
        except SieParseError as exc:
            reader.issue(ERROR, exc.code, exc.message, no)
            continue
        label = tokens[0].upper()
        fields = tokens[1:]
        if label == "#KSUMMA":
            value = reader.text(fields, 0)
            if not checksum_open and not value:
                checksum_open = True
                sie.checksum_declared = True
            elif value:
                checksum_closed = True
                try:
                    sie.checksum_value = int(value) & 0xFFFFFFFF
                except ValueError:
                    reader.issue(WARNING, "bad_checksum", f"#KSUMMA '{value}' is not a number", no)
                # The specification counts "the labels"; a writer that leaves out their '#'
                # is accepted too.
                sie.checksum_computed = crc & 0xFFFFFFFF
                if sie.checksum_value == crc_bare & 0xFFFFFFFF:
                    sie.checksum_computed = sie.checksum_value
                checksum_open = False
            continue
        if checksum_open:
            crc = zlib.crc32(_checksum_bytes(tokens), crc)
            crc_bare = zlib.crc32(_checksum_bytes([tokens[0][1:]] + tokens[1:]), crc_bare)
        reader.handle(label, fields, no)
    reader.finish(last)

    # -- file-level checks
    if sie.flag is None:
        reader.issue(WARNING, "no_flag", "#FLAGGA is missing")
    elif sie.flag == "1":
        reader.issue(WARNING, "flag_imported",
                     "#FLAGGA 1: the file is marked as already imported somewhere")
    if sie.checksum_declared and not checksum_closed:
        reader.issue(ERROR, "truncated",
                     "the file starts with #KSUMMA but the closing #KSUMMA is missing: the file "
                     "is probably cut off")
    if sie.checksum_ok is False:
        severity = WARNING if encoding == "cp437" else INFO
        reader.issue(severity, "checksum_mismatch",
                     f"#KSUMMA {sie.checksum_value} does not match the computed checksum "
                     f"{sie.checksum_computed}", None, encoding=encoding,
                     declared=sie.checksum_value, computed=sie.checksum_computed)
    if encoding != "cp437":
        reader.issue(INFO, "encoding",
                     f"read as {encoding} (declared #FORMAT {sie.format or '-'})",
                     None, encoding=encoding, declared=sie.format)
    elif sie.format and sie.format.upper() != "PC8":
        reader.issue(INFO, "encoding",
                     f"read as cp437 (declared #FORMAT {sie.format})", None,
                     encoding=encoding, declared=sie.format)
    if not sie.sie_type:
        reader.issue(INFO, "no_sie_type", "#SIETYP is missing: read as SIE type 1")
    if sie.vouchers and 0 not in sie.fiscal_years:
        reader.issue(WARNING, "no_rar", "the file has vouchers but no #RAR 0 (financial year)")
    elif sie.vouchers:
        outside = [v for v in sie.vouchers if v.date and not sie.year_of(v.date)]
        if outside:
            reader.issue(WARNING, "outside_years",
                         f"{len(outside)} vouchers are dated outside the financial years in the "
                         f"file (first: {outside[0].reference} on line {outside[0].line})",
                         outside[0].line, count=len(outside), first=outside[0].reference)
    undeclared = sorted(sie.used_accounts() - {a for a, acc in sie.accounts.items() if acc.name})
    if undeclared:
        reader.issue(INFO, "undeclared_accounts",
                     "accounts used without #KONTO: " + ", ".join(undeclared[:20])
                     + (" ..." if len(undeclared) > 20 else ""), None,
                     accounts=undeclared)
    return sie


# -- writer ------------------------------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def quote(value):
    """A text field: always in quotes, ``"`` escaped, control characters replaced by a space."""
    value = _CONTROL.sub(" ", str(value or ""))
    if value.endswith("\\"):
        value = value[:-1] + "/"
    return '"' + value.replace('"', '\\"') + '"'


def _plain(value):
    """A code field (series, number, account): unquoted unless it needs quotes."""
    value = _CONTROL.sub(" ", str(value if value is not None else ""))
    if value == "" or any(c in value for c in ' \t"{}'):
        return quote(value)
    return value


def _objects(pairs):
    return "{" + " ".join(f"{dim} {quote(obj)}" for dim, obj in pairs) + "}"


def _record(label, *fields):
    """A record line; trailing empty fields are left out, earlier empty ones written ``""``."""
    fields = list(fields)
    while fields and fields[-1] in ("", None):
        fields.pop()
    return " ".join([label] + ['""' if f in ("", None) else f for f in fields])


def write_lines(sie, *, checksum_records=False):
    """The text lines of a SIE file for ``sie`` (a :class:`SieFile`), in the order of the
    specification: flag, identification, chart of accounts, balances and vouchers."""
    lines = [_record("#FLAGGA", sie.flag or "0")]
    if checksum_records:
        lines.append("#KSUMMA")
    lines.append(_record("#PROGRAM", quote(sie.program), _plain(sie.program_version)))
    lines.append("#FORMAT PC8")
    lines.append(_record("#GEN", format_date(sie.generated or date.today()),
                         quote(sie.generated_by) if sie.generated_by else ""))
    lines.append(_record("#SIETYP", sie.sie_type or "4"))
    if sie.prosa:
        lines.append(_record("#PROSA", quote(sie.prosa)))
    if sie.company_type:
        lines.append(_record("#FTYP", _plain(sie.company_type)))
    if sie.company_id:
        lines.append(_record("#FNR", _plain(sie.company_id)))
    if sie.orgnr:
        lines.append(_record("#ORGNR", _plain(sie.orgnr)))
    lines.append(_record("#FNAMN", quote(sie.company_name)))
    if any(sie.address):
        lines.append(_record("#ADRESS", *[quote(a) for a in (list(sie.address) + [""] * 4)[:4]]))
    for idx in sorted(sie.fiscal_years, reverse=True):
        fy = sie.fiscal_years[idx]
        lines.append(_record("#RAR", str(idx), format_date(fy.start), format_date(fy.end)))
    if sie.tax_year:
        lines.append(_record("#TAXAR", _plain(sie.tax_year)))
    if sie.balances_until:
        lines.append(_record("#OMFATTN", format_date(sie.balances_until)))
    if sie.chart_type:
        lines.append(_record("#KPTYP", _plain(sie.chart_type)))
    if sie.currency:
        lines.append(_record("#VALUTA", _plain(sie.currency)))
    for number in sorted(sie.accounts, key=lambda n: (len(n), n)):
        acc = sie.accounts[number]
        lines.append(_record("#KONTO", number, quote(acc.name)))
        if acc.type:
            lines.append(_record("#KTYP", number, acc.type))
        if acc.unit:
            lines.append(_record("#ENHET", number, quote(acc.unit)))
        for code in acc.sru:
            lines.append(_record("#SRU", number, _plain(code)))
    for number in sorted(sie.dimensions, key=int):
        dim = sie.dimensions[number]
        if dim.parent:
            lines.append(_record("#UNDERDIM", number, quote(dim.name), dim.parent))
        else:
            lines.append(_record("#DIM", number, quote(dim.name)))
    for (dim, code) in sorted(sie.objects, key=lambda k: (int(k[0]), k[1])):
        obj = sie.objects[(dim, code)]
        lines.append(_record("#OBJEKT", dim, quote(code), quote(obj.name)))

    def qty(b):
        return format_amount(b.quantity) if b.quantity is not None else ""

    for label, items in (("#IB", sie.opening), ("#UB", sie.closing)):
        for b in items:
            lines.append(_record(label, str(b.year), b.account, format_amount(b.amount), qty(b)))
    for label, items in (("#OIB", sie.object_opening), ("#OUB", sie.object_closing)):
        for b in items:
            lines.append(_record(label, str(b.year), b.account, _objects(b.objects),
                                 format_amount(b.amount), qty(b)))
    for b in sie.results:
        lines.append(_record("#RES", str(b.year), b.account, format_amount(b.amount), qty(b)))
    for label, items in (("#PSALDO", sie.period_balances), ("#PBUDGET", sie.period_budgets)):
        for p in items:
            lines.append(_record(label, str(p.year), p.period, p.account, _objects(p.objects),
                                 format_amount(p.amount), qty(p)))
    for v in sie.vouchers:
        lines.append(_record("#VER", _plain(v.series), _plain(v.number), format_date(v.date),
                             quote(v.text) if v.text else "", format_date(v.regdate),
                             quote(v.sign) if v.sign else ""))
        lines.append("{")
        for t in v.transactions:
            lines.append(_record(
                "#TRANS", t.account, _objects(t.objects), format_amount(t.amount),
                format_date(t.date), quote(t.text) if t.text else "",
                format_amount(t.quantity) if t.quantity is not None else "",
                quote(t.sign) if t.sign else "",
            ))
        lines.append("}")
    if checksum_records:
        lines.append(f"#KSUMMA {checksum(lines[2:])}")
    return lines


def write(sie, *, checksum_records=False):
    """The file as bytes: code page 437 (characters it lacks become ``?``), CRLF line ends."""
    text = "".join(line + "\r\n" for line in write_lines(sie, checksum_records=checksum_records))
    return text.encode("cp437", errors="replace")
