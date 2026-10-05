# Changelog

All notable changes to the modules in this repository are documented here,
one section per module. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use Odoo's
`<odoo-version>.<major>.<minor>.<patch>` scheme.

## l10n_se_sie4

### [19.0.1.0.0] — 2026-10-05

- Initial release. *SIE Import*: one or more SIE 4 files (Fortnox, Visma/Spiris and others), a
  preview (program, character set, company and organisation number, financial years, vouchers per
  series, dimensions, missing accounts, vouchers that do not balance, vouchers already in Odoo,
  lock dates, every problem with file and line number), then the first year's opening balance and
  the vouchers as journal entries, posted or as drafts, in one journal or a journal per series.
  Missing accounts are created from `#KONTO`/`#KTYP` with the type of their neighbours in the
  chart; dimensions become analytic plans and analytic distributions. Only `#TRANS` lines are
  booked (`#RTRANS` is followed by an identical `#TRANS`, `#BTRANS` is a removed line).
- A key per voucher (financial year, series, number) and a database constraint keep a voucher from
  being imported twice; references of earlier import scripts are recognised. Import into a locked
  period is refused. More than 1 000 entries are imported in the background.
- Import history with the files, entries, created accounts and a reconciliation of every account
  against the files: balance sheet accounts with `#UB`, result accounts with `#RES`. *Undo Import*
  while no entry is locked.
- *Reconciliation only*: compare the posted books with a file without creating anything, at the
  end of the year or on a date (`#PSALDO` on a month end, the vouchers on any day).
- *SIE Export*: type 4E (default), 4I, 1, 2 or 3; balances for the year and the year before,
  monthly balances, objects from the analytic distribution, vouchers per journal; earlier years'
  unclosed result in the opening balance of 2099; code page 437, CRLF, optional `#KSUMMA`.
- `lib/sie.py`: a pure-Python SIE 4 reader and writer (tokenizer, encoding detection, all records,
  checks with line numbers, CRC-32 checksum) with pytest tests. Swedish translation.
- Review before release: the opening balance only goes into books without entries and years are
  imported in order (no double opening balance); the opening balance differences entry books
  exactly what the preview showed and stops when the books before the year disagree; undo is all
  or nothing and refused for reconciled, reversed, bank or changed entries (fingerprint), and
  only removes accounts, analytic accounts and journals nothing refers to; a voucher number twice
  in a year stops the import, identical vouchers without number are all imported; vouchers
  outside the file's year are shown; the report is green only when every year was checked, and
  later years are checked again; amounts are rounded to the currency and checked before an entry
  is created; file size and line length limits, one analysis per run; the stored preview is
  sanitized; branches are part of the company; the export counts a line split over several plans
  once, takes the previous year from the settings (editable) and explains unclosed years; ASCII
  digits only; formula-like texts neutralised; unambiguous references; cancelled entries can be
  imported again; record rules on year and series lines; uploads move to the import record and
  abandoned ones are removed; journals created under OCA `account_journal_restrict_mode`.

## l10n_se_compliance_calendar

### [19.0.1.0.0] — 2026-10-01

- Initial release. Statutory dates for Swedish companies and associations as all-day events in
  Calendar, computed in the module (`rules.py`, no Odoo import, no network) from the company form,
  VAT period, VAT base class, EU trade, EC sales list, employer, F-tax and the financial year:
  VAT returns (month, quarter, financial year), EC sales list, employer declaration and payment,
  F-tax, income tax return (INK2, INK4, INK1 with NE), and for limited companies the annual
  general meeting and the annual report to Bolagsverket.
- Tax dates on a Saturday, Sunday, public holiday, Midsummer Eve, Christmas Eve or New Year's Eve
  move to the next weekday; public holidays are computed in the module.
- Custom rules per company or for all: a fixed day every year, or days/weeks/months before or after
  an anchor date entered per year (e.g. the annual meeting).
- Nightly scheduled action and *Update Now*: a stable key per rule and period, so a second run
  updates instead of duplicating; dates no longer produced are removed, only the module's own
  events are touched, earlier years are kept. The only attendee is a contact per company that can
  never have an e-mail address; no mail is sent. Optional default reminder (off), calendar filters
  for chosen groups, a *Dates* list with dates only and upcoming first. Swedish translation.

## account_reconcile_oca_mobile

### [19.0.1.0.0] — 2026-09-29

- Initial release. Styles for the OCA bank reconciliation view on screens narrower than 768 px:
  statement lines stacked above the form, wrapping status bar buttons, reconciliation lines and the
  matching list as cards. Styling only; the desktop layout is unchanged.

## mail_server_rate_limit

### [19.0.1.0.0] — 2026-09-29

- Initial release. A rate limit on an outgoing mail server: at most N messages per M minutes
  (sliding window, each recipient address counts), 0 = no limit, not used for personal servers.
  An SMTP provider refused a batch of invoices with "451 Too many mails" because Odoo sends the
  whole queue at once.
- Mail through a limited server is only sent by the *Mail: Email Queue Manager* cron. What does
  not fit the window keeps state *Outgoing* with a scheduled date for when there is room, and the
  cron is triggered at each of those dates; sending outside the cron (right after a post, *Send
  Now*, a template with `force_send`) leaves the mail in the queue and wakes the cron. A mail with
  more recipients than the limit is split, as Odoo splits one for a personal mail server; the parts
  share one message, and deleting one of them (auto delete once sent) never deletes the others.
- A temporary answer (4xx, e.g. `451 4.7.1 Too many mails`) puts the mail back in the queue for
  the recipients it has not reached, their notifications back to *Ready* and the reached ones to
  *Sent*, so nobody gets a second copy; the rest of that batch waits for a full window. After the
  configured number of retries the mail fails as before, without the recipients it reached, so
  *Retry* by hand (which gives it its retries back) does not send them a second copy either.
- Settings on the mail server form (*Rate Limit* tab) with the number used in the current window;
  retries on the e-mail's *Advanced* tab. Swedish translation.

## l10n_se_payment_file_seb_csv

### [19.0.1.2.0] — 2026-09-29

- *Payee requires OCR* and *Payee accepts only messages* moved to `l10n_se_bank_account`, so the
  pain.001 module follows them too. Update both modules together; the values are kept.

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

### [19.0.1.1.0] — 2026-09-29

- *Payee requires OCR* and *Payee accepts only messages* on Bankgiro/Plusgiro accounts (moved
  here from `l10n_se_payment_file_seb_csv`, values kept), with the rule that not both are set.

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

### [19.0.1.2.0] — 2026-09-29

- Follows what the payee accepts: a payee that accepts only messages never gets an OCR
  reference; a payee that requires OCR stops the file when the bill has no valid OCR number.
- A message longer than 140 characters stops the file instead of being cut by the payment line.

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

### [19.0.1.8.0] — 2026-09-29

- Swish payments are reconciled automatically after a pull, as the Swedbank CSV import does, when
  `account_statement_import_swedbank_csv` (with `account_reconcile_oca`) is installed and the
  journal has *Stäm av Swish automatiskt vid import* on: a new line with a partner (found by the
  payer's number) and exactly one open customer invoice of the line's company with that amount
  due. Before, this only ran for CSV imports, so a journal switched to Enable Banking got no
  automatic Swish reconciliation. Only the lines the pull imported are considered; each is tried
  in its own savepoint, so a failure is logged and never undoes the import. Without the Swedbank
  module nothing changes.
- The chatter note on new booked transactions is posted once the lines are imported (it counts
  the lines actually created) and says how many Swish payments were reconciled; the pull result
  says so too.

### [19.0.1.7.0] — 2026-09-29

- A journal also fed by the Swedbank CSV import: new lines of a pull (in its period, not imported
  yet) with the booking day and signed amount of a line from the file are counted in the pull
  result, noted in the chatter and given one to-do for the renewal user, so they can be checked.
  They are imported, not left out - the channels give one transaction different ids, and two real
  payments of the same amount on one day (or a payment and its refund) cannot be told from one
  payment seen twice; a lost payment would not be noticed, a duplicate is. With the value date as
  line date, a line is compared four days either way.

### [19.0.1.6.0] — 2026-09-29

- A session the bank has ended (401/403 with a session code such as `EXPIRED_SESSION`) disconnects
  every provider on that session and gives the renewal user a to-do at once; before, the pulls
  failed until the consent's end date. A 401/403 without such a code is not treated as that.
- One consent for several accounts at the same bank connects the other providers whose journal
  account is in it (same currency). SEB allows one session per person, so a separate
  authorisation per account ended the previous one.

### [19.0.1.5.0] — 2026-09-29

- Fixed: a debit whose amount the bank also sent as negative became a credit; the sign now
  comes from the credit/debit indicator whenever the bank sends one.
- Fixed: a bank that sends `bank_transaction_code` as a string (not an object) stopped the whole
  pull; a string `remittance_information` was read letter by letter. Both are read either way.
  The id of already imported lines is unchanged.
- Errors are fixed, translated texts by kind (daily limit - with "the connection does not need
  to be renewed" -, temporarily unavailable, refused, rejected, network error, unreadable
  answer) plus the HTTP status and error code, shown as a warning (plain `UserError`); the raw
  answer, which can hold account numbers, only goes to the server log. A scheduled pull notes
  the failure once in the chatter and the result field (the OCA base's own note, with the
  exception text, is replaced for this provider). An Enable Banking error in the bank's
  callback is posted in the chatter instead of an error page.
- Fixed: after a failed scheduled pull the OCA base moved on and never pulled the failed period
  again. For failures that go away by themselves or with a new consent (daily limit, 5xx,
  network, unreadable answer, 401/403, expired or missing consent) the next scheduled pull now
  starts at the failed period, for at most 14 days; the periods before it stay imported, and a
  pull still stops at its first failure. Other failures skip only the failed period (the note
  says so) and the next run continues right after it.
- An expired or missing consent is a failure instead of an empty pull: one note per run instead
  of one per period, and the periods are pulled after the new authorisation.
- A pull stops after 100 pages or when a page repeats, instead of looping.
- An account in another currency than the journal's is not connected.

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

### [19.0.1.5.1] — 2026-10-05

- The check that a reminder level's days are 0 or more is declared with `models.Constraint`. It was a `_sql_constraints` list, which Odoo 19 ignores with a warning, so the database never had the check.

### [19.0.1.5.0] — 2026-09-29

- New hook `account.reminder._email_values(attachments)`: the values the reminder e-mail gets on
  top of the template (by default the invoices' PDFs). They are applied after the template's
  `partner_to`, so a module can replace the recipients with
  `{"recipient_ids": [Command.set(partner_ids)]}`. No change in behaviour.
- The e-mail is still sent with `force_send`, through `mail.mail.send()`: at once on an ordinary
  outgoing server, and held in the mail queue by an outgoing-server rate limit that defers mail
  sent outside the queue cron (the cron is triggered, so it still goes out, paced). The reminder
  is marked sent either way; a later delivery failure shows on the e-mail (*Settings → Technical
  → Emails*), not on the reminder.
- New hook `account.reminder._check_send()`: why a reminder cannot be sent on its channel (no
  channel method; for e-mail no template or no address), or an empty string. `action_send` asks
  every reminder of the run before sending any and raises one error listing them all. Before, an
  error on a later reminder rolled back the whole run after earlier SMS or letters had already
  gone out, and running it again sent those a second time. A module adds a channel's own
  requirements by overriding the hook; the checks in `_send_<channel>` stay as a last guard.
- Tests for the hooks and the default recipient. The e-mail tests use their own company and assert
  on the created e-mail with `mail.mail.send` mocked, so they do not depend on the database's
  outgoing mail servers.

### [19.0.1.4.0] — 2026-09-29

- Fixed: reminders and reminder levels had no company rule, so another company's reminder was
  listed and opening the list ended in an access error on its invoices. Both are now shown only
  for the selected companies.

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

### [19.0.1.6.0] — 2026-09-29

- New rows with the day and signed amount of a line Enable Banking (or another channel) brought
  into the journal are reported in a notification, so they can be checked. They are imported, not
  left out: the channels give one transaction different ids, and two real payments of the same
  amount on one day cannot be told from one payment seen twice. When that provider dates its lines
  by value date, a row is compared four days either way.
- The order of the file (newest or oldest first) is read from the balance column, also within one
  day. When every pair of rows cancels out (+100, -100, +100) the balances fit both orders; the
  lines an earlier import stored under the ids those rows share then decide, so a payment imported
  from an export made during the day keeps its id and a new one of the same amount is not skipped
  in its place. The dates decide only when nothing else does. Start and end balance come from the
  nearest rows that have a balance.

### [19.0.1.5.0] — 2026-09-29

- Fixed: rows that share an id - +100, -100, +100 on one day (same amount and balance after), or
  two identical payments on one day without balances - lost all but the first as "already
  imported". The first keeps its id, the next ones get -2, -3; importing an export again now
  brings in a row that was dropped before.
- A UTF-8 export (with or without byte order mark) keeps its letters; Swedbank's cp1252 export is
  read as before.
- Tests.

### [19.0.1.4.1] — 2026-09-15

- Detect row order from the dates instead of assuming newest-first; balances are
  taken from the running-balance column of the oldest and newest rows. An
  oldest-first export previously produced mirrored balances.

### [19.0.1.4.0] — 2026-09-15

- Initial public release. Balance-based `unique_import_id`, Swish matching on
  `phone_sanitized`, optional automatic reconciliation (journal flag).

## account_statement_import_bankgirot_xlsx

### [19.0.1.5.3] — 2026-09-29

- Fixed: with several companies selected, a payment could be matched to another company's
  invoice. Companies number their invoices each on their own, so the first invoice of a year has
  the same number and OCR number in each. Invoices, SIE sales entries, the *redan betald* /
  *krediterad* lookup and payer Bankgiro accounts are now searched in the bank line's company (and
  its branches) only.
- When the receiving Bankgiro of a deposit is registered as a bank account on a company, only that
  company's bank lines are considered; before, a same-day line with the same amount in another
  company could be picked. That company is found whichever companies are selected, and when it is
  not selected in the company switcher no line is picked: the result says which company to select.

### [19.0.1.5.2] — 2026-09-29

- Fixed: camt.054.001.08 and later nest the status (`<Sts><Cd>`); a pending entry was read as a
  booked deposit.
- camt.054 with whitespace or a byte order mark before `<?xml` is read (it failed).
- Bankgirot XLSX: a date stored as a real Excel date, and a reference stored as a number, are read
  (the date was missed, the reference came out as "203100425.0").
- Tests for the XLSX and camt.054 parsers.

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
