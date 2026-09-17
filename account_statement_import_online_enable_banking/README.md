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

- Only booked transactions are imported; pending ones wait for the next pull.
- `unique_import_id` is the bank's `entry_reference`/`transaction_id` when
  present. Swedbank sends neither, so the id is a hash of the stable fields
  (date, amount, currency, remittance, counterparty, type) plus an occurrence
  counter for identical rows on the same day. Deterministic as long as pulls
  cover whole days, which the framework guarantees.
- Line text: the remittance information, prefixed with the bank's transaction
  type when it adds something (`Bankgiro inbetalning 1234567`, `Bg-bet. via
  internet Water Utility`). Swish lines end with `Swish +46…` so the Swish
  matching and automatic reconciliation of `account_statement_import_swedbank_csv`
  apply unchanged; the payer is looked up on `res.partner.phone_sanitized` here
  as well.
- The closing balance (`balance_end_real`) is set only when the pulled period
  includes today, because the balances endpoint knows only the current balance.
- The private key is stored on the provider (system administrators only,
  masked in the form). Anyone with database access can read it; treat the
  database as you would the key.

## Licence

AGPL-3.0, because the OCA base module it extends is AGPL-3.
