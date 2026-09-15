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

## Automatic reconciliation

Journal option *Reconcile Bankgirot automatically on import*: when every detail
row matched its own open invoice and the residual amounts add up to the bank
line, the line is reconciled against all of them at once. Requires OCA
`account_reconcile_oca`; otherwise the split note is left for manual
reconciliation. Always starts from a clean line (`clean_reconcile()`), so a
half-edited reconciliation view does not interfere.

## Notes

- Bankgirot formats amounts as text with non-breaking spaces (`'2 000,00'`);
  the parser handles that.
- The bank line is found by date, total and receiving Bankgiro number on lines
  whose text contains "Bankgiro inbetalning".
- Python dependency: `openpyxl`.
