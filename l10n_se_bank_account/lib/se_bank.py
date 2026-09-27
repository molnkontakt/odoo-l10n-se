"""Swedish bank account numbers, giro numbers and payment references.

Pure Python without Odoo imports, so it can be tested with pytest and reused by any payment-file
writer. The Odoo models only add thin wrappers (translated error messages, record fields).

Sources:

* Bankgiro, Plusgiro and OCR: modulus 10 (Luhn) check digit over the whole number; OCR is 2-25
  digits, optionally with a length digit (Bankgirot, "OCR-referenskontroll").
* RF creditor reference: ISO 11649 (``RF`` + 2 check digits + 1-21 alphanumerics, mod 97 = 1).
* Bank account numbers (BBAN): the bank's ISO 20022 implementation guide, SEB MIG pain.001.001.03,
  Annex 5 "How to enter Swedish bank account numbers" (how the number is written), and Bankgirot's
  "Bankernas kontonummer" of 2024-02-22 (clearing number ranges, account lengths and check digits:
  type 1 comment 1/2 and type 2 comment 1/2/3).
"""

import re
from datetime import date, timedelta
from typing import NamedTuple

ACCOUNT_TYPES = ("bankgiro", "plusgiro", "bban", "iban", "other")

# Bank code in a Swedish IBAN (positions 5-7) -> bank, as named in CLEARING_RANGES
SE_IBAN_BANK_NAME = {
    "120": "Danske Bank",
    "300": "Nordea",
    "500": "SEB",
    "600": "Handelsbanken",
    "800": "Swedbank",
    "902": "Länsförsäkringar Bank",
    "915": "Skandiabanken",
    "927": "ICA Banken",
    "957": "Sparbanken Syd",
}

# Bank code in a Swedish IBAN -> BIC. Only used when the bank record has no BIC.
SE_IBAN_BANK_BIC = {
    "120": "DABASESX",  # Danske Bank
    "300": "NDEASESS",  # Nordea
    "500": "ESSESESS",  # SEB
    "600": "HANDSESS",  # Handelsbanken
    "800": "SWEDSESS",  # Swedbank / savings banks
    "902": "ELLFSESS",  # Länsförsäkringar Bank
    "915": "SKIASESS",  # Skandiabanken
    "927": "IBCASES1",  # ICA Banken
    "957": "SPSDSE23",  # Sparbanken Syd
}

# --- Clearing numbers ------------------------------------------------------------------------

# How the account is written as BBAN (MIG Annex 5)
RULE_CLEARING_ACCOUNT = "clearing_account"  # clearing (4) + account (7) = 11 digits; the default
RULE_ACCOUNT_ONLY = "account_only"  # account number only, without the clearing number
RULE_SWEDBANK_8 = "swedbank_8"  # 5-digit clearing + account, zero-padded to 15 digits in total
RULE_CLEARING_ACCOUNT_10 = "clearing_account_10"  # clearing (4) + account (10) = 14 digits
RULE_PLUSGIROT = "plusgirot"  # clearing (4) + PlusGiro account number as given


# Check digit of the account number ("Bankernas kontonummer": account type and comment)
CHECK_TYPE1_COMMENT1 = "type1_comment1"  # mod 11 over clearing digits 2-4 + the 7-digit account
CHECK_TYPE1_COMMENT2 = "type1_comment2"  # mod 11 over the 4-digit clearing + the 7-digit account
CHECK_TYPE2_COMMENT1 = "type2_comment1"  # mod 10 over the 10-digit account
CHECK_TYPE2_COMMENT2 = "type2_comment2"  # mod 11 over the 9-digit account (Handelsbanken)
CHECK_TYPE2_COMMENT3 = "type2_comment3"  # mod 10 over the account "as a rule": known exceptions
CHECKS = (
    CHECK_TYPE1_COMMENT1,
    CHECK_TYPE1_COMMENT2,
    CHECK_TYPE2_COMMENT1,
    CHECK_TYPE2_COMMENT2,
    CHECK_TYPE2_COMMENT3,
)

# Outcome of the check digit test of a bank account (Bban.check_digit)
CHECK_DIGIT_OK = "ok"
CHECK_DIGIT_FAILED = "failed"  # only for CHECK_TYPE2_COMMENT3; any other failure is an error
CHECK_DIGIT_UNKNOWN = "unknown"  # unlisted clearing number: nothing to check against


class ClearingRange(NamedTuple):
    first: int
    last: int
    bank: str
    rule: str
    account_length: int  # digits after the clearing number (maximum for Swedbank / PlusGirot)
    check: str  # one of the CHECK_* constants


# Clearing number ranges ("Bankernas kontonummer", 2024-02-22). A clearing number that is not
# listed is written with the default rule (clearing + 7-digit account), which is what the MIG
# prescribes for "all other banks"; its check digit cannot be tested.
CLEARING_RANGES = (
    ClearingRange(1100, 1199, "Nordea", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(1200, 1399, "Danske Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(1400, 2099, "Nordea", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(2300, 2399, "Ålandsbanken", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(2400, 2499, "Danske Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(3000, 3299, "Nordea", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    # Nordea personal accounts: the account number is the holder's 10-digit personal id number
    ClearingRange(3300, 3300, "Nordea", RULE_ACCOUNT_ONLY, 10, CHECK_TYPE2_COMMENT1),
    ClearingRange(3301, 3399, "Nordea", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(3400, 3409, "Länsförsäkringar Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(3410, 3781, "Nordea", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(3782, 3782, "Nordea", RULE_ACCOUNT_ONLY, 10, CHECK_TYPE2_COMMENT1),
    ClearingRange(3783, 3999, "Nordea", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(4000, 4999, "Nordea", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(5000, 5999, "SEB", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(6000, 6999, "Handelsbanken", RULE_ACCOUNT_ONLY, 9, CHECK_TYPE2_COMMENT2),
    ClearingRange(7000, 7999, "Swedbank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(8000, 8999, "Swedbank", RULE_SWEDBANK_8, 10, CHECK_TYPE2_COMMENT3),
    ClearingRange(9020, 9029, "Länsförsäkringar Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9040, 9049, "Citibank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9060, 9069, "Länsförsäkringar Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(9070, 9079, "Multitude Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(9100, 9109, "Nordnet Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9120, 9124, "SEB", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(9130, 9149, "SEB", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(9150, 9169, "Skandiabanken", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9170, 9179, "Ikano Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(9180, 9189, "Danske Bank", RULE_ACCOUNT_ONLY, 10, CHECK_TYPE2_COMMENT1),
    ClearingRange(9190, 9199, "DNB Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9230, 9239, "Marginalen Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(9250, 9259, "SBAB Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(9260, 9269, "DNB Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9270, 9279, "ICA Banken", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(9280, 9289, "Resurs Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(9390, 9399, "Landshypotek Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9460, 9469, "Santander Consumer Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(9470, 9479, "BNP Paribas", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9500, 9549, "Nordea (PlusGirot)", RULE_PLUSGIROT, 10, CHECK_TYPE2_COMMENT3),
    ClearingRange(9550, 9569, "Avanza Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9570, 9579, "Sparbanken Syd", RULE_CLEARING_ACCOUNT_10, 10, CHECK_TYPE2_COMMENT1),
    ClearingRange(9580, 9589, "Aion Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(9590, 9599, "Erik Penser", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9630, 9639, "Lån & Spar Bank Sverige", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(9640, 9649, "NOBA Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9660, 9669, "Svea Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9670, 9679, "JAK Medlemsbank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9680, 9689, "Bluestep Finans", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT1),
    ClearingRange(9700, 9709, "Ekobanken", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9710, 9719, "Lunar Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9750, 9759, "Northmill Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9780, 9789, "Klarna Bank", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9880, 9889, "Riksgälden", RULE_CLEARING_ACCOUNT, 7, CHECK_TYPE1_COMMENT2),
    ClearingRange(9960, 9969, "Nordea (PlusGirot)", RULE_PLUSGIROT, 10, CHECK_TYPE2_COMMENT3),
)
# Deliberately not listed, so their numbers are refused on length rather than written in a form
# no bank guide confirms: Riksgälden 9890-9899 and Swedbank (formerly Sparbanken Öresund)
# 9300-9349, both type 2 with a 10-digit account (MIG Annex 5 does not say how to write them).

DEFAULT_ACCOUNT_LENGTH = 7


class Bban(NamedTuple):
    clearing: str  # 4 digits; 5 for Swedbank's 8-series (including the clearing check digit)
    account: str  # the account number after the clearing number, as given (no padding)
    bank: str | None  # bank name from CLEARING_RANGES, None for an unlisted clearing number
    rule: str  # one of the RULE_* constants
    bban: str  # the account as written in payment files (MIG Annex 5)
    # CHECK_DIGIT_OK, CHECK_DIGIT_FAILED (Swedbank 8-series / PlusGirot, which have exceptions:
    # warn, do not refuse) or CHECK_DIGIT_UNKNOWN (unlisted clearing number)
    check_digit: str = CHECK_DIGIT_OK


# --- Errors ----------------------------------------------------------------------------------

ERR_EMPTY = "empty"
ERR_BANKGIRO_LENGTH = "bankgiro_length"
ERR_BANKGIRO_CHECK_DIGIT = "bankgiro_check_digit"
ERR_PLUSGIRO_LENGTH = "plusgiro_length"
ERR_PLUSGIRO_CHECK_DIGIT = "plusgiro_check_digit"
ERR_BBAN_TOO_SHORT = "bban_too_short"
ERR_BBAN_LENGTH = "bban_length"
ERR_BBAN_CLEARING_CHECK_DIGIT = "bban_clearing_check_digit"
ERR_BBAN_AMBIGUOUS = "bban_ambiguous"
ERR_BBAN_CHECK_DIGIT = "bban_check_digit"
ERR_ZERO = "zero"
ERR_IBAN_INVALID = "iban_invalid"
ERR_IBAN_NOT_SWEDISH = "iban_not_swedish"
ERR_IBAN_NO_BBAN = "iban_no_bban"
ERR_ACCOUNT_TYPE_UNKNOWN = "account_type_unknown"

# English messages; the Odoo layer translates by code (see res.partner.bank).
MESSAGES = {
    ERR_EMPTY: "The account number is empty.",
    ERR_BANKGIRO_LENGTH: "Bankgiro number %(number)s has %(length)s digits; a Bankgiro number has 7 or 8.",
    ERR_BANKGIRO_CHECK_DIGIT: "Bankgiro number %(number)s has an invalid check digit.",
    ERR_PLUSGIRO_LENGTH: "Plusgiro number %(number)s has %(length)s digits; a Plusgiro number has 2 to 8.",
    ERR_PLUSGIRO_CHECK_DIGIT: "Plusgiro number %(number)s has an invalid check digit.",
    ERR_BBAN_TOO_SHORT: "Bank account %(number)s is too short to hold a clearing number and an account number.",
    ERR_BBAN_LENGTH: "Bank account %(number)s: clearing number %(clearing)s%(bank)s expects %(expected)s digits "
    "after the clearing number, found %(length)s.",
    ERR_BBAN_CLEARING_CHECK_DIGIT: "Bank account %(number)s: the Swedbank clearing number %(clearing)s has an "
    "invalid check digit (expected %(expected)s).",
    ERR_BBAN_AMBIGUOUS: "Bank account %(number)s: cannot tell where the Swedbank clearing number ends. Write it "
    "with its check digit and a separator, e.g. 8xxx-x followed by the account number.",
    ERR_BBAN_CHECK_DIGIT: "Bank account %(number)s has an invalid check digit for clearing number "
    "%(clearing)s%(bank)s. Check the clearing and account number with the payee.",
    ERR_ZERO: "Account number %(number)s is only zeros.",
    ERR_IBAN_INVALID: "%(number)s is not a valid IBAN.",
    ERR_IBAN_NOT_SWEDISH: "%(number)s is not a Swedish IBAN.",
    ERR_IBAN_NO_BBAN: "The domestic account number cannot be derived from IBAN %(number)s.",
    ERR_ACCOUNT_TYPE_UNKNOWN: "Account %(number)s has no known Swedish account type (Bankgiro, Plusgiro, "
    "bank account or IBAN).",
}


class InvalidAccountNumber(ValueError):
    """An account number that cannot be used in a Swedish payment file.

    ``code`` is one of the ERR_* constants and ``params`` the values for its message, so a caller
    can show a translated text instead of the English ``str(exc)``.
    """

    def __init__(self, code, **params):
        self.code = code
        self.params = params
        super().__init__(MESSAGES[code] % params)


# --- Digits and check digits -----------------------------------------------------------------

_PREFIX_RE = re.compile(r"^\s*(BANKGIRO|BG|PLUSGIRO|POSTGIRO|PG)(?![A-Z])\s*[:.]?", re.IGNORECASE)
_SUFFIX_RE = re.compile(r"(SEK|EUR)\s*$", re.IGNORECASE)


def digits_only(value):
    """All digits of ``value`` (``None`` and ``False`` give an empty string)."""
    return re.sub(r"\D", "", value or "")


def compact_reference(value):
    """A payment reference without whitespace (``None`` and ``False`` give an empty string)."""
    return re.sub(r"\s", "", value or "")


def luhn_check_digit(number):
    """Modulus 10 (Luhn) check digit, as a one-character string, for a string of digits."""
    total = 0
    for i, char in enumerate(reversed(number)):
        digit = int(char)
        if i % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def luhn_valid(number):
    """True when ``number`` is a non-empty string of digits whose last digit is its Luhn check digit."""
    if not number or not number.isdigit() or not number.isascii():
        return False
    return luhn_check_digit(number[:-1]) == number[-1]


def mod11_valid(number):
    """True when ``number`` is a non-empty string of digits that passes the modulus 11 check of
    Swedish bank accounts: weights 1, 2, ... 10, 1, 2 ... from the right, sum divisible by 11."""
    if not number or not number.isdigit() or not number.isascii():
        return False
    total = sum(int(char) * (i % 10 + 1) for i, char in enumerate(reversed(number)))
    return total % 11 == 0


def mod11_check_digit(number):
    """The modulus 11 check digit to append to ``number``, or ``None`` when no single digit fits
    (such a base number cannot carry a mod 11 check digit)."""
    # the check digit takes weight 1, so the base number starts at weight 2
    total = sum(int(char) * ((i + 1) % 10 + 1) for i, char in enumerate(reversed(number)))
    digit = (11 - total % 11) % 11
    return None if digit == 10 else str(digit)


def _all_zero(digits):
    return bool(digits) and not digits.strip("0")


def account_digits(acc_number):
    """Digits of an account number without a BG/PG prefix or a currency suffix (``BG 5555-5551`` -> ``55555551``)."""
    raw = _SUFFIX_RE.sub("", _PREFIX_RE.sub("", acc_number or ""))
    return digits_only(raw)


def bankgiro_valid(number):
    """A Bankgiro number: 7 or 8 digits with a valid check digit. A ``BG`` prefix and separators are ignored."""
    digits = account_digits(number)
    return 7 <= len(digits) <= 8 and luhn_valid(digits) and not _all_zero(digits)


def plusgiro_valid(number):
    """A Plusgiro number: 2 to 8 digits with a valid check digit. A ``PG`` prefix and separators are ignored."""
    digits = account_digits(number)
    return 2 <= len(digits) <= 8 and luhn_valid(digits) and not _all_zero(digits)


# --- References ------------------------------------------------------------------------------


def ocr_valid(reference, length_digit=False):
    """A Bankgiro/Plusgiro OCR reference: 2-25 digits ending with a Luhn check digit.

    With ``length_digit`` the second last digit must also be the reference's total length modulo
    10 (variable length control, "OCR 3"). Whitespace is ignored.
    """
    ref = compact_reference(reference)
    if not re.fullmatch(r"[0-9]{2,25}", ref) or not luhn_valid(ref) or _all_zero(ref):
        return False
    return not length_digit or (len(ref) >= 3 and ref[-2] == str(len(ref) % 10))


def make_ocr(payload, length_digit=False):
    """An OCR reference from a string of digits: optional length digit, then the Luhn check digit."""
    if not payload or not payload.isdigit() or not payload.isascii():
        raise ValueError("An OCR payload must be digits")
    if length_digit:
        payload += str((len(payload) + 2) % 10)
    ref = payload + luhn_check_digit(payload)
    if len(ref) > 25:
        raise ValueError("An OCR reference has at most 25 digits")
    return ref


def _mod97(text):
    """ISO 7064 mod 97-10 of an alphanumeric string (letters count as 10-35)."""
    return int("".join(str(int(char, 36)) for char in text)) % 97


def rf_valid(reference):
    """An ISO 11649 creditor reference: ``RF``, 2 check digits and 1-21 letters or digits. Spaces are ignored."""
    ref = compact_reference(reference).upper()
    if not re.fullmatch(r"RF[0-9]{2}[A-Z0-9]{1,21}", ref):
        return False
    return _mod97(ref[4:] + ref[:4]) == 1


def make_rf(payload):
    """An ISO 11649 creditor reference for 1-21 letters or digits (``make_rf("539007547034")`` -> ``RF18539007547034``)."""
    payload = compact_reference(payload).upper()
    if not re.fullmatch(r"[A-Z0-9]{1,21}", payload):
        raise ValueError("An RF payload is 1-21 letters or digits")
    return f"RF{98 - _mod97(payload + 'RF00'):02d}{payload}"


# --- IBAN ------------------------------------------------------------------------------------


def iban_compact(iban):
    return re.sub(r"[\s-]", "", iban or "").upper()


def iban_valid(iban):
    """Structure and ISO 7064 check digits of an IBAN; a Swedish IBAN must have 24 characters."""
    iban = iban_compact(iban)
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", iban):
        return False
    if iban.startswith("SE") and (len(iban) != 24 or not iban[2:].isdigit()):
        return False
    return _mod97(iban[4:] + iban[:4]) == 1


def bank_bic(bank):
    """BIC of a bank named as in CLEARING_RANGES, for the banks SE_IBAN_BANK_NAME lists; else None."""
    for code, name in SE_IBAN_BANK_NAME.items():
        if name == bank:
            return SE_IBAN_BANK_BIC.get(code)
    return None


def se_iban_bic(iban):
    """BIC for a Swedish IBAN from its bank code, or ``None`` when unknown or not a Swedish IBAN."""
    iban = iban_compact(iban)
    if not (iban.startswith("SE") and iban_valid(iban)):
        return None
    return SE_IBAN_BANK_BIC.get(iban[4:7])


def bban_from_se_iban(iban):
    """The domestic account (BBAN per MIG Annex 5) held in a Swedish IBAN.

    Only for IBANs that carry the clearing number, read by position from the 17 digits after the
    bank code: the last 7 are the account and the 4 before them the clearing number (``SEkk 5000
    0000 0CCC CAAA AAAA`` gives ``CCCCAAAAAAA``); for Swedbank's 8-series the last 10 are the
    account and the 5 before them the clearing number with its check digit. The rest must be
    zeros, the account must pass its bank's check digit, and the clearing number must belong to
    the bank the IBAN's bank code names (SE_IBAN_BANK_NAME). Banks that write the account only
    (Handelsbanken) are refused. Raises InvalidAccountNumber otherwise, rather than guess.
    """
    compact = iban_compact(iban)
    if not iban_valid(compact):
        raise InvalidAccountNumber(ERR_IBAN_INVALID, number=iban or "")
    if not compact.startswith("SE"):
        raise InvalidAccountNumber(ERR_IBAN_NOT_SWEDISH, number=iban)
    bank_code, body = compact[4:7], compact[7:]
    bank = SE_IBAN_BANK_NAME.get(bank_code)
    swedbank_8 = body[-15:-14] == "8"
    clearing_length, account_length = (5, 10) if swedbank_8 else (4, 7)
    prefix = body[: -(clearing_length + account_length)]
    clearing = body[-(clearing_length + account_length) : -account_length]
    account = body[-account_length:].lstrip("0") if swedbank_8 else body[-account_length:]
    if not bank or prefix.strip("0") or not account:
        raise InvalidAccountNumber(ERR_IBAN_NO_BBAN, number=iban)
    grouped = f"{clearing[:4]}-{clearing[4:]} {account}" if swedbank_8 else f"{clearing} {account}"
    try:
        parts = split_bban(grouped)
    except InvalidAccountNumber:
        raise InvalidAccountNumber(ERR_IBAN_NO_BBAN, number=iban) from None
    if parts.rule == RULE_ACCOUNT_ONLY or parts.bank != bank or parts.check_digit != CHECK_DIGIT_OK:
        raise InvalidAccountNumber(ERR_IBAN_NO_BBAN, number=iban)
    return parts.bban


# --- Account type ----------------------------------------------------------------------------


def _starts_with_clearing(raw, clearing):
    """True when the text ``raw`` begins with the digits ``clearing`` followed by a separator (or
    nothing), e.g. ``8327-9 12345`` for ``83279`` or ``8327``; ``8327912345`` does not."""
    candidates = [clearing]
    if len(clearing) == 4 and clearing.startswith("8"):
        candidates.append(clearing + luhn_check_digit(clearing))  # Swedbank: 8327 -> 8327-9
    for candidate in candidates:
        match = re.match(r"\s*" + r"\D?".join(candidate) + r"(?!\d)", raw)
        if match:
            return True
    return False


def _with_clearing(raw, clearing_number):
    """Prefix the account with a separately stored clearing number unless the account number
    already begins with it as a separate group (``5203 1234567`` or ``5203-1234567``)."""
    clearing = digits_only(clearing_number)
    if clearing and not _starts_with_clearing(raw, clearing):
        return f"{clearing_number.strip()} {raw}"
    return raw


def guess_account_type(acc_number, acc_type=None, clearing_number=None):
    """How a Swedish payment file writes the account: bankgiro, plusgiro, bban, iban or other.

    * ``acc_type`` ``iban`` (Odoo base_iban) or a valid IBAN -> iban
    * prefix ``BG``/``Bankgiro`` -> bankgiro; ``PG``/``Plusgiro``/``Postgiro`` -> plusgiro
    * ``123-4567`` / ``1234-5678`` -> bankgiro; ``1234567-8`` (up to 7 digits, dash, 1 digit) -> plusgiro
    * 9-15 digits (with a separately stored clearing number, if any) -> bban
    * anything else -> other (a bare 7- or 8-digit number can be either giro)
    """
    raw = (acc_number or "").strip()
    if acc_type == "iban" or iban_valid(raw):
        return "iban"
    prefix = _PREFIX_RE.match(raw)
    if prefix:
        return "bankgiro" if prefix.group(1).upper() in ("BG", "BANKGIRO") else "plusgiro"
    if re.fullmatch(r"\d{3,4}-\d{4}", raw):
        return "bankgiro"
    if re.fullmatch(r"\d{1,7}-\d", raw.replace(" ", "")):
        return "plusgiro"
    if 9 <= len(digits_only(_with_clearing(raw, clearing_number))) <= 15:
        return "bban"
    return "other"


# --- Bank accounts (BBAN) --------------------------------------------------------------------


def clearing_info(clearing):
    """The ClearingRange of a clearing number (its first four digits are used), or ``None``."""
    digits = digits_only(str(clearing))[:4]
    if len(digits) < 4:
        return None
    number = int(digits)
    for entry in CLEARING_RANGES:
        if entry.first <= number <= entry.last:
            return entry
    return None


def _clearing_group_hint(raw):
    """Length of the clearing number as the text is grouped: 5 for ``8xxx-x ...``/``8xxxx ...``,
    4 for ``8xxx 123...``, None when there is no grouping to go by."""
    groups = re.findall(r"\d+", raw)
    if len(groups) < 2:
        return None
    if len(groups[0]) == 5:
        return 5
    if len(groups[0]) == 4:
        return 5 if len(groups[1]) == 1 else 4
    return None


def _split_swedbank(raw, digits):
    """Split a Swedbank 8-series number into the 5-digit clearing number and the account number.

    The fifth clearing digit is a Luhn check digit over the first four. It may be missing from the
    stored number; the text's grouping decides first. Without grouping the reading must give a
    Luhn-valid account number (Swedbank accounts carry a mod 10 check digit as a rule), and a
    number that could just as well be another bank's account written without its clearing number
    (10 digits passing mod 10, or 9 digits passing Handelsbanken's mod 11) is refused as
    ambiguous: a separator settles it.
    """
    number = raw.strip()
    check = luhn_check_digit(digits[:4])
    clearing = digits[:4] + check
    with_check = digits[4] == check and 1 <= len(digits) - 5 <= 10  # 8xxx-x given
    without_check = 1 <= len(digits) - 4 <= 10  # 8xxx given, check digit left out
    hint = _clearing_group_hint(raw)
    if hint == 5:
        if digits[4] != check:
            raise InvalidAccountNumber(
                ERR_BBAN_CLEARING_CHECK_DIGIT, number=number, clearing=digits[:5], expected=clearing
            )
        if not with_check:
            raise InvalidAccountNumber(
                ERR_BBAN_LENGTH, number=number, clearing=clearing, bank=" (Swedbank)",
                expected="1-10", length=len(digits) - 5,
            )
        return clearing, digits[5:]
    if hint == 4 and without_check:
        return clearing, digits[4:]
    if hint is None:
        looks_account_only = (len(digits) == 10 and luhn_valid(digits)) or (
            len(digits) == 9 and mod11_valid(digits)
        )
        readings = []
        if with_check and luhn_valid(digits[5:]):
            readings.append(digits[5:])
        if without_check and luhn_valid(digits[4:]):
            readings.append(digits[4:])
        if looks_account_only or len({reading.lstrip("0") for reading in readings}) != 1:
            if not readings and not with_check and not without_check:
                raise InvalidAccountNumber(
                    ERR_BBAN_LENGTH, number=number, clearing=clearing, bank=" (Swedbank)",
                    expected="1-10", length=len(digits) - 5,
                )
            raise InvalidAccountNumber(ERR_BBAN_AMBIGUOUS, number=number)
        return clearing, readings[0]
    if with_check and without_check:
        if digits[4] == "0":
            # the extra digit is a leading zero of the account: both readings give the same BBAN
            return clearing, digits[5:]
        valid_with, valid_without = luhn_valid(digits[5:]), luhn_valid(digits[4:])
        if valid_with == valid_without:
            raise InvalidAccountNumber(ERR_BBAN_AMBIGUOUS, number=number)
        return (clearing, digits[5:]) if valid_with else (clearing, digits[4:])
    if with_check:
        return clearing, digits[5:]
    if without_check:
        return clearing, digits[4:]
    raise InvalidAccountNumber(
        ERR_BBAN_LENGTH, number=number, clearing=clearing, bank=" (Swedbank)", expected="1-10",
        length=len(digits) - 5,
    )


def split_bban(acc_number, clearing_number=None):
    """Split a Swedish bank account number and write it as MIG Annex 5 prescribes.

    ``acc_number`` holds the clearing number followed by the account number, with any separators
    (``8327-9, 123 456 789-7``). A separately stored ``clearing_number`` is put in front unless the
    account number already begins with it as a separate group.

    Returns a Bban; raises InvalidAccountNumber when the number does not fit its bank's format or
    fails its bank's check digit. Swedbank 8-series and PlusGirot accounts only check "as a rule";
    a failure there is reported in ``Bban.check_digit`` (CHECK_DIGIT_FAILED) instead, and an
    unlisted clearing number gives CHECK_DIGIT_UNKNOWN. Callers should warn in both cases.

    * Handelsbanken (6xxx): account number only, 9 digits
    * Swedbank 8-series: 5-digit clearing + account, zero-padded to 15 digits
    * Danske Bank 9180-9189, Nordea personal accounts (3300, 3782): account number only, 10 digits
    * Sparbanken Syd (9570-9579): clearing + 10-digit account, 14 digits
    * Nordea PlusGirot (9500-9549, 9960-9969): clearing + PlusGiro account number
    * all other banks, including unlisted clearing numbers: clearing + 7-digit account, 11 digits
    """
    raw = _with_clearing(_SUFFIX_RE.sub("", _PREFIX_RE.sub("", acc_number or "")), clearing_number)
    number = raw.strip()
    digits = digits_only(raw)
    if not digits:
        raise InvalidAccountNumber(ERR_EMPTY)
    if len(digits) < 5:
        raise InvalidAccountNumber(ERR_BBAN_TOO_SHORT, number=number)
    entry = clearing_info(digits)
    rule = entry.rule if entry else RULE_CLEARING_ACCOUNT
    bank = entry.bank if entry else None
    if rule == RULE_SWEDBANK_8:
        clearing, account = _split_swedbank(raw, digits)
        if _all_zero(account):
            raise InvalidAccountNumber(ERR_ZERO, number=number)
        return Bban(
            clearing,
            account,
            bank,
            rule,
            clearing + account.zfill(entry.account_length),
            _check_digit(entry, clearing, account, number),
        )
    clearing, account = digits[:4], digits[4:]
    length = entry.account_length if entry else DEFAULT_ACCOUNT_LENGTH
    if rule == RULE_PLUSGIROT:
        valid, expected = 2 <= len(account) <= length, f"2-{length}"
    else:
        valid, expected = len(account) == length, str(length)
    if not valid:
        raise InvalidAccountNumber(
            ERR_BBAN_LENGTH, number=number, clearing=clearing, bank=f" ({bank})" if bank else "",
            expected=expected, length=len(account),
        )
    if _all_zero(account):
        raise InvalidAccountNumber(ERR_ZERO, number=number)
    bban = account if rule == RULE_ACCOUNT_ONLY else clearing + account
    return Bban(clearing, account, bank, rule, bban, _check_digit(entry, clearing, account, number))


def _check_digit(entry, clearing, account, number):
    """CHECK_DIGIT_* of a split account; raises ERR_BBAN_CHECK_DIGIT when a check that has no
    exceptions fails ("Bankernas kontonummer")."""
    if entry is None:
        return CHECK_DIGIT_UNKNOWN
    check = entry.check
    if check == CHECK_TYPE1_COMMENT1:
        valid = mod11_valid(clearing[1:4] + account)
    elif check == CHECK_TYPE1_COMMENT2:
        valid = mod11_valid(clearing[:4] + account)
    elif check == CHECK_TYPE2_COMMENT2:
        valid = mod11_valid(account)
    else:  # CHECK_TYPE2_COMMENT1 / CHECK_TYPE2_COMMENT3: mod 10 over the account
        valid = luhn_valid(account)
    if valid:
        return CHECK_DIGIT_OK
    if check == CHECK_TYPE2_COMMENT3:
        return CHECK_DIGIT_FAILED
    raise InvalidAccountNumber(
        ERR_BBAN_CHECK_DIGIT, number=number, clearing=clearing, bank=f" ({entry.bank})"
    )


def normalize_bban(acc_number, clearing_number=None):
    """The account as written in payment files (MIG Annex 5); see split_bban."""
    return split_bban(acc_number, clearing_number).bban


# --- The account as a payment file writes it -------------------------------------------------


def payment_account_number(acc_number, account_type, clearing_number=None):
    """The payee account for a payment file, digits only (IBAN: compact, upper case).

    ``account_type`` is one of ACCOUNT_TYPES (see guess_account_type). Bankgiro and Plusgiro
    numbers are checked for length and check digit, bank accounts are written per MIG Annex 5,
    IBANs are checked. Raises InvalidAccountNumber.
    """
    number = (acc_number or "").strip()
    if not number:
        raise InvalidAccountNumber(ERR_EMPTY)
    if account_type in ("bankgiro", "plusgiro"):
        digits = account_digits(number)
        low, high, err_length, err_check = (
            (7, 8, ERR_BANKGIRO_LENGTH, ERR_BANKGIRO_CHECK_DIGIT)
            if account_type == "bankgiro"
            else (2, 8, ERR_PLUSGIRO_LENGTH, ERR_PLUSGIRO_CHECK_DIGIT)
        )
        if not low <= len(digits) <= high:
            raise InvalidAccountNumber(err_length, number=number, length=len(digits))
        if _all_zero(digits):
            raise InvalidAccountNumber(ERR_ZERO, number=number)
        if not luhn_valid(digits):
            raise InvalidAccountNumber(err_check, number=number)
        return digits
    if account_type == "bban":
        return normalize_bban(number, clearing_number)
    if account_type == "iban":
        if not iban_valid(number):
            raise InvalidAccountNumber(ERR_IBAN_INVALID, number=number)
        return iban_compact(number)
    raise InvalidAccountNumber(ERR_ACCOUNT_TYPE_UNKNOWN, number=number)


# --- Bank days --------------------------------------------------------------------------------


def _easter_sunday(year):
    """Easter Sunday (Gregorian calendar; anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    j = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * j) // 451
    month, day = divmod(h + j - 7 * m + 114, 31)
    return date(year, month, day + 1)


def bank_holidays(year):
    """Swedish weekdays without bank payments: public holidays plus midsummer, Christmas and New
    Year's Eve. Weekends are never bank days and are not listed."""
    easter = _easter_sunday(year)
    midsummer_eve = next(date(year, 6, d) for d in range(19, 26) if date(year, 6, d).weekday() == 4)
    return {
        date(year, 1, 1),  # New Year's Day
        date(year, 1, 6),  # Epiphany
        easter - timedelta(days=2),  # Good Friday
        easter + timedelta(days=1),  # Easter Monday
        date(year, 5, 1),  # May Day
        easter + timedelta(days=39),  # Ascension Day
        date(year, 6, 6),  # National Day
        midsummer_eve,
        date(year, 12, 24),  # Christmas Eve
        date(year, 12, 25),  # Christmas Day
        date(year, 12, 26),  # Boxing Day
        date(year, 12, 31),  # New Year's Eve
    }


def is_bank_day(day):
    """True for a Swedish bank day: Monday to Friday and not in bank_holidays."""
    return day.weekday() < 5 and day not in bank_holidays(day.year)


def previous_bank_day(day):
    """``day`` when it is a bank day, else the last bank day before it."""
    while not is_bank_day(day):
        day -= timedelta(days=1)
    return day
