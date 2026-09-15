# Bank statement import: Swedbank CSV

Adds Swedbank's CSV statement export to the OCA import wizard
(`account_statement_import_file`, repository *bank-statement-import*).

## Format

Swedbank's export (`Radnr,Clnr,Kontonr,Produkt,Valuta,Bokfdag,Transdag,Valutadag,
Referens,Text,Belopp,Saldo`, cp1252, newest row first) matches neither the generic
OCA CSV parser nor any standard (CAMT.053, OFX, MT940). The parser recognises the
header and column layout and produces statement lines with the running balance.

## Behaviour

- **Dedupe**: `unique_import_id` is built from account, date, amount and balance
  after the transaction, so re-importing an overlapping export skips rows already
  present and keeps the opening balance correct.
- **Swish**: the payer's mobile number in the text (`Swish +46…`) is looked up
  against `res.partner.phone_sanitized` (E.164); when exactly one customer matches,
  the line gets that partner.
- **Automatic Swish reconciliation** (journal option *Reconcile Swish automatically
  on import*): when the partner has exactly one open customer invoice with the same
  residual amount, the line is reconciled against it. Requires OCA
  `account_reconcile_oca`; silently skipped otherwise.

## Setup

On the bank journal set *Bank feeds* to *Import (OCA)*. Import with
*Accounting → Bank → Import statement*.
