# Changelog

All notable changes to the modules in this repository are documented here,
one section per module. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use Odoo's
`<odoo-version>.<major>.<minor>.<patch>` scheme.

## account_statement_import_online_enable_banking

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
