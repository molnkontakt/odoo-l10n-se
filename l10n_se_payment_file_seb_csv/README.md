# l10n_se_payment_file_seb_csv

Pay Swedish vendor bills through SEB's internet bank by uploading a CSV file in SEB's
**Domestic** template ("sverige inrikes"): Bankgiro, Plusgiro, bank account (BBAN) and Swedish
IBAN, with OCR number, RF reference, invoice number or message.

The module registers **no payment** in Odoo. An exported bill stays unpaid until the bank
statement line of the payment is imported and reconciled against it, exactly as for a payment
made by hand in the internet bank. The export only records what was sent and keeps the bill out
of later exports, so it cannot be paid twice by mistake.

Depends on `account` and [`l10n_se_bank_account`](../l10n_se_bank_account/) (account types,
check digits and the account number as payment files write it). No OCA modules are needed.

> [!WARNING]
> **SEB publishes no specification of the CSV upload.** The only documented facts are those of
> the template itself: UTF-8 without BOM, LF line endings, comma separated, no quoting, a header
> row with 37 columns and `sverige inrikes` as payment type. How amounts, dates, account numbers
> and format codes are written is **inferred** from SEB's ISO 20022 guide (MIG pain.001.001.03)
> and has **not been verified**. Do not rely on the export before a test upload (see the
> checklist below) has confirmed every value in the *VERIFY* block of `lib/seb_csv.py`.

## How it works

1. **Setup, once.** In *Accounting > Configuration > Journals*, open the bank journal of your SEB
   account and tick **SEB CSV export** (tab *Journal Entries*, next to the bank account number).
   The journal's bank account must be an SEB account in SEK, stored as an SEB IBAN or as clearing
   number + account number; the form shows the resulting *From account (SEB CSV)*. On supplier
   bank accounts, check the *Swedish account type*, mark the accounts you pay to as trusted
   (*Send Money*) and tick *Payee requires OCR* for Bankgiro numbers that only accept OCR.
2. **Select bills.** In the vendor bill list, the filter **To pay via SEB** shows posted, unpaid
   SEK bills that are not in a payment file. Select bills and choose *Actions > Export to SEB
   (CSV)*.
3. **Review.** The wizard checks every bill and lists, above the bills, why a bill cannot be paid
   (*Blocked*) and what needs attention (*Warning*); the status column marks the bill. Blocked bills
   are left out; warnings must be acknowledged with *I have read the warnings* under the list. The bank account, amount, payment date and the reference the payee sees can be
   changed per bill. Payment date: the last Swedish bank day on or before the due date (today at
   the earliest), or one date for all. A bill you untick stays unticked when you change other
   values; ticking *I have read the warnings* is taken back when the warnings change.
4. **Generate file.** A payment file (batch) `SEBYYYY-NNNN` is created with the CSV attached.
   Its chatter and every bill's chatter record what was sent; the bill form shows *Exported to
   SEB payment file*. *Save as draft* creates the batch without a file; generating it later
   checks every bill again.
5. **In the internet bank.** Download the file, upload it in SEB's internet bank, compare the
   preview with the number of payments and the total, and sign.
6. **Something went wrong.** If the bank rejects a payment or a payment is not signed, cancel that
   payment (the *Cancel payment* button on its line) or the whole file (*Cancel file*). The bills
   are released and can be exported again. Only cancel what was not signed, otherwise a bill can
   be paid twice.
7. **Afterwards.** Reconcile the bank statement line against the bill as usual. *Mark as done* on
   the file when its payments have gone out (manual for now). *Register Payment* is refused on a
   bill while its file is draft or exported (it could still be signed); mark the file as done
   first if SEB has paid, or cancel the bill's line if it will not be signed. If a bill in an
   exported file is paid or credited another way, the file shows a red warning and marks the
   line: remove that payment in the internet bank before signing and cancel the line here.

Menu: *Accounting > Vendors > SEB payment files*.

## Checks

A bill is **blocked** (never written to the file) when:

- it is not a posted vendor bill (credit notes are never exported: negative amounts are not
  allowed for giro payments), or belongs to another company than the SEB journal;
- its currency is not SEK (the Domestic template only pays SEK within Sweden);
- nothing is left to pay, or it is already in a draft or exported payment file, or in a done
  file whose payment has not yet reached the bill (its amount due has not dropped by the amount
  paid: after a partial payment, the rest can be exported once the payment is reconciled);
- it has no bank account, the account belongs to your own company, is not trusted, has no known
  Swedish account type, fails its check digit or length (Bankgiro, Plusgiro, the banks' clearing
  number and check digit rules), is only zeros, or is a foreign IBAN;
- the payee requires OCR and the bill has no valid OCR number, or the chosen reference is invalid
  (OCR check digit, OCR to a bank account, RF check digits, invoice number over 35 or message over
  140 characters);
- the amount is zero, negative or more than the amount due, or the payment date is in the past.

**Warnings** (to acknowledge): open credit notes of the supplier (they are not netted: reconcile
them against the bill first, then export the rest), a partial amount, a payment date that is not
a Swedish bank day (weekend, public holiday, midsummer, Christmas or New Year's Eve), a numeric
payment reference that fails the OCR check digit (sent as invoice number or message instead), an
OCR number equal to the supplier's invoice number, a bank account of another partner, a bank
account whose check digit could not be confirmed (Swedbank 8-series and PlusGirot have accounts
that fail it; an unlisted clearing number cannot be checked), a bank on the account record that
is not the bank of its clearing number, the supplier's only trusted account used because the
bill has none, and Odoo's possible-duplicate warning.

A bill in a draft or exported file cannot be reset to draft. At most one payment line per bill
can be draft or exported; a partial unique index in the database enforces this even for two
exports running at the same time. The state of a file and the values recorded on its lines can
only be changed by its buttons, not by a direct write (RPC, import).

## Columns

| Column | Value |
|---|---|
| Betaltyp | `sverige inrikes` (the template's example) |
| Från konto | the journal's SEB account: clearing + account, 11 digits (`FROM_ACCOUNT_FORM`) |
| Till konto | Bankgiro/Plusgiro digits without hyphen; bank account per MIG Annex 5 (`BBAN_FORM`); compact IBAN |
| Till konto - format | `BG`, `PG`, `BBAN` or `IBAN` (`ACCOUNT_FORMAT_CODES`) |
| Mottagarens namn | bank account holder, else the supplier; commas, semicolons and quotes removed, cut at 35 |
| Belopp | `1234.50` (`DECIMAL_SEPARATOR`) |
| Betaldatum | `YYYY-MM-DD` (`DATE_FORMAT`) |
| OCR | the bill's payment reference when it is a valid OCR number and the payee is Bankgiro/Plusgiro |
| RF | the payment reference when it is a valid RF creditor reference |
| Fakturanummer | otherwise the bill's vendor reference (max 35) |
| Meddelande | otherwise a message (max 140): the vendor reference, payment reference or bill number |
| Egen anteckning | the bill number |
| Standard eller Express | `Standard` (`PRIORITY_STANDARD`) |
| Avsändarens referens | batch name + line number, e.g. `SEB2026-0001-001`: unique, for matching the bank line later |
| all other columns | empty (they are for foreign payments, funds and payments on behalf of others) |

Exactly one of OCR, RF, Fakturanummer and Meddelande is filled, so OCR and an invoice number are
never sent together. Free texts have commas, semicolons, quotes and line breaks replaced by
spaces (names: removed), so no field ever needs quoting, and a leading `=`, `+`, `-` or `@` is
dropped so that a spreadsheet opening the file does not read a formula.

Each payment line keeps the values written to the file (to account, format, payee name,
reference, sender's reference, own note, amount due at export) for reconciling the bank
statement line later.

## Values to verify with a test upload

All format choices SEB does not document are constants in one block of `lib/seb_csv.py`, marked
*VERIFY WITH A TEST UPLOAD*. Change them there; no other code hard-codes them.

| Constant | Default | Question |
|---|---|---|
| `DECIMAL_SEPARATOR`, `QUOTED_COLUMNS` | `.`, none | `1.25`, or `"1,25"` in quotes? |
| `DATE_FORMAT` | `%Y-%m-%d` | `2026-10-01`, or `20261001`? |
| `ACCOUNT_FORMAT_CODES` | `BG`, `PG`, `BBAN`, `IBAN` | spelling and case |
| `GIRO_WITH_HYPHEN` | `False` | `55512347` or `5551-2347`? |
| `BBAN_FORM` | `annex5` | MIG Annex 5 (Handelsbanken without clearing, Swedbank 8-series padded) or clearing + account for every bank? |
| `FROM_ACCOUNT_FORM` | `bban` | own account as 11-digit BBAN or IBAN? |
| `PRIORITY_STANDARD` | `Standard` | spelling; is an empty value accepted? |
| `REFERENCE_WITHOUT_OCR` | `invoice_number` | does the payee see *Fakturanummer*, or should it be a message? |
| `PAYEE_NAME_MAX`, `INVOICE_NUMBER_MAX`, `MESSAGE_MAX`, `SENDER_REFERENCE_MAX`, `OWN_NOTE_MAX` | 35, 35, 140, 35, 35 | length limits |

### Test-upload checklist

Every test row pays the company's **own** Bankgiro or another **own** account, 1.00-1.25 SEK, so
an accidental signature only moves a krona between your own accounts. Upload one file at a time,
read the preview or the error message, **do not sign**, and delete the payment from the list of
upcoming payments afterwards.

The wizard cannot make these files: it refuses payments to your own accounts and invalid OCR
numbers on purpose. Write them by hand from `tests/data/Domestic.csv`, or with the library in a
Python shell (the numbers below are placeholders for your own accounts):

```python
from datetime import date
from odoo.addons.l10n_se_payment_file_seb_csv.lib import seb_csv

payment = seb_csv.Payment(
    from_account="<own SEB account, 11 digits>",
    to_account="<own Bankgiro, digits only>",
    account_type="bankgiro",
    payee_name="<own company name>",
    amount=1.00,
    payment_date=date(2026, 10, 1),
    reference_type=seb_csv.REFERENCE_INVOICE,
    reference="TEST-1",
)
open("/tmp/seb-test-1.csv", "wb").write(seb_csv.build_file([payment]))
```

(Outside Odoo, load `lib/seb_csv.py` directly with `importlib`, as the pytest file does.)

- [ ] One row: own Bankgiro without hyphen, `1.00`, `YYYY-MM-DD`, own account as 11-digit BBAN,
  `Standard`, invoice number only.
- [ ] Amount `1.25` against `"1,25"` in quotes.
- [ ] Own account as IBAN.
- [ ] Bankgiro with hyphen.
- [ ] Three rows: OCR only (valid check digit), invoice number only, message only.
- [ ] A row with an OCR number with a wrong check digit (must be rejected).
- [ ] A bank account row to another own account (BBAN, 11 digits); if possible also a
  Handelsbanken and a Swedbank 8-series account (Annex 5 against clearing + account), and a
  Nordea PlusGirot account as bank account (95xx / 9960 + number).
- [ ] What the payee of a **bank account** payment sees: one row with a 35-character invoice
  number and one with a 30-character message, then read the receiving account's statement.
  Account deposits have historically shown only about 12 characters; if that holds, a shorter
  limit per account type is needed.
- [ ] An RF reference to a Bankgiro and to a bank account (accepted? shown to the payee?).
- [ ] A payment date far ahead (e.g. 13 months): how far ahead does SEB accept?
- [ ] `Express`, an empty value and `standard` in the priority column; `bg` in lower case.
- [ ] Note where *Egen anteckning* and *Avsändarens referens* show up, and whether the upload
  needs an agreement or customer id.
- [ ] Optionally sign one 1-krona payment to an own account and look at the imported bank
  statement line: which text (reference, sender's reference, payee name) arrives, and whether a
  file with two rows gives one or two statement lines.
- [ ] Set the constants accordingly and record the result.

## Access

- *Invoicing* (`account.group_account_invoice`) and above: export, generate, cancel, mark as
  done.
- *Show Accounting Features - Readonly* (`account.group_account_readonly`): read payment files.
- Deleting a (draft or cancelled) payment file: *Administrator* (`account.group_account_manager`).
- Payment files and their lines follow the company rules (multi-company).

Signing in the internet bank is the second control.

## Not included (yet)

- Matching the bank statement line to the payment line automatically; the stored references are
  there for it.
- Netting credit notes in the file, foreign-currency or foreign payments, Express payments.
- Purchase receipts (`in_receipt`) are not exported, only vendor bills.

## Technical notes

- A supplier bank account (or bill) that was ever used in a payment file, even a cancelled one,
  cannot be deleted: the lines keep the reference. Archive the account instead.
- `lib/seb_csv.py` is pure Python (no Odoo import): the template header, the *VERIFY* constants,
  cleaning, field formats and `build_file(payments)`. It can be reused, e.g. by an OCA payment
  order glue module.
- A byte-identical copy of SEB's template is in `tests/data/Domestic.csv`; the tests compare the
  header and the example row byte for byte.
- pytest (from the repository root):
  `python -m pytest -p no:cacheprovider --confcutdir=l10n_se_payment_file_seb_csv/tests/pytest l10n_se_payment_file_seb_csv/tests/pytest`
- Odoo tests: `--test-tags /l10n_se_payment_file_seb_csv` (plain `TransactionCase` with a SEK
  company on Odoo's generic chart of accounts).
