# Sweden - ISO 20022 supplier payments (pain.001)

Swedish domestic payments in the pain.001 payment file of OCA
[`account_banking_sepa_credit_transfer`](https://github.com/OCA/bank-payment): pay vendor bills
to **Bankgiro**, **Plusgiro**, a Swedish **bank account** (clearing + account number) or an
**IBAN**, with **OCR** as structured reference. OCA's generator is built for SEPA (IBAN, EUR);
this module adds the Swedish rules of the bank's implementation guide (MIG).

## Usage

1. Payment mode (Invoicing → Configuration → Payment Modes) with payment method *SEPA Credit
   Transfer* (pain.001.001.03), set **Swedish bank profile** and the **customer id** from your
   file-transfer agreement with the bank.
2. Vendor bank accounts get a **Swedish account type** from
   [`l10n_se_bank_account`](../l10n_se_bank_account/), guessed from the number: prefix `BG`/`PG`,
   `555-1239` / `5551-2347` (Bankgiro), `123456-6` (Plusgiro), a valid IBAN, otherwise clearing +
   account. Correct it on the account when the guess is wrong. Generating the file refuses an
   account with an unknown type, a Bankgiro/Plusgiro number with a wrong check digit or length, or
   a bank account whose length or check digit does not fit its clearing number.
3. Create a payment order from the vendor bills as usual and generate the file. A bill whose
   *Payment Reference* is numeric and Luhn-valid is paid with that OCR number (SCOR); any other
   reference goes as free text (max 140 characters).

## What goes in the file (SEB profile, MIG pain.001.001.03 section 7.10)

| | Creditor agent | Creditor account | Remittance |
|---|---|---|---|
| Bankgiro | `ClrSysMmbId` SESBA / 9900 | `Othr/Id` + `SchmeNm/Prtry` **BGNR** | OCR as `Strd/CdtrRefInf` SCOR with `RfrdDocAmt/RmtdAmt` (SEB requires it, error 32565/AM09), otherwise `Ustrd` |
| Plusgiro | `ClrSysMmbId` SESBA / 9960 | `Othr/Id` + `SchmeNm/Cd` **BBAN** | as Bankgiro |
| Bank account | `ClrSysMmbId` SESBA / clearing (4 digits) | `Othr/Id` per MIG Annex 5 (clearing + account; Handelsbanken and Danske 9180-9189 account only; Swedbank 8-series 5-digit clearing, zero-padded to 15) + `SchmeNm/Cd` **BBAN** | `Ustrd` |
| IBAN | BIC (from the bank, or derived from a Swedish IBAN) | `IBAN` | `Ustrd` |

What the payee accepts (flags on the bank account, `l10n_se_bank_account`): a payee that accepts
only messages gets `Ustrd` even when the bill has a valid OCR number; a payee that requires OCR
stops the file when the bill has none. A message longer than 140 characters stops the file (the
payment line would cut it).

Payment level: `SvcLvl/Prtry` **MPNS**, no `BtchBookg` (the bank batches per debtor account and
date), charge bearer `SHAR`. Creditor postal address only `Ctry` (SEB uses it for regulatory reporting, warning 32065 without it), no debtor address (SEB takes it from its register). Initiating party and debtor are identified by
the customer id with `SchmeNm/Cd` **BANK**. Only SEK.

Verified against the bank's samples and SEB's file test in the internet bank ("Swedish payments"); run your own files through the bank's test service before
going live.

## Bank profiles

- **SEB** — MIG pain.001.001.03 version 26.1
- Swedbank — planned

## Tests

`tests/test_pain_se.py`: a full payment order (Bankgiro with OCR and with free text, Plusgiro,
bank accounts at Swedbank, Handelsbanken and Danske Bank, IBAN) whose file is validated against
the pain.001.001.03 XSD and checked element by element; refused account numbers; the Swedish
translation. Account types and number rules are tested in `l10n_se_bank_account`.

## Upgrading from 19.0.1.0.x

The account type field moved to `l10n_se_bank_account`. Upgrading this module installs it
(`-u l10n_se_account_banking_pain`); the field, its column and all values, including types set
by hand, are kept (migration `19.0.1.1.0`).

> **Deploy the new code and run `-u l10n_se_account_banking_pain` in the same restart.** Between
> the two, the old module version is still installed but its Python code no longer defines the
> account type field: the stored bank-account views still show it and generating a payment
> order fails. Installing another module first (e.g. `l10n_se_payment_file_seb_csv`) does not
> upgrade this one.
