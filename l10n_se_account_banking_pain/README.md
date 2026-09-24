# Sweden - ISO 20022 supplier payments (pain.001)

Swedish domestic payments in the pain.001 payment file of OCA
[`account_banking_sepa_credit_transfer`](https://github.com/OCA/bank-payment): pay vendor bills
to **Bankgiro**, **Plusgiro**, a Swedish **bank account** (clearing + account number) or an
**IBAN**, with **OCR** as structured reference. OCA's generator is built for SEPA (IBAN, EUR);
this module adds the Swedish rules of the bank's implementation guide (MIG).

## Usage

1. Payment mode (Invoicing → Configuration → Payment Modes) with payment method *SEPA Credit
   Transfer* (pain.001.001.03), set **Svensk bankprofil** (bank profile) and the **customer id**
   from your file-transfer agreement with the bank.
2. Vendor bank accounts get a **Svensk kontotyp** (Swedish account type), guessed from the number:
   prefix `BG`/`PG`, `123-4567` / `1234-5678` (Bankgiro), `123456-7` (Plusgiro), a valid IBAN,
   otherwise clearing + account. Correct it on the account when the guess is wrong.
3. Create a payment order from the vendor bills as usual and generate the file. A bill whose
   *Payment Reference* is numeric and Luhn-valid is paid with that OCR number (SCOR); any other
   reference goes as free text (max 140 characters).

## What goes in the file (SEB profile, MIG pain.001.001.03 section 7.10)

| | Creditor agent | Creditor account | Remittance |
|---|---|---|---|
| Bankgiro | `ClrSysMmbId` SESBA / 9900 | `Othr/Id` + `SchmeNm/Prtry` **BGNR** | OCR as `Strd/CdtrRefInf` SCOR with `RfrdDocAmt/RmtdAmt` (SEB requires it, error 32565/AM09), otherwise `Ustrd` |
| Plusgiro | `ClrSysMmbId` SESBA / 9960 | `Othr/Id` + `SchmeNm/Cd` **BBAN** | as Bankgiro |
| Bank account | `ClrSysMmbId` SESBA / clearing (4 digits) | `Othr/Id` (clearing + account) + `SchmeNm/Cd` **BBAN** | `Ustrd` |
| IBAN | BIC (from the bank, or derived from a Swedish IBAN) | `IBAN` | `Ustrd` |

Payment level: `SvcLvl/Prtry` **MPNS**, no `BtchBookg` (the bank batches per debtor account and
date), charge bearer `SHAR`. Creditor postal address only `Ctry` (SEB uses it for regulatory reporting, warning 32065 without it), no debtor address (SEB takes it from its register). Initiating party and debtor are identified by
the customer id with `SchmeNm/Cd` **BANK**. Only SEK.

Verified against the bank's samples and SEB's file test in the internet bank ("Swedish payments"); run your own files through the bank's test service before
going live.

## Bank profiles

- **SEB** — MIG pain.001.001.03 version 26.1
- Swedbank — planned

## Tests

`tests/test_pain_se.py`: account type guessing, and a full payment order (Bankgiro with OCR and
with free text, Plusgiro, bank account, IBAN) whose file is validated against the pain.001.001.03
XSD and checked element by element.
