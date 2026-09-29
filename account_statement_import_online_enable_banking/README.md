# Online bank statements: Enable Banking

Pulls bank transactions through [Enable Banking](https://enablebanking.com)
(PSD2 account information, most Nordic and European banks — in Sweden e.g.
Swedbank, SEB, Handelsbanken, Nordea, Länsförsäkringar, Danske; business and
personal accounts) into the OCA online statement framework
(`account_statement_import_online`, repository *bank-statement-import*).

## What you get, and what you don't

The feed carries exactly what the bank shows on the account: date, amount,
the bank's transaction type, the remittance text and, for Swish, the payer's
mobile number. A Swedish *Bankgiro inbetalning* is still the day's lump sum
with the Bankgiro number as the only reference — payer and OCR live in
Bankgirot's *Insättningsuppgifter*, not in the account feed, so keep
`account_statement_import_bankgirot_xlsx` for matching invoices. This module
replaces the manual CSV download and adds a daily closing balance, nothing
more.

## Setup

1. Register an application in Enable Banking's control panel. Generate the RSA
   key outside the browser (`openssl req -x509 -newkey rsa:4096 -nodes -days 3650
   -keyout private.key -out certificate.pem`), import the certificate and note
   the *Application ID*. Add `https://<your odoo>/enable_banking/callback` as a
   redirect URL.
2. In Odoo, on the bank journal (with its IBAN set as bank account): *Bank
   feeds → Online (OCA)*, then on the provider choose service *Enable
   Banking*, enter the application id, paste the private key, and set the bank
   name exactly as Enable Banking lists it (*Check bank name* verifies it and
   shows the supported account-holder types), country and account-holder type.
3. *Authorise with the bank* opens the bank's consent screen (BankID in
   Sweden). Back in Odoo the session is bound to the journal; the account is
   selected by the journal's IBAN (or the only account when the journal has
   none). Consents run for at most 180 days. A daily cron creates a to-do for the
   *renewal user* (default: whoever created the provider) *Warn before consent
   expires* days ahead (default 14) and notifies them in the chatter; renewing
   closes the to-do.
4. Pull manually with *Pull Online Bank Statement* or let the scheduled pull
   run.

## Behaviour

- Every pull writes *Last pull* and *Last pull result* on the provider (period, how
  many booked transactions the bank returned and how many of them are new). The bank
  filters on its own date, so the same transactions often come back for neighbouring
  periods (a weekend Swish booked on Monday); only lines dated inside the period and
  not yet imported count as new. The chatter gets a note only when something new was
  imported (posted after the import, with the lines actually created) or the pull
  failed, so a quiet day leaves no noise.
- Only booked transactions are imported; pending ones wait for the next pull.
- `unique_import_id` is the bank's `entry_reference`/`transaction_id` when
  present. Swedbank sends neither, so the id is a hash of the stable fields
  (date, amount, currency, remittance, counterparty, type) plus an occurrence
  counter for identical rows on the same day. Deterministic as long as pulls
  cover whole days, which the framework guarantees.
- Line text: the remittance information, prefixed with the bank's transaction
  type when it adds something (`Bankgiro inbetalning 1234567`, `Bg-bet. via
  internet Water Utility`). Swish lines end with `Swish +46…`, as in Swedbank's
  CSV export; the payer is looked up on `res.partner.phone_sanitized`, the same
  rule as `account_statement_import_swedbank_csv` (one partner with the number, or
  none).
- **Swish reconciliation.** With `account_statement_import_swedbank_csv` (and
  `account_reconcile_oca`) installed and *Stäm av Swish automatiskt vid import* on the
  journal, every pull reconciles the new Swish payments it imported the way the CSV
  import does: a positive line with a partner and exactly one open customer invoice of
  the line's company whose amount due equals the payment. Two matching invoices, an
  invoice in another company, a line from an earlier pull or a journal without the flag
  are left for the reconciliation view. Each line is tried in its own savepoint; a
  failure is logged and does not undo the import. The count goes into the pull result
  and the chatter note. Without the Swedbank module the pull imports as before.
- The closing balance (`balance_end_real`) is set only when the pulled period
  includes today, because the balances endpoint knows only the current balance.
  A failed balance call does not lose the transactions; the result field says so.
- **Balance check.** Some banks (Swedbank) deliver same-day transactions as booked
  while their *booked* balance still excludes them until the nightly run. An open
  statement therefore keeps Odoo's computed closing balance, and after every pull —
  also on days without new lines — the provider compares Odoo with the bank:
  consistent when Odoo equals the booked or the available balance, or when the booked
  balance only lacks some of today's lines and the available balance (if the bank
  sends one) is not below Odoo. Anything else is a real difference: one chatter
  warning per difference, and the result in *Balance check* on the provider.
- Messages from scheduled pulls follow the company's language (Swedish included).
- **PSD2 rate limit**: banks allow 4 unattended calls per day, account and service
  (transactions, balances). Keep the scheduled pull at once per day; each manual
  pull eats into the same budget and the bank answers 429 beyond it. The chatter then
  says the daily limit is reached and that the connection does not need to be renewed.
- **Errors** are fixed, translated texts by kind (daily limit, temporarily unavailable,
  refused, rejected, network error, unreadable answer) with the HTTP status and Enable
  Banking's error code. A scheduled pull notes the failure once in the chatter and the
  result field; a manual pull shows it as a warning (nothing is saved). The bank's raw
  answer, which can contain account numbers, only goes to the server log.
- **A failed scheduled pull is retried** when the failure goes away by itself or with a new
  consent (daily limit, 5xx, network, an unreadable answer, 401/403, an expired or missing
  consent): the next scheduled pull starts at the period that failed (the OCA base alone would
  skip it), for at most 14 days. It still stops at its first failure, so a dead connection
  costs one call per run. A failure that would repeat for the same data (a malformed
  transaction, a page loop), or one older than 14 days, skips only that period - the note
  says so - and the next run continues with the period after it (the OCA base alone would
  skip the rest of the window too); pull the skipped period manually once the cause is fixed.
- An expired or missing consent is a failure, not an empty pull: one note per run, and the
  periods are pulled once the bank is authorised again.
- **A session the bank has ended** (401/403 with `EXPIRED_SESSION`, `CLOSED_SESSION`,
  `INVALID_SESSION` and the like, long before the consent's end date) disconnects every
  provider on that session at once and gives the renewal user a to-do right away, instead of
  failing with only a note until the consent would have expired. It is detected on the
  scheduled pull. A 401/403 without such a code (a bad application key) does not count.
- **A journal also fed by the Swedbank CSV import** (e.g. before the switch): the two channels
  give one transaction different ids, so nothing is left out. New lines with the day and amount
  of a line from the file are counted in the pull result and in the chatter, and the renewal
  user gets a to-do to check them. Delete a duplicate on the file's side: a later pull can bring
  the Enable Banking line back. Do not import files for days Enable Banking pulls.
- **One consent, several accounts.** SEB allows one session per person: authorising one
  account ends the session of another. Select every account at the bank that Odoo pulls in the
  bank's consent screen; the authorisation then also connects the other providers for the same
  bank and the same Enable Banking application whose journal account is in the consent (same
  currency) - in any company, as the consent does - and closes their renewal to-dos.
- **Robust to bank variations**: the sign comes from the credit/debit indicator even
  when a bank also signs the amount; the transaction type and the remittance
  information are read whether the bank sends an object/list or a plain string. A pull
  stops after 100 pages or when a page repeats. An account is only connected when its
  currency is the journal's.
- The private key is stored on the provider (system administrators only,
  masked in the form). Anyone with database access can read it; treat the
  database as you would the key.

## Licence

AGPL-3.0, because the OCA base module it extends is AGPL-3.
