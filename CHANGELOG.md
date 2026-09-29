# Changelog

All notable changes to the modules in this repository are documented here,
one section per module. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use Odoo's
`<odoo-version>.<major>.<minor>.<patch>` scheme.

## l10n_se_payment_file_seb_csv

### [19.0.1.1.1] — 2026-09-29

- SEB refused a file because *Fakturanummer* was filled for a Bankgiro payment ("not needed for bg
  and pg payments"). Without an OCR number, a Bankgiro or Plusgiro payment now sends the
  supplier's invoice number as a message (*Meddelande*); choosing *Invoice number* for one is
  blocked, and the file writer refuses it too. Bank account payments keep *Fakturanummer*.
- New on Bankgiro/Plusgiro accounts: *Payee accepts only messages*, next to *Payee requires OCR*
  (not both). Such a payee never gets an OCR or RF reference, even when the bill has a valid OCR
  number; choosing one is blocked.
- A message is at most 100 characters, the limit of SEB's payment form for a Bankgiro message.

### [19.0.1.1.0] — 2026-09-28

- *Avsändarens referens* (sender's reference) is only written for bank account payments
  (BBAN/IBAN): a test upload was refused because SEB does not accept it for Bankgiro and Plusgiro
  payments. The export line keeps it for the trace.
- Export wizard: opens as a wide dialog; blocked reasons and the warnings to acknowledge are listed
  above the bills, with *I have read the warnings* directly under them. The long *Why blocked* and
  *Warnings* columns, and the due date, amount due and clearing bank columns, are hidden by default
  (optional columns).
- The warning for an OCR number equal to the supplier's invoice number no longer says a wrong
  number is rejected: the bank checks the check digit (and for some payees the length), so a
  wrong number can go through and the supplier then cannot match the payment.

### [19.0.1.0.0] — 2026-09-27

- Initial release. Export vendor bills to SEB's CSV upload for domestic payments (the internet
  bank's *Domestic* template): Bankgiro, Plusgiro, bank account and Swedish IBAN, with OCR
  number, RF reference, invoice number or message.
- Wizard on the vendor bill list with per-bill checks (bank account, check digits, OCR, currency,
  amount, payment date) and warnings (open credit notes, partial amount, a date that is not a
  Swedish bank day, reference that fails the OCR check digit, OCR equal to the invoice number,
  bank account check digit not confirmed, bank not matching the clearing number); payment files
  with lines, the CSV as attachment and chatter on the file and the bills. No payment is
  registered; a bill is in at most one draft or exported payment file, and a done file holds it
  until the payment has reached the bill (the rest of a partial payment can then be exported).
- Default payment date: the last bank day on or before the due date.
- *Register Payment* is refused for a bill in a draft or exported file; a file flags bills that
  were paid or credited another way after export. The state and the values recorded on the lines
  can only be changed by the file's buttons.
- Journal setting *SEB CSV export* with the own SEB account check.
- The value formats SEB does not document are constants in one *VERIFY WITH A TEST UPLOAD*
  block of `lib/seb_csv.py`; not yet verified against SEB.

## l10n_se_bank_account

### [19.0.1.0.0] — 2026-09-27

- Initial release. Swedish account type on bank accounts (moved from
  `l10n_se_account_banking_pain`, which now depends on this module), now also recognising an IBAN
  without `base_iban`, a `BG`/`PG` prefix without a space and a separately stored clearing number.
- Bankgiro/Plusgiro check digit and length, OCR (Luhn, optional length digit), RF (ISO 11649) and
  IBAN validation; bank accounts written per clearing number range (MIG Annex 5) from a clearing
  number table; the number in payment files and a warning on the bank account form.
- Bank account check digits per Bankgirot's *Bankernas kontonummer* (mod 11 for type 1 and
  Handelsbanken, mod 10 for the other type 2 banks); Swedbank 8-series and PlusGirot, which have
  exceptions, and unlisted clearing numbers give a warning instead. An 8-series number without
  separators that could be another bank's account without its clearing number is refused.
  All-zero numbers are refused.
- Swedish bank days (weekends, public holidays, midsummer, Christmas and New Year's Eve).
- Pure-Python library `lib/se_bank.py` with pytest tests.

## l10n_se_account_banking_pain

### [19.0.1.1.0] — 2026-09-27

- Depends on `l10n_se_bank_account`; the account type field moved there (migration keeps the
  column and all values, including types set by hand).
- Bank accounts are written as MIG Annex 5 requires: Handelsbanken and Danske Bank 9180-9189
  without the clearing number, Swedbank 8-series with a 5-digit clearing number zero-padded to
  15 digits. Previously every digit of the stored number was sent.
- Generating the file refuses a Bankgiro/Plusgiro number with a wrong check digit or length and
  a bank account whose length or check digit does not fit its clearing number.
- Derived BIC for Swedish IBANs: bank code 915 is Skandiabanken, 957 Sparbanken Syd (957 was
  mapped to Skandiabanken's BIC).
- English source strings with a Swedish translation (`i18n/sv.po`).
- Upgrading: deploy the code and run `-u l10n_se_account_banking_pain` in the same restart.

### [19.0.1.0.1] — 2026-09-24

- SCOR (OCR) references carry `RfrdDocAmt/RmtdAmt`, which SEB requires (error 32565/AM09).
- The creditor's country is sent in `PstlAdr/Ctry` (SEB warning 32065, regulatory reporting).

### [19.0.1.0.0] — 2026-09-24

- Initial release: Swedish domestic payments in ISO 20022 pain.001.001.03 on top of OCA
  `account_banking_sepa_credit_transfer`. Bankgiro (SESBA 9900 + BGNR), Plusgiro (SESBA 9960 +
  BBAN), bank account (clearing + account, BBAN), IBAN, OCR as SCOR reference, service level
  MPNS, bank customer id with `SchmeNm` BANK; bank profile SEB (MIG pain.001.001.03 7.10). The
  tests validate the file against the XSD.

## account_statement_import_online_enable_banking

### [19.0.1.4.1] — 2026-09-28

- Swish payers on personal accounts are matched too: such accounts send no transaction type and
  no counterparty id, and the payer's number only starts the remittance text. Two partners with
  the number still means no match.

### [19.0.1.4.0] — 2026-09-28

- Balance check after every pull, also on days without transactions: Odoo's balance is compared
  with the bank's booked and available balances. A booked balance that lags same-day
  transactions is accepted only when the available balance is not below Odoo's, so duplicate
  imports still surface. One chatter warning per difference.
- The pull note counts only lines inside the period that are not yet imported, so neighbouring
  periods no longer report the same transactions twice.
- Scheduled pulls write in the company's language and show times in the time zone of the user
  who renews the consent; Swedish translation (`i18n/sv.po`).

### [19.0.1.3.0] — 2026-09-21

- A statement dated today keeps the bank's closing balance only when it agrees with the start
  balance plus the lines; otherwise the computed end is used and the difference is posted on the
  provider's chatter. Some banks deliver a booked transaction before their booked balance
  includes it, which left today's statement flagged as incomplete for good. Past statements keep
  the bank's figure.

### [19.0.1.2.1] — 2026-09-18

- A failed balance call (PSD2 daily limit of 4 unattended calls, 429) no longer
  discards the transactions already fetched; noted in *Last pull result*.

### [19.0.1.2.0] — 2026-09-18

- Every pull is recorded on the provider (*Last pull* / *Last pull result*: period and
  number of booked transactions the bank returned); the chatter gets a note when
  transactions came in or the pull failed.

### [19.0.1.1.0] — 2026-09-17

- Consent renewal warning: a daily cron schedules a to-do for the provider's
  renewal user (and notifies them in the chatter) *N* days before the bank
  consent expires (default 14); renewing closes the to-do.
- Swish: the `Swish +46…` suffix is not added when the bank's text already
  contains the number.

### [19.0.1.0.1] — 2026-09-17

- Account binding and own-account detection accept a domestic account number on
  the journal (Swedish `8305-5 …`): the IBAN's account part is that number
  zero-padded, so a digit-suffix match is used.

### [19.0.1.0.0] — 2026-09-17

- Initial release: Enable Banking as an `account_statement_import_online`
  provider. JWT (RS256) authentication, bank consent flow with callback
  controller, account selection by journal IBAN, paginated pull of booked
  transactions, closing balance when the period covers today, deterministic
  ids for banks that send no transaction reference, Swish payer matching.

## account_invoice_send_ekopost, account_invoice_send_sms_46elks, account_invoice_send_hand_delivered

### [19.0.1.0.0] — 2026-09-15

- Initial public release (extracted from an association-specific module).

## account_invoice_reminder_ekopost, account_invoice_reminder_sms_46elks

### [19.0.1.0.0] — 2026-09-15

- Initial public release.

## account_invoice_reminder

### [19.0.1.3.0] — 2026-09-15

- Levels: *Betalningsfrist (dagar)* → `date_due` ("betala senast") on the reminder, and
  *Kräver brev* (default channel letter). Fees from earlier sent reminders on the same
  invoices are carried over (`previous_fee_amount`) and listed separately. PDF and
  mail show the creditor's registration number and what each invoice concerns, to
  satisfy 5 § inkassolagen for self-issued debt collection demands.

### [19.0.1.2.0] — 2026-09-15

- **Fee is no longer a separate invoice.** The level has a fee amount and an income
  account; the fee is shown on the reminder (PDF/e-mail/SMS) and booked when paid
  through an auto-created manual reconciliation model (button in the bank
  reconciliation view). Migration copies product prices to `fee_amount`; existing
  reminders keep their fee invoice in `fee_move_id`.

### [19.0.1.1.1] — 2026-09-15

- Initial public release. Reminder levels per company, fee invoice, PDF report,
  e-mail/manual delivery (extensible), invoice list filter and action, review
  wizard. Template ships with `use_default_to=False` so `partner_to` is honoured
  even when the customer's e-mail is one of the system's own aliases.

## account_statement_import_swedbank_csv

### [19.0.1.4.1] — 2026-09-15

- Detect row order from the dates instead of assuming newest-first; balances are
  taken from the running-balance column of the oldest and newest rows. An
  oldest-first export previously produced mirrored balances.

### [19.0.1.4.0] — 2026-09-15

- Initial public release. Balance-based `unique_import_id`, Swish matching on
  `phone_sanitized`, optional automatic reconciliation (journal flag).

## account_statement_import_bankgirot_xlsx

### [19.0.1.5.1] — 2026-09-28

- Fixed: a reference that names an invoice exactly - its OCR number, its number as printed or the
  number's digits - is never matched to another invoice. When that invoice is no longer open
  (paid, credited, a file imported again) the detail shows it as *redan betald* or *krediterad*
  with its member, and nothing is proposed; before, the digit variants could end on an open
  invoice of the same year and a re-imported file set that member on the bank line.
- Fixed: the year-prefixed variants yielded bare numbers and the import stopped with *too many
  values to unpack*. The sequence of a year-prefixed reference is now everything between the year
  and the check digit (all of it when there is no valid check digit), with and without leading
  zeros; it is never cut further. Year-bound variants only match SIE entries of that year too.

### [19.0.1.5.0] — 2026-09-24

- ISO 20022 **camt.054** (Bank-to-Customer Debit/Credit Notification) as a deposit-detail source
  next to Bankgirot's XLSX: one deposit per credit entry, one detail row per payer (OCR/invoice
  number, payer Bankgiro), multi-invoice payments split per invoice. The bank line falls back to
  date + amount + unreconciled when it is not labelled as a Bankgiro deposit.

### [19.0.1.4.0] — 2026-09-15

- Matched invoices are always added as a reconciliation proposal on the bank
  line (OCA `account_reconcile_oca`); the journal option only decides whether
  the proposal is confirmed automatically.

### [19.0.1.3.1] — 2026-09-15

- Match the invoice number literally as printed ("INV/2026/0011") before trying digit
  variants, and restrict year-prefixed variants to invoices of that year. A reference
  "INV/2026/0011" previously matched a 2024 entry via "001" → "/0001".

### [19.0.1.3.0] — 2026-09-15

- Initial public release. Parses Bankgirot's text-formatted amounts, matches on
  OCR, customer name in reference/message, invoice number and amount; optional
  automatic reconciliation of the lump sum (journal flag).

## l10n_se_ocr

### [19.0.1.0.0] — 2026-09-15

- Initial public release.
