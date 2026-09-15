# Bank statement import: Bankgirot deposit details (XLSX)

Bankgiro deposits arrive on the bank statement as one lump sum per day, without
payer or reference. Bankgirot's *Insättningsuppgifter → Detaljer* export (XLSX)
has the individual payments. This wizard reads the file, matches each payment to
an open customer invoice and annotates, or reconciles, the bank line.

## Matching

Per detail row, in order: OCR reference (`payment_reference`), customer name in
the reference or message (normalised), invoice number, amount. The result is
posted as a note on the bank statement line ("split" with payer, amount and the
matched invoice or the reason no match was found).

## Reconciliation proposal

With OCA `account_reconcile_oca` installed, the matched invoices are always
added as a *proposal* on the bank line, so opening it in the reconciliation
view only requires confirming (or fixing the unmatched rows). Always starts
from a clean line (`clean_reconcile()`), so a half-edited view does not
interfere.

Journal option *Reconcile Bankgirot automatically on import*: when every detail
row matched its own open invoice and the residual amounts add up to the bank
line, the proposal is confirmed immediately.

## Notes

- Bankgirot formats amounts as text with non-breaking spaces (`'2 000,00'`);
  the parser handles that.
- The bank line is found by date, total and receiving Bankgiro number on lines
  whose text contains "Bankgiro inbetalning".
- Python dependency: `openpyxl`.
