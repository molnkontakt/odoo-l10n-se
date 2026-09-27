"""pytest for lib/se_bank.py, without Odoo.

Run from the repository root: ``python -m pytest -p no:cacheprovider l10n_se_bank_account/tests/pytest``.
The library is loaded from its file so the Odoo package (and its ``odoo`` import) is never imported.

All account numbers, giro numbers and references are invented; they only satisfy the check digits
("Bankernas kontonummer": mod 11 for type-1 banks and Handelsbanken, mod 10 for the others).
"""

import importlib.util
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parents[2] / "lib" / "se_bank.py"
_spec = importlib.util.spec_from_file_location("se_bank", _LIB)
se_bank = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(se_bank)

InvalidAccountNumber = se_bank.InvalidAccountNumber


def _iban(country, bban):
    """A check-digit-valid IBAN for an invented BBAN."""
    return f"{country}{98 - se_bank._mod97(bban + country + '00'):02d}{bban}"


SEB_IBAN = _iban("SE", "500" + "52031234560".zfill(17))  # SEB 5203-123 45 60
SWEDBANK_IBAN = _iban("SE", "800" + "832791234567897".zfill(17))  # Swedbank 8327-9, 123 456 789-7
HANDELSBANKEN_IBAN = _iban("SE", "600" + "123456789".zfill(17))
DE_IBAN = _iban("DE", "370400440532013000")


# --- Luhn --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "check"),
    [("5551234", "7"), ("555123", "9"), ("123456", "6"), ("7992739871", "3"), ("", "0"), ("0", "0")],
)
def test_luhn_check_digit(payload, check):
    assert se_bank.luhn_check_digit(payload) == check
    assert se_bank.luhn_valid(payload + check)


@pytest.mark.parametrize("number", ["", "1", "12", "55512348", "12a4", "５５５１２３９", None])
def test_luhn_invalid(number):
    assert not se_bank.luhn_valid(number)


def test_luhn_matches_every_single_digit_error():
    number = "55512347"
    assert se_bank.luhn_valid(number)
    for pos in range(len(number)):
        for digit in "0123456789":
            if digit != number[pos]:
                assert not se_bank.luhn_valid(number[:pos] + digit + number[pos + 1 :])


# --- Digits and account type --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("acc", "digits"),
    [
        ("BG 555-1239", "5551239"),
        ("Bankgiro: 5551-2347", "55512347"),
        ("PG 555 12 34-7SEK", "55512347"),
        ("pg 12 34 56-6 eur", "1234566"),
        ("8327-9, 123 456 789-7", "832791234567897"),
        ("", ""),
        (None, ""),
    ],
)
def test_account_digits(acc, digits):
    assert se_bank.account_digits(acc) == digits


@pytest.mark.parametrize(
    ("acc", "expected"),
    [
        # the shapes of the original pain module's cases
        ("BG 123-4567", "bankgiro"),
        ("123-4567", "bankgiro"),
        ("PG 12 34 56-7", "plusgiro"),
        ("8327-9 123 456 789-7", "bban"),
        (SWEDBANK_IBAN, "iban"),
        # other common shapes
        ("BG 5551-2347", "bankgiro"),
        ("BG 555-1239", "bankgiro"),
        ("PG 555 12 34-7SEK", "plusgiro"),
        ("55512347", "other"),  # bare 8 digits: Bankgiro or Plusgiro, cannot tell
        ("BG 5551-234567890123", "bankgiro"),  # 16 digits labelled BG; payment_account_number refuses it
        # prefix without a space, spelled out, other case
        ("BG5551239", "bankgiro"),
        ("bankgiro 555-1239", "bankgiro"),
        ("Postgiro 12 34 56-6", "plusgiro"),
        ("1234566-6", "plusgiro"),
        ("5551-2347", "bankgiro"),
        # IBANs are recognised without base_iban, with or without spaces
        (SEB_IBAN, "iban"),
        (" ".join(SWEDBANK_IBAN[i : i + 4] for i in range(0, 24, 4)), "iban"),
        (DE_IBAN, "iban"),
        (SWEDBANK_IBAN[:-1] + "8", "other"),  # wrong IBAN check digits: not an IBAN, too long for a BBAN
        ("5203-123 45 60", "bban"),
        ("6789 123 456 789", "bban"),
        ("1234567", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_guess_account_type(acc, expected):
    assert se_bank.guess_account_type(acc) == expected


def test_guess_account_type_acc_type_iban_wins():
    assert se_bank.guess_account_type("anything", acc_type="iban") == "iban"


def test_guess_account_type_with_separate_clearing_number():
    assert se_bank.guess_account_type("123 45 60") == "other"
    assert se_bank.guess_account_type("123 45 60", clearing_number="5203") == "bban"
    # already starts with the clearing number: not prefixed twice
    assert se_bank.guess_account_type("5203 123 45 60", clearing_number="5203") == "bban"
    # a Bankgiro stays a Bankgiro
    assert se_bank.guess_account_type("BG 555-1239", clearing_number="5203") == "bankgiro"


# --- Clearing table ---------------------------------------------------------------------------


def test_clearing_table_is_sorted_and_disjoint():
    ranges = se_bank.CLEARING_RANGES
    rules = {
        se_bank.RULE_CLEARING_ACCOUNT,
        se_bank.RULE_ACCOUNT_ONLY,
        se_bank.RULE_SWEDBANK_8,
        se_bank.RULE_CLEARING_ACCOUNT_10,
        se_bank.RULE_PLUSGIROT,
    }
    for entry in ranges:
        assert 1000 <= entry.first <= entry.last <= 9999
        assert entry.rule in rules
        assert entry.check in se_bank.CHECKS
        assert entry.bank
        # type 1 banks (clearing + 7 digits) use mod 11; the others are type 2
        type1 = entry.check in (se_bank.CHECK_TYPE1_COMMENT1, se_bank.CHECK_TYPE1_COMMENT2)
        assert type1 == (entry.rule == se_bank.RULE_CLEARING_ACCOUNT and entry.account_length == 7)
    for previous, entry in zip(ranges, ranges[1:], strict=False):
        assert previous.last < entry.first


@pytest.mark.parametrize(
    ("clearing", "bank", "rule"),
    [
        ("5203", "SEB", se_bank.RULE_CLEARING_ACCOUNT),
        ("9130", "SEB", se_bank.RULE_CLEARING_ACCOUNT),
        ("6000", "Handelsbanken", se_bank.RULE_ACCOUNT_ONLY),
        ("6999", "Handelsbanken", se_bank.RULE_ACCOUNT_ONLY),
        ("7001", "Swedbank", se_bank.RULE_CLEARING_ACCOUNT),
        ("8327-9", "Swedbank", se_bank.RULE_SWEDBANK_8),
        (8000, "Swedbank", se_bank.RULE_SWEDBANK_8),
        ("3299", "Nordea", se_bank.RULE_CLEARING_ACCOUNT),
        ("3300", "Nordea", se_bank.RULE_ACCOUNT_ONLY),
        ("3301", "Nordea", se_bank.RULE_CLEARING_ACCOUNT),
        ("3782", "Nordea", se_bank.RULE_ACCOUNT_ONLY),
        ("4000", "Nordea", se_bank.RULE_CLEARING_ACCOUNT),
        ("9880", "Riksgälden", se_bank.RULE_CLEARING_ACCOUNT),
        ("9179", "Ikano Bank", se_bank.RULE_CLEARING_ACCOUNT),
        ("9180", "Danske Bank", se_bank.RULE_ACCOUNT_ONLY),
        ("9189", "Danske Bank", se_bank.RULE_ACCOUNT_ONLY),
        ("9190", "DNB Bank", se_bank.RULE_CLEARING_ACCOUNT),
        ("9570", "Sparbanken Syd", se_bank.RULE_CLEARING_ACCOUNT_10),
        ("9579", "Sparbanken Syd", se_bank.RULE_CLEARING_ACCOUNT_10),
        ("9960", "Nordea (PlusGirot)", se_bank.RULE_PLUSGIROT),
    ],
)
def test_clearing_info(clearing, bank, rule):
    entry = se_bank.clearing_info(clearing)
    assert (entry.bank, entry.rule) == (bank, rule)


@pytest.mark.parametrize("clearing", ["9990", "9890", "9300", "9400", "0999", "123", "", None])
def test_clearing_info_unknown(clearing):
    assert se_bank.clearing_info(clearing) is None


# --- BBAN, MIG Annex 5 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("acc", "clearing", "bban", "bank"),
    [
        # SEB and other type-1 banks: clearing + 7-digit account, 11 digits
        ("5203-123 45 60", "5203", "52031234560", "SEB"),
        ("5839-8257466", "5839", "58398257466", "SEB"),  # SEB's own example
        ("1234 1234569", "1234", "12341234569", "Danske Bank"),
        ("3001 1234563", "3001", "30011234563", "Nordea"),
        ("7001-1234563", "7001", "70011234563", "Swedbank"),
        ("9150 1234562", "9150", "91501234562", "Skandiabanken"),  # comment 2: whole clearing number
        # unlisted clearing number: the default rule, bank unknown
        ("9990 1234567", "9990", "99901234567", None),
        # Handelsbanken: account number only, 9 digits
        ("6789 123 456 789", "6789", "123456789", "Handelsbanken"),
        # Danske Bank 9180-9189: account number only, 10 digits
        ("9180-1234567897", "9180", "1234567897", "Danske Bank"),
        # Nordea personal account: the 10-digit personal id number only
        ("3300 800101-1231", "3300", "8001011231", "Nordea"),
        # Sparbanken Syd: clearing + 10-digit account, 14 digits
        ("9570 123 456 789 7", "9570", "95701234567897", "Sparbanken Syd"),
        # Nordea PlusGirot as a bank account: clearing + PlusGiro number
        ("9960 123456-6", "9960", "99601234566", "Nordea (PlusGirot)"),
        # Swedbank 8-series: 5-digit clearing, account zero-padded to 15 digits in total
        ("8327-9 123 456 789-7", "83279", "832791234567897", "Swedbank"),
        ("8327-9, 12 345 678-2", "83279", "832790123456782", "Swedbank"),
        ("83279-1234566", "83279", "832790001234566", "Swedbank"),
        ("8327 1234567897", "83279", "832791234567897", "Swedbank"),  # check digit left out
        ("8327-9123456789-7", "83279", "832791234567897", "Swedbank"),  # 4-digit group, 11 digits: 5-digit clearing
        ("832791234567897", "83279", "832791234567897", "Swedbank"),  # 15 digits, no grouping
    ],
)
def test_split_bban(acc, clearing, bban, bank):
    parts = se_bank.split_bban(acc)
    assert (parts.clearing, parts.bban, parts.bank) == (clearing, bban, bank)
    assert se_bank.normalize_bban(acc) == bban
    assert parts.check_digit == (se_bank.CHECK_DIGIT_OK if bank else se_bank.CHECK_DIGIT_UNKNOWN)


# --- Check digits ("Bankernas kontonummer") ---------------------------------------------------


def test_mod11():
    assert se_bank.mod11_valid("1912763608957")  # Bankgirot's worked example, 13 digits
    assert se_bank.mod11_valid("8398257466")  # SEB 5839-8257466: clearing digits 2-4 + account
    assert not se_bank.mod11_valid("8398257467")
    assert not se_bank.mod11_valid("")
    assert se_bank.mod11_check_digit("839825746") == "6"
    for base in ("20312345", "123456", "0", "9150765432", "19127636089"):
        digit = se_bank.mod11_check_digit(base)
        assert digit is None or se_bank.mod11_valid(base + digit)
    assert any(se_bank.mod11_check_digit(str(n)) is None for n in range(100, 200))


def test_single_digit_typo_is_refused():
    """A typo in a type-1 account (SEB, comment 1) is caught by mod 11 (single-digit errors always
    are); before, any 7 digits were accepted."""
    number = "8257466"
    for pos in range(len(number)):
        for digit in "0123456789":
            if digit == number[pos]:
                continue
            typo = number[:pos] + digit + number[pos + 1 :]
            with pytest.raises(InvalidAccountNumber) as err:
                se_bank.split_bban(f"5839-{typo}")
            assert err.value.code == se_bank.ERR_BBAN_CHECK_DIGIT


@pytest.mark.parametrize(
    "acc",
    [
        "5000 1234568",  # SEB, comment 1
        "9150 1234560",  # Skandiabanken, comment 2 (valid under comment 1: the rule matters)
        "6789 123 456 788",  # Handelsbanken, mod 11 over 9 digits
        "9180-1234567890",  # Danske Bank 918x, mod 10 over 10 digits
        "3300 800101-1234",  # Nordea personal account (personal id number check digit)
        "9570 123 456 789 0",  # Sparbanken Syd
    ],
)
def test_check_digit_refused(acc):
    with pytest.raises(InvalidAccountNumber) as err:
        se_bank.split_bban(acc)
    assert err.value.code == se_bank.ERR_BBAN_CHECK_DIGIT
    assert err.value.params["bank"]


@pytest.mark.parametrize("acc", ["8327-9 123 456 789-0", "8327-9 1", "9960 123456-7"])
def test_check_digit_soft_for_swedbank_and_plusgirot(acc):
    """Swedbank 8-series and PlusGirot pass mod 10 only "as a rule": reported, not refused."""
    assert se_bank.split_bban(acc).check_digit == se_bank.CHECK_DIGIT_FAILED


@pytest.mark.parametrize(
    "acc",
    [
        "812345678",  # a Handelsbanken 9-digit account without its clearing number
        "8001011231",  # a Nordea personal account (3300) without its clearing number
        "8912345678",  # a Danske Bank 918x account without its clearing number
        "832791234567890",  # 15 digits whose account part fails mod 10
        "8327123456",  # neither reading gives a mod 10 valid account
    ],
)
def test_swedbank_without_separator_must_be_unambiguous(acc):
    """Without a separator an 8-series number is only read as Swedbank when exactly one reading
    passes mod 10 and it cannot be another bank's account without clearing number."""
    with pytest.raises(InvalidAccountNumber) as err:
        se_bank.split_bban(acc)
    assert err.value.code == se_bank.ERR_BBAN_AMBIGUOUS


@pytest.mark.parametrize(
    "acc", ["5203 0000000", "8327-9 0000000000", "80002 0000000000", "6789 000 000 000", "9960 00"]
)
def test_zero_account_refused(acc):
    with pytest.raises(InvalidAccountNumber) as err:
        se_bank.split_bban(acc)
    assert err.value.code == se_bank.ERR_ZERO


def test_bank_bic():
    assert se_bank.bank_bic("SEB") == "ESSESESS"
    assert se_bank.bank_bic("Swedbank") == "SWEDSESS"
    assert se_bank.bank_bic("Ikano Bank") is None
    assert se_bank.bank_bic(None) is None


def test_split_bban_keeps_account_part():
    parts = se_bank.split_bban("8327-9, 12 345 678-2")
    assert parts.account == "123456782"
    assert parts.rule == se_bank.RULE_SWEDBANK_8


def test_swedbank_without_grouping_uses_account_check_digit():
    # 14 digits: 5-digit clearing + 9-digit account, or 4-digit clearing + 10-digit account.
    # "83279" + "12345674" (Luhn-valid) vs "8327" + "912345674" (not Luhn-valid)
    assert se_bank.split_bban("8327912345674").bban == "832790012345674"
    # "8327" + "1234567897" (Luhn-valid); "83271..." has the wrong clearing check digit
    assert se_bank.split_bban("83271234567897").bban == "832791234567897"
    # both readings Luhn-valid is impossible for the same digits shifted by one, but a number
    # where neither is valid is refused (see test_swedbank_ambiguous)


def test_swedbank_ambiguous():
    # Neither reading gives a Luhn-valid account number: refuse rather than guess
    ambiguous = "83279" + "1234568"
    assert not se_bank.luhn_valid(ambiguous[5:]) and not se_bank.luhn_valid(ambiguous[4:])
    with pytest.raises(InvalidAccountNumber) as err:
        se_bank.split_bban(ambiguous)
    assert err.value.code == se_bank.ERR_BBAN_AMBIGUOUS
    # with a separator the grouping decides; the failed account check digit is only reported
    parts = se_bank.split_bban("8327-9 1234568")
    assert parts.bban == "832790001234568"
    assert parts.check_digit == se_bank.CHECK_DIGIT_FAILED


def test_swedbank_clearing_check_digit_zero():
    # 8001-0: a fifth digit 0 is the clearing check digit and/or a leading zero of the account;
    # both readings give the same 15 digits
    assert se_bank.luhn_check_digit("8001") == "0"
    assert se_bank.normalize_bban("80010123456782") == "800100123456782"


def test_swedbank_wrong_clearing_check_digit():
    with pytest.raises(InvalidAccountNumber) as err:
        se_bank.split_bban("8327-5 123 456 789-0")
    assert err.value.code == se_bank.ERR_BBAN_CLEARING_CHECK_DIGIT
    assert err.value.params["expected"] == "83279"


@pytest.mark.parametrize("acc", ["8327-9", "8327-9 12345678901", "83279 12345678901"])
def test_swedbank_length(acc):
    with pytest.raises(InvalidAccountNumber) as err:
        se_bank.split_bban(acc)
    assert err.value.code == se_bank.ERR_BBAN_LENGTH


@pytest.mark.parametrize(
    ("acc", "expected", "length"),
    [
        ("5203 123456", "7", 6),  # SEB, account one digit short
        ("9890 1234567897", "7", 10),  # Riksgälden type 2: not supported, refused on length
        ("5203 12345678", "7", 8),
        ("6789 12345678", "9", 8),  # Handelsbanken
        ("123 456 789", "7", 5),  # Handelsbanken account without its clearing number
        ("9180 123456789", "10", 9),  # Danske Bank 918x
        ("9570 123456789", "10", 9),  # Sparbanken Syd
        ("3300 8001011231 5", "10", 11),  # Nordea personal account
        ("9960 1", "2-10", 1),  # PlusGirot
    ],
)
def test_split_bban_length(acc, expected, length):
    with pytest.raises(InvalidAccountNumber) as err:
        se_bank.split_bban(acc)
    assert err.value.code == se_bank.ERR_BBAN_LENGTH
    assert (err.value.params["expected"], err.value.params["length"]) == (expected, length)


@pytest.mark.parametrize(("acc", "code"), [("", se_bank.ERR_EMPTY), (None, se_bank.ERR_EMPTY), ("1234", se_bank.ERR_BBAN_TOO_SHORT)])
def test_split_bban_too_short(acc, code):
    with pytest.raises(InvalidAccountNumber) as err:
        se_bank.split_bban(acc)
    assert err.value.code == code


def test_split_bban_separate_clearing_number():
    assert se_bank.normalize_bban("123 45 60", "5203") == "52031234560"
    assert se_bank.normalize_bban("5203 123 45 60", "5203") == "52031234560"
    assert se_bank.normalize_bban("5203-1234560", "5203") == "52031234560"
    assert se_bank.normalize_bban("123 456 789", "6789") == "123456789"
    assert se_bank.normalize_bban("123 456 789-7", "8327-9") == "832791234567897"
    assert se_bank.normalize_bban("1234567897", "8327") == "832791234567897"
    assert se_bank.normalize_bban("8327-9 123 456 789-7", "8327") == "832791234567897"
    # an account that happens to begin with the clearing digits is not taken for the clearing
    # number: 8327912345 with clearing 8327-9 is the account 8327912345
    parts = se_bank.split_bban("8327912345", "8327-9")
    assert parts.account == "8327912345"


# --- Giro numbers and references --------------------------------------------------------------


@pytest.mark.parametrize(
    ("number", "valid"),
    [
        ("5551239", True),
        ("BG 555-1239", True),
        ("5551-2347", True),
        ("5551238", False),  # check digit
        ("555123", False),  # 6 digits
        ("555123456", False),  # 9 digits
        ("", False),
        ("0000000", False),  # passes Luhn, but is no Bankgiro number
    ],
)
def test_bankgiro_valid(number, valid):
    assert se_bank.bankgiro_valid(number) is valid


@pytest.mark.parametrize(
    ("number", "valid"),
    [
        ("18", True),
        ("PG 12 34 56-6", True),
        ("5551-2347", True),
        ("1234567", False),
        ("1", False),
        ("555123470", False),
        ("00", False),
    ],
)
def test_plusgiro_valid(number, valid):
    assert se_bank.plusgiro_valid(number) is valid


@pytest.mark.parametrize(
    ("reference", "valid"),
    [
        ("18", True),  # 2 digits: shortest OCR
        ("1", False),  # 1 digit
        ("1234 5674", True),  # spaces ignored
        ("1234567897", True),
        ("1234567898", False),
        ("12345-67897", False),  # not digits only
        ("1" * 24 + se_bank.luhn_check_digit("1" * 24), True),  # 25 digits
        ("1" * 25 + se_bank.luhn_check_digit("1" * 25), False),  # 26 digits
        ("", False),
        (None, False),
        ("00", False),  # all zeros pass Luhn but are no reference
        ("0" * 25, False),
    ],
)
def test_ocr_valid(reference, valid):
    assert se_bank.ocr_valid(reference) is valid


def test_ocr_length_digit():
    ref = se_bank.make_ocr("12345", length_digit=True)
    assert ref == "1234574"  # 7 digits: length digit 7, then the check digit
    assert se_bank.ocr_valid(ref, length_digit=True)
    assert se_bank.ocr_valid(ref)
    without = se_bank.make_ocr("123456")
    assert se_bank.ocr_valid(without)
    assert not se_bank.ocr_valid(without, length_digit=True)
    # length 12: length digit 2
    long_ref = se_bank.make_ocr("1234567890", length_digit=True)
    assert len(long_ref) == 12 and long_ref[-2] == "2"
    assert se_bank.ocr_valid(long_ref, length_digit=True)
    assert not se_bank.ocr_valid("18", length_digit=True)


def test_make_ocr_errors():
    with pytest.raises(ValueError):
        se_bank.make_ocr("12a")
    with pytest.raises(ValueError):
        se_bank.make_ocr("1" * 25)
    assert se_bank.make_ocr("1" * 24) == "1" * 24 + se_bank.luhn_check_digit("1" * 24)


@pytest.mark.parametrize(
    ("reference", "valid"),
    [
        ("RF18539007547034", True),  # ISO 11649 example
        ("RF18 5390 0754 7034", True),
        ("rf18 5390 0754 7034", True),
        ("RF19539007547034", False),
        ("RF18539007547035", False),
        ("RF18", False),
        ("RF18" + "A" * 22, False),  # more than 21 characters after the check digits
        ("XX18539007547034", False),
        ("RF1853900754703-", False),
        ("", False),
        (None, False),
    ],
)
def test_rf_valid(reference, valid):
    assert se_bank.rf_valid(reference) is valid


@pytest.mark.parametrize("payload", ["539007547034", "1", "ABC123", "Z" * 21, "invoice 42"])
def test_make_rf(payload):
    ref = se_bank.make_rf(payload)
    assert ref.startswith("RF") and se_bank.rf_valid(ref)


def test_make_rf_known_value_and_errors():
    assert se_bank.make_rf("539007547034") == "RF18539007547034"
    with pytest.raises(ValueError):
        se_bank.make_rf("")
    with pytest.raises(ValueError):
        se_bank.make_rf("A" * 22)


def test_compact_reference():
    assert se_bank.compact_reference(" 1234 5674\t") == "12345674"
    assert se_bank.compact_reference(False) == ""


# --- IBAN -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("iban", "valid"),
    [
        (SEB_IBAN, True),
        (SWEDBANK_IBAN, True),
        (" ".join(SWEDBANK_IBAN[i : i + 4] for i in range(0, 24, 4)), True),
        (DE_IBAN, True),
        (SWEDBANK_IBAN[:2] + "00" + SWEDBANK_IBAN[4:], False),  # check digits
        (SWEDBANK_IBAN[:-1], False),  # Swedish IBAN of 23 characters
        (SWEDBANK_IBAN[:-2] + "A" + SWEDBANK_IBAN[-1], False),  # letter in a Swedish IBAN
        ("12", False),
        ("", False),
        (None, False),
    ],
)
def test_iban_valid(iban, valid):
    assert se_bank.iban_valid(iban) is valid


def test_se_iban_bic():
    assert se_bank.se_iban_bic(SEB_IBAN) == "ESSESESS"
    assert se_bank.se_iban_bic(SWEDBANK_IBAN) == "SWEDSESS"
    assert se_bank.se_iban_bic(HANDELSBANKEN_IBAN) == "HANDSESS"
    assert se_bank.se_iban_bic(_iban("SE", "915" + "91501234567".zfill(17))) == "SKIASESS"
    assert se_bank.se_iban_bic(_iban("SE", "999" + "1".zfill(17))) is None
    assert se_bank.se_iban_bic(DE_IBAN) is None
    assert se_bank.se_iban_bic(SWEDBANK_IBAN[:2] + "00" + SWEDBANK_IBAN[4:]) is None


def test_bban_from_se_iban():
    assert se_bank.bban_from_se_iban(SEB_IBAN) == "52031234560"
    assert se_bank.bban_from_se_iban(SWEDBANK_IBAN) == "832791234567897"
    assert se_bank.bban_from_se_iban(_iban("SE", "800" + "832790001234566".zfill(17))) == "832790001234566"
    for iban, code in [
        (HANDELSBANKEN_IBAN, se_bank.ERR_IBAN_NO_BBAN),  # account-only bank: no clearing in the IBAN
        (_iban("SE", "500" + "70011234563".zfill(17)), se_bank.ERR_IBAN_NO_BBAN),  # SEB code, Swedbank clearing
        (_iban("SE", "500" + "52031234567".zfill(17)), se_bank.ERR_IBAN_NO_BBAN),  # account check digit
        (_iban("SE", "500" + "152031234560".zfill(17)), se_bank.ERR_IBAN_NO_BBAN),  # digits before the clearing
        (_iban("SE", "800" + "832791234567890".zfill(17)), se_bank.ERR_IBAN_NO_BBAN),  # Luhn fails: no guess
        (_iban("SE", "500" + "1".zfill(17)), se_bank.ERR_IBAN_NO_BBAN),
        (DE_IBAN, se_bank.ERR_IBAN_NOT_SWEDISH),
        (SWEDBANK_IBAN[:2] + "00" + SWEDBANK_IBAN[4:], se_bank.ERR_IBAN_INVALID),
    ]:
        with pytest.raises(InvalidAccountNumber) as err:
            se_bank.bban_from_se_iban(iban)
        assert err.value.code == code, iban


# --- The number a payment file writes ---------------------------------------------------------


@pytest.mark.parametrize(
    ("acc", "account_type", "expected"),
    [
        ("BG 555-1239", "bankgiro", "5551239"),
        ("BG 5551-2347", "bankgiro", "55512347"),
        ("PG 555 12 34-7SEK", "plusgiro", "55512347"),
        ("PG 1-8", "plusgiro", "18"),
        ("55512347", "bankgiro", "55512347"),  # bare number, type set by hand
        ("6789 123 456 789", "bban", "123456789"),
        ("8327-9, 12 345 678-2", "bban", "832790123456782"),
        (SWEDBANK_IBAN.lower(), "iban", SWEDBANK_IBAN),
    ],
)
def test_payment_account_number(acc, account_type, expected):
    assert se_bank.payment_account_number(acc, account_type) == expected


def test_payment_account_number_clearing_number():
    assert se_bank.payment_account_number("123 45 60", "bban", clearing_number="5203") == "52031234560"


@pytest.mark.parametrize(
    ("acc", "account_type", "code"),
    [
        ("BG 5551-234567890123", "bankgiro", se_bank.ERR_BANKGIRO_LENGTH),
        ("BG 555-123", "bankgiro", se_bank.ERR_BANKGIRO_LENGTH),
        ("BG 555-1238", "bankgiro", se_bank.ERR_BANKGIRO_CHECK_DIGIT),
        ("PG 1", "plusgiro", se_bank.ERR_PLUSGIRO_LENGTH),
        ("PG 555123470", "plusgiro", se_bank.ERR_PLUSGIRO_LENGTH),
        ("PG 12 34 56-7", "plusgiro", se_bank.ERR_PLUSGIRO_CHECK_DIGIT),
        ("6789 12345678", "bban", se_bank.ERR_BBAN_LENGTH),
        ("5203-123 45 67", "bban", se_bank.ERR_BBAN_CHECK_DIGIT),
        ("BG 000-0000", "bankgiro", se_bank.ERR_ZERO),
        ("PG 0-0", "plusgiro", se_bank.ERR_ZERO),
        (SWEDBANK_IBAN[:2] + "00" + SWEDBANK_IBAN[4:], "iban", se_bank.ERR_IBAN_INVALID),
        ("55512347", "other", se_bank.ERR_ACCOUNT_TYPE_UNKNOWN),
        ("55512347", None, se_bank.ERR_ACCOUNT_TYPE_UNKNOWN),
        ("", "bankgiro", se_bank.ERR_EMPTY),
        (None, "bban", se_bank.ERR_EMPTY),
    ],
)
def test_payment_account_number_errors(acc, account_type, code):
    with pytest.raises(InvalidAccountNumber) as err:
        se_bank.payment_account_number(acc, account_type)
    assert err.value.code == code
    assert isinstance(err.value, ValueError)


# --- Messages ---------------------------------------------------------------------------------


def test_every_error_code_has_a_message():
    codes = {value for name, value in vars(se_bank).items() if name.startswith("ERR_")}
    assert codes == set(se_bank.MESSAGES)
    params = {"number": "X", "length": 1, "clearing": "1234", "bank": " (B)", "expected": "7"}
    for code in codes:
        assert str(InvalidAccountNumber(code, **params))


# --- Bank days ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("year", "easter"),
    [(2024, (3, 31)), (2025, (4, 20)), (2026, (4, 5)), (2027, (3, 28)), (2038, (4, 25))],
)
def test_easter_sunday(year, easter):
    assert se_bank._easter_sunday(year) == se_bank.date(year, *easter)


def test_bank_holidays_2026():
    d = se_bank.date
    holidays = se_bank.bank_holidays(2026)
    for day in [
        d(2026, 1, 1), d(2026, 1, 6), d(2026, 4, 3), d(2026, 4, 6), d(2026, 5, 1), d(2026, 5, 14),
        d(2026, 6, 6), d(2026, 6, 19), d(2026, 12, 24), d(2026, 12, 25), d(2026, 12, 26), d(2026, 12, 31),
    ]:
        assert day in holidays, day
        assert not se_bank.is_bank_day(day)
    assert se_bank.is_bank_day(d(2026, 6, 18))  # Thursday before midsummer eve
    assert not se_bank.is_bank_day(d(2026, 9, 26))  # Saturday
    assert se_bank.is_bank_day(d(2026, 9, 28))


def test_previous_bank_day():
    d = se_bank.date
    assert se_bank.previous_bank_day(d(2026, 9, 28)) == d(2026, 9, 28)
    assert se_bank.previous_bank_day(d(2026, 9, 27)) == d(2026, 9, 25)  # Sunday -> Friday
    assert se_bank.previous_bank_day(d(2026, 4, 6)) == d(2026, 4, 2)  # Easter Monday -> Maundy Thursday
    assert se_bank.previous_bank_day(d(2027, 1, 1)) == d(2026, 12, 30)  # past New Year's Eve
