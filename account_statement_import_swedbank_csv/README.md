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
  present and keeps the opening balance correct. Rows that share it (+100, -100, +100 on
  one day; identical payments without balances) get -2, -3 in date order.
- **Next to Enable Banking**: the channels give one transaction different ids, so nothing
  is left out; a notification counts new rows with the day and amount of a line another
  channel brought into the journal, to be checked and the duplicates deleted.
- **Order**: newest or oldest first is read from the balance column, also within one day.
  When every pair of rows cancels out (+100, -100, +100), what an earlier import stored under
  the ids those rows share decides; the dates only when nothing else does. Start and end
  balance come from the nearest rows that have a balance.
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
