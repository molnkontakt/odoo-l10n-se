# Sweden - Bank accounts, Bankgiro, Plusgiro, OCR

Shared Swedish account-number rules for modules that write payment files: account types, check
digits and the way each bank's account number is written in ISO 20022 files. Depends only on
`account`. Used by [`l10n_se_account_banking_pain`](../l10n_se_account_banking_pain/) and
[`l10n_se_payment_file_seb_csv`](../l10n_se_payment_file_seb_csv/).

## Bank accounts

Bank accounts (`res.partner.bank`) get a **Swedish account type**, guessed from the number and
editable by hand:

| Number | Type |
|---|---|
| `BG 5551-2347`, `Bankgiro 555-1239`, `555-1239`, `5551-2347` | Bankgiro |
| `PG 12 34 56-6`, `Plusgiro ...`, `123456-6` (up to 7 digits, dash, check digit) | Plusgiro |
| a valid IBAN (with or without `base_iban`) | IBAN |
| 9-15 digits: clearing number + account number (the *Clearing Number* field is used when filled) | Bank account |
| anything else, e.g. a bare `55512347` (Bankgiro or Plusgiro?) | Other: set the type by hand |

A type set by hand is kept until the account number or the *Clearing Number* field changes. The
form also shows the **number in payment files** (see below), the bank found from the clearing
number, and a warning when the number cannot be used: wrong Bankgiro/Plusgiro check digit or
length, a bank account whose length or check digit does not fit its bank, an invalid IBAN, a
number of only zeros. A second, softer warning appears when a bank account's check digit could
not be confirmed (see below).

For a Bankgiro or Plusgiro number the form also says what the payee accepts, which SEB's payment
form shows when the number is typed: **Payee requires OCR** ("Du måste fylla i OCR") or **Payee
accepts only messages** ("tillåter bara textmeddelanden, inte OCR"), never both. The payment-file
modules follow them: no payment without a valid OCR number to the first, never an OCR or RF
reference to the second.

## Giro numbers in imported UBL/Peppol bills

A Swedish supplier's Peppol BIS 3 bill carries the bare giro digits in
`cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID` and says which giro it is in
`cac:FinancialInstitutionBranch/cbc:ID`: `SE:BANKGIRO` or `SE:PLUSGIRO` (the Swedish national
rules). Odoo's import reads only the digits, so the account would be created as `5551239`, type
*Other*, and the payment-file modules could not pay the bill until someone retyped it.

When a vendor bill is imported from UBL (Peppol, e-mail or upload) this module reads the scheme
(`FinancialInstitutionBranch/ID` or the account `ID`'s `schemeID`; `SE:BANKGIRO`, `SE:PLUSGIRO`,
`Bankgiro`, `BG`, `PG`, `Postgiro`, case-insensitive) and:

* creates the account as `BG 555-1239` / `PG 1234567-4` with the Swedish account type filled in,
  after checking the check digit (`lib/se_bank.py`: `format_bankgiro`, `format_plusgiro`,
  `giro_type_from_scheme`);
* **reuses** an account the supplier already has with the same digits, however it is written
  (`BG 555-1239`, `555-1239` or `5551239`), without changing it. A bare number left from an
  earlier import keeps its type *Other* and the bill gets a chatter note asking for the type to be
  filled in; an account typed as the *other* giro is a different payee and is not reused;
* puts the **Bankgiro first** as the bill's bank account when the bill offers both, else the
  Plusgiro, then whatever else the bill names (an IBAN, say);
* keeps a number with an **invalid check digit** as Odoo creates it (raw digits, type *Other*)
  and adds a chatter note naming the number;
* leaves a number **without a scheme** alone: a bare 7- or 8-digit number can be either giro and
  is never guessed, in UBL as when typed by hand.

Only vendor bills are touched: a vendor credit note or a customer invoice names the company's own
account, which Odoo never creates from a file. Plain UBL 2.0/2.1 files without the Peppol
customization id go through Odoo's older import path and are not handled.

## Bank account numbers in payment files (MIG Annex 5)

Swedish banks' ISO 20022 guides (SEB MIG pain.001.001.03, Annex 5) write a domestic account
(BBAN) differently per clearing number range:

| Bank (clearing) | Written as | Example (invented) |
|---|---|---|
| Handelsbanken (6000-6999) | account number only, 9 digits | `6789 123 456 789` -> `123456789` |
| Swedbank 8-series (8000-8999) | 5-digit clearing + account, zero-padded to 15 digits | `8327-9, 12 345 678-2` -> `832790123456782` |
| Danske Bank 9180-9189 | account number only, 10 digits | `9180-1234567897` -> `1234567897` |
| Nordea personal account (3300, 3782) | the 10-digit personal id number | `3300 800101-1231` -> `8001011231` |
| Sparbanken Syd (9570-9579) | clearing + 10-digit account, 14 digits | `9570 123 456 789 7` -> `95701234567897` |
| Nordea PlusGirot (9500-9549, 9960-9969) | clearing + PlusGiro number | `9960 123456-6` -> `99601234566` |
| SEB, Swedbank 7-series, Nordea, all others | clearing + 7-digit account, 11 digits | `5203-123 45 60` -> `52031234560` |

The clearing number table is data in `lib/se_bank.py` (`CLEARING_RANGES`); an unlisted clearing
number gets the default rule. Swedbank's fifth clearing digit is a check digit over the first
four: it is added when left out, a wrong one is refused, and a number without separators whose
split is ambiguous is refused rather than guessed.

### Check digits

The account number's check digit is tested as Bankgirot's *Bankernas kontonummer* (2024-02-22)
prescribes for each clearing number range:

| Type / comment | Banks | Check |
|---|---|---|
| type 1, comment 1 | SEB, Nordea, Danske Bank 12xx-13xx/24xx, Swedbank 7-series, ICA, SBAB, ... | mod 11 over clearing digits 2-4 + the 7-digit account |
| type 1, comment 2 | Skandiabanken, Avanza, Nordnet, Nordea 4xxx, DNB, Klarna, Ålandsbanken, ... | mod 11 over the 4-digit clearing + the 7-digit account |
| type 2, comment 1 | Danske Bank 9180-9189, Nordea personal accounts, Sparbanken Syd | mod 10 over the 10-digit account |
| type 2, comment 2 | Handelsbanken | mod 11 over the 9-digit account |
| type 2, comment 3 | Swedbank 8-series, Nordea PlusGirot | mod 10 over the account, *as a rule* |

A failed check is an error, except for type 2 comment 3: Swedbank and PlusGirot have accounts
that do not pass, so a failure there is only a warning (`Bban.check_digit == "failed"`). A
clearing number the list does not have cannot be checked and also gives a warning
(`"unknown"`). An 8-series number written without separators is only read as Swedbank when
exactly one reading gives a mod 10 valid account and the digits are not also a complete type 2
account of another bank (10 digits passing mod 10, 9 passing mod 11): a Handelsbanken, Danske
Bank or Nordea personal account entered without its clearing number is refused instead of
being paid to a Swedbank account. Not supported (refused on length): Riksgälden 9890-9899 and
Swedbank 9300-9349, whose form in payment files the MIG does not give.

## Library (`lib/se_bank.py`)

Pure Python, no Odoo import: `from odoo.addons.l10n_se_bank_account.lib import se_bank`.

| Function | Returns |
|---|---|
| `guess_account_type(acc_number, acc_type=None, clearing_number=None)` | `"bankgiro"`, `"plusgiro"`, `"bban"`, `"iban"` or `"other"` |
| `payment_account_number(acc_number, account_type, clearing_number=None)` | the payee account for a file (digits; IBAN compact); raises `InvalidAccountNumber` |
| `split_bban(acc_number, clearing_number=None)` | `Bban(clearing, account, bank, rule, bban, check_digit)`; raises `InvalidAccountNumber` |
| `normalize_bban(acc_number, clearing_number=None)` | `split_bban(...).bban` |
| `bban_from_se_iban(iban)` | the BBAN held in a Swedish IBAN that carries the clearing number (e.g. SEB); raises otherwise |
| `clearing_info(clearing)` | `ClearingRange(first, last, bank, rule, account_length, check)` or `None` |
| `bank_bic(bank)` | BIC of a bank named as in `CLEARING_RANGES`, for the major banks; else `None` |
| `mod11_valid(number)`, `mod11_check_digit(payload)` | the banks' modulus 11 check |
| `is_bank_day(day)`, `previous_bank_day(day)`, `bank_holidays(year)` | Swedish bank days (weekends, public holidays, midsummer, Christmas and New Year's Eve are not) |
| `account_digits(acc_number)` | digits without `BG`/`PG` prefix or currency suffix |
| `bankgiro_valid(number)`, `plusgiro_valid(number)` | length (7-8 / 2-8) and mod 10 check digit |
| `format_bankgiro(number)`, `format_plusgiro(number)`, `format_giro(number, account_type)` | `BG 555-1239` / `BG 5551-2347`, `PG 1234567-4`; no validation |
| `giro_type_from_scheme(scheme)` | `"bankgiro"`/`"plusgiro"` for a UBL payment account scheme such as `SE:BANKGIRO`, else `None` |
| `ocr_valid(reference, length_digit=False)`, `make_ocr(payload, length_digit=False)` | OCR: 2-25 digits, Luhn, optional length digit |
| `rf_valid(reference)`, `make_rf(payload)` | ISO 11649 creditor reference (`RF` + mod 97) |
| `iban_valid(iban)`, `iban_compact(iban)`, `se_iban_bic(iban)` | IBAN check digits; BIC from a Swedish IBAN's bank code |
| `luhn_valid(number)`, `luhn_check_digit(payload)`, `digits_only(value)`, `compact_reference(value)` | helpers |

`InvalidAccountNumber` is a `ValueError` with `.code` (one of the `ERR_*` constants) and
`.params`; `str(exc)` is an English message. On `res.partner.bank` the same functions are
available as methods that raise a translated `UserError`: `_l10n_se_payment_account()`,
`_l10n_se_bban()`, `_l10n_se_domestic_bban()` (own account as BBAN, from a bank account or a
Swedish IBAN), `_l10n_se_account_problem_message()` (translated reason or `False`),
`_l10n_se_account_warning_message()` (check digit not confirmed: translated text or `False`),
`_l10n_se_bic()`, `_l10n_se_account_digits()`, `_l10n_se_error_message(exc)`.

## Tests

* Odoo: `tests/test_res_partner_bank.py` and `tests/test_ubl_import.py` (a Peppol BIS 3 bill with a
  Bankgiro and a Plusgiro, `tests/data/peppol_bill_giro.xml`) (`--test-tags /l10n_se_bank_account`)
* Library, without Odoo, from the repository root:

  ```
  python -m pytest -p no:cacheprovider --confcutdir=l10n_se_bank_account/tests/pytest l10n_se_bank_account/tests/pytest
  ```

  (`--confcutdir` keeps pytest from importing the Odoo package above the test directory.)

All account numbers in the tests and this README are invented; they only satisfy the check digits.
