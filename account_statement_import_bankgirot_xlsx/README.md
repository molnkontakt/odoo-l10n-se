# Bank statement import: Bankgiro deposit details (Bankgirot XLSX or camt.054)

Bankgiro deposits arrive on the bank statement as one lump sum per day, without
payer or reference. Bankgirot's *Insättningsuppgifter → Detaljer* export (XLSX)
has the individual payments, and so does the bank's ISO 20022 **camt.054**
notification (Bank-to-Customer Debit/Credit Notification, `.001.02` and later). This
wizard reads either file, matches each payment to an open customer invoice and
annotates, or reconciles, the bank line.

## camt.054

One notification per credit entry (`Ntry`, `CRDT`, booked); one detail row per
`TxDtls` (payer name, payer Bankgiro from `DbtrAcct` with `BGNR`, OCR from
`CdtrRefInf/Ref` or invoice number from `RfrdDocInf/Nb`). A payment that covers
several invoices with their own amounts (`RfrdDocAmt/RmtdAmt`) becomes one row per
invoice. The receiving Bankgiro comes from `CdtrAcct` (`BGNR`) or `NtryRef` (`BG…`).
Tested against SEB's sample `camt.054_SE_CRED_BGC.xml`.

The bank line is found by date and amount: first a line labelled *Bankgiro
inbetalning* (Swedbank), otherwise any unreconciled line with that date and
amount (other banks and Enable Banking feeds label it differently). When the
receiving Bankgiro is registered as a bank account on a company, only that
company's lines are considered; among several lines, the one whose text holds
the receiving Bankgiro number wins. When that company is not selected in the
company switcher, no line is picked and the result says which company to
select.

## Matching

Per detail row, in order: OCR reference (`payment_reference`), customer name in
the reference or message (normalised), invoice number, amount. The result is
posted as a note on the bank statement line ("split" with payer, amount and the
matched invoice or the reason no match was found).

Only invoices and SIE-imported sales entries of the bank line's company (and
its branches), and payer bank accounts shared or of that company, are
searched, whichever companies are selected in the company switcher. Companies number their invoices each on
their own, so the first invoice of a year has the same number, and the same
OCR number, in every company.

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
- The bank line is found by date, total and receiving Bankgiro number (see
  *camt.054* above); it decides the company.
- Python dependency: `openpyxl`.
