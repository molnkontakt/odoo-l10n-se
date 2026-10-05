# Sweden - SIE 4 import and export

Moves the bookkeeping from Fortnox, Visma/Spiris and other Swedish programs into Odoo through
**SIE 4** files, checks the result against the files' closing balances, and writes Odoo's
bookkeeping as SIE 4 for the auditor or a tax return program. Follows SIE-Gruppen's *SIE
filformat* version 4B/4C. Depends only on `account`; the BAS chart of accounts (`l10n_se`) is
expected but not required.

Menu: *Accounting > Accounting > SIE*: **SIE Import**, **SIE Export** and **SIE Imports** (the
history). Only accounting administrators (`account.group_account_manager`) see them.

## Import

1. **Upload.** One or more `.se`/`.si`/`.sie` files, for example one per financial year
   (Fortnox: *Inställningar > Exportera SIE*, type 4; Visma/Spiris: *Export > SIE-fil*, type 4).
   Choose *Import the bookkeeping* or *Reconciliation only* (below).
2. **Analyse.** The preview shows, per file, the program, character set, SIE type, company name,
   organisation number and checksum; per financial year the period, accounts, vouchers, lines and
   series, and what happens to every voucher: imported, already in Odoo, dated outside the file's
   financial year (e.g. in `#RAR -1`: listed, never imported), without lines, not balanced, in a
   locked period; the accounts missing in Odoo and the type they would get; the dimensions and
   their analytic plans; and every problem with file name and line number. **Problems that stop
   the import** are listed in red and the import is refused until they are solved; warnings are
   listed in yellow. Files larger than 50 MB (system parameter `l10n_se_sie4.max_file_mb`) and
   lines longer than 10 000 characters are refused.
3. **Options.**
   - *Opening balance*: the first selected year's `#IB 0` as an entry on its first day
     (`SIE 2025 IB — Opening balance`), only into books without entries (posted or draft, the
     branches included); otherwise it is unticked and refused if ticked, since the balances would
     count twice. The amounts are rounded to the currency and must still add up to zero. Later
     years get their opening balance from the vouchers before them.
   - *Book opening balance differences*: when a file's opening balance differs from the previous
     year's closing balance (a program that moves the result to retained earnings in the opening
     balance), exactly the differences the preview showed become an entry on the first day of the
     year. Before booking it, Odoo's balances before the year must still agree with what the
     preview compared with (the previous year's `#UB`, or Odoo's balances at the preview);
     otherwise the import stops and lists the accounts. Differences that do not add up to zero
     (the year before has no year-end closing) stop the import.
   - *Order of the years*: years are imported in order. An opening balance already in the books
     dated after the start of a selected year (a later year imported first) stops the import.
   - *Vouchers*: every `#VER` becomes a journal entry (`entry`) with the voucher date, reference
     `SIE 2025 A12 — <voucher text>` (`SIE 2025 A1/2` when the series ends with a digit), line
     labels from the transaction text (or the voucher text), no taxes. *Post entries* (default
     on) posts them. A voucher number that occurs twice in a year stops the import.
   - *Years*: tick the years to import.
   - *Journals*: one journal (default: *SIE Import*, code `SIE`, created when needed) or a journal
     per series (choose per series; empty creates *SIE series &lt;series&gt;*). Whether a new
     journal hashes its entries is left to Odoo (OCA `account_journal_restrict_mode` makes it so);
     the preview says when the import cannot be undone because of a hash.
   - *Missing accounts*: create them (name from `#KONTO`; the type of the company's accounts with
     the same first three, then two digits, so it follows the installed chart; `#KTYP` and the BAS
     class otherwise) or stop the import. Archived accounts are reactivated when creating.
   - *Dimensions as analytics*: every SIE dimension (1 cost centre, 6 project, ...) becomes an
     analytic plan, its objects analytic accounts, and the lines get the analytic distribution of
     their object list.
   - *Leave out vouchers that do not balance*: import the rest; otherwise such a voucher stops
     the import.
4. **Import.** Up to 1 000 entries run at once; more run in the background (scheduled action *SIE
   import: run imports in the background*, batches of 100 for up to 60 seconds per run) and the
   import record shows the progress. Everything is checked once per run, before writing.
5. **Reconciliation.** When done, every account is compared, per financial year: balance sheet
   accounts with the closing balance `#UB` (Odoo: balance up to the last day of the year), result
   accounts with `#RES` (Odoo: the year's total). A year-end closing in the source program (e.g.
   8999/2099) is imported like any other voucher, so Odoo, which never closes years, still agrees.
   The report is green only when every year could be checked and nothing differs. Imports and
   reconciliations of **later** years already in Odoo are checked again, since an earlier year
   changes their balances, and shown in the report. The import record lists the deviations;
   *Deviations* opens all compared accounts (filter *Deviations only*, totals, group by year,
   export via the list's *Export*; texts that a spreadsheet would read as a formula get an
   apostrophe in front). *Check Again* repeats the comparison, e.g. after a correction.

The same voucher is never imported twice into a company: every entry carries a key (financial
year, series, number; for a voucher without a number a hash of its content and which of several
identical vouchers it is), and a database constraint backs it. A cancelled entry does not count:
its voucher can be imported again. Entries with the reference of the earlier import scripts
(`SIE 2025 A12 — ...`, series of letters only) are recognised too. The company is always taken
with its branches.

### Undo

*Undo Import* deletes the import's entries, and the accounts, analytic accounts and journals it
created when nothing refers to them any more (any field of any model, analytic distributions
included). It is refused, with the entries named, while any entry is in a locked period (lock
date, hard lock date), secured by a hash, kept by the restrictive audit trail, reversed, part of
a bank statement line, reconciled (partly or fully), or changed since the import (every entry
keeps a fingerprint of what it booked; posting an imported draft is not a change). Undo is all or
nothing: everything is checked in the same transaction as the deletion. More than 3 000 entries
are undone in the background, still as one transaction; if that does not finish within the
server's time limit for scheduled actions, nothing is deleted and after two attempts the undo is
marked failed.

### Reconciliation only

For a year that is booked again by hand in Odoo, with the file from the earlier program as the
check: nothing is created. The report compares Odoo's **posted** balances with the file, at the
end of the year (`#UB`/`#RES`) or *As of* a date within it: on a month end from the file's opening
balance plus its monthly balances `#PSALDO`, on any other day from the opening balance plus its
vouchers up to that date. The history keeps the reconciliation (state *Reconciliation*); *Check
Again* repeats it after corrections. Errors in the file only warn here.

## Export

*SIE Export*: from, to (default: the last financial year), type and options. The file is named
`<company name>_<year>.se` (`.si` for 4I).

| Type | Content |
|---|---|
| 4E (default) | `#KONTO`/`#KTYP`, `#IB`/`#UB` (balance sheet) and `#RES` (result) for the year and the year before, `#PSALDO` per month, `#DIM`/`#OBJEKT`, `#VER`/`#TRANS` |
| 4I | header and `#VER`/`#TRANS` only, for import into another program |
| 1 | chart of accounts and `#IB`/`#UB`/`#RES` |
| 2 | type 1 plus `#PSALDO` and `#OMFATTN` |
| 3 | type 2 plus `#OIB`/`#OUB` and `#PSALDO` per object |

- Header: `#FLAGGA 0`, `#PROGRAM "Odoo" 19.0`, `#FORMAT PC8`, `#GEN`, `#SIETYP`, `#FNR`, `#ORGNR`
  (company registry or VAT number), `#FNAMN`, `#ADRESS` (when the company has an address), `#RAR 0`
  and `-1`, `#KPTYP` (company setting, default `EUBAS97`, which every program reads; `BAS2025`,
  `BAS2026`, `NE2007`), `#VALUTA`.
- Posted entries only. Account names in Swedish when Swedish is installed.
- Vouchers: series = journal code, numbered 1, 2, ... per series in date order; text = the entry
  number and its reference; objects from the analytic distribution, one per dimension on the same
  `#TRANS`. A line split over several analytic accounts of a plan becomes one `#TRANS` per account
  by percentage; percentages of different plans are combined, so the line's amount is counted
  once and every object gets its share.
- Opening entries (the company's opening move and an SIE import's opening balance) on the first
  day count as the opening balance, not as vouchers or monthly movements.
- Odoo does not close financial years: the result of earlier years is still on the income and
  expense accounts. The file has it in the opening balance of *Account for earlier years' result*
  (default 2099), so the balance sheet balances; the summary says so, and explains BAS 2099, 2098
  and 2091 when more than one year is not closed.
- The previous year (`#RAR -1`) is proposed from the company's financial year settings and can be
  corrected (shortened or extended years); a period that is not a financial year is warned
  about.
- The company is exported with its branches (they share its books); the summary names them.
- Code page 437 (characters it lacks become `?`), CRLF, quotes escaped as `\"`. Optional
  `#KSUMMA` (CRC-32 as the specification describes; off by default).
- A journal filter limits the vouchers; the balances always include every journal.

## Known limitations

- **VAT**: SIE carries no VAT codes. Imported entries have no taxes, so Odoo's tax report cannot
  show the VAT of imported periods; use the source program's VAT reports for them.
- **No customer and supplier ledgers**: SIE 4 has no invoices or partners. Receivables and
  payables are imported as account balances without partner; open invoices must be entered (or
  opened) separately to be paid and reconciled in Odoo.
- Accounts are matched on the exact number. A chart with longer codes (e.g. `193000`) gets new
  four-digit accounts.
- Not imported: quantities, transaction dates that differ from the voucher date (the entry has the
  voucher date), `#PBUDGET`, `#OIB`/`#OUB`, `#SRU` (Odoo's Swedish chart has no SRU field, so the
  export writes none either), signatures. `#FLAGGA` is not set in the uploaded file.
- A sub-dimension (`#UNDERDIM`) becomes an analytic plan of its own; when a line has two objects
  of the same dimension only the first is used.
- The checksum follows the specification's text (labels and field contents, without separators,
  quotes and braces, as code page 437); a file whose checksum does not match only gives a warning.
- Years must be imported in order, and the opening balance only into books without entries. To
  add an earlier year to a company with later years, undo the later imports first.
- A background undo is one transaction: the server's time limit for scheduled actions
  (`limit_time_real_cron`) must allow it (about 6 ms per entry).

## How it works

- `lib/sie.py` is plain Python (no Odoo import): the tokenizer (quotes with `\"`, object lists,
  empty fields), encoding detection (code page 437 by default; UTF-8 with or without BOM and
  Windows-1252 recognised and reported), every record of the specification as dataclasses,
  checks with line numbers (unbalanced vouchers, `#RTRANS` not followed by its `#TRANS`, unknown
  records, truncated files, checksum) and the writer. Only `#TRANS` lines are booked: `#RTRANS`
  (an added line) is always followed by an identical `#TRANS`, `#BTRANS` is a removed line.
- `models/sie_analysis.py` works out everything an import would do; the preview shows it and every
  run (the import, each background run) works it out again before writing, so lock dates, the
  order of the years and duplicates are checked against the database as it is at that moment.
- `l10n_se.sie.import` keeps the files, options, entries, created accounts and the reconciliation
  lines (`l10n_se.sie.import.check`).

## Tests

- `tests/pytest/` (no Odoo): tokenizer, encodings, a Fortnox-like file with `#RTRANS`/`#BTRANS`,
  `#PSALDO`, `#PBUDGET`, `#SRU`, `#DIM`/`#OBJEKT` and two years, broken files with line numbers,
  checksum (against the specification's algorithm), writer and round trip. Run from the
  repository root:
  `python3 -m pytest -p no:cacheprovider --confcutdir=l10n_se_sie4/tests/pytest l10n_se_sie4/tests/pytest`
- `tests/test_sie_import.py`, `tests/test_sie_export.py` (Odoo, `--test-tags /l10n_se_sie4`,
  install together with `l10n_se`): preview, import, `#RTRANS`/`#BTRANS`, created accounts and
  their types, analytics, idempotency, two years, opening balance differences, lock dates, undo,
  background runs, access, reconciliation only (year end, month end, a day), export of every
  type, checksum, and a round trip into a new company with identical balances.
  `tests/test_sie_review.py` holds a regression test for every finding of the review before
  release (opening balance twice, order of the years, undo of reconciled/reversed/changed
  entries, duplicate numbers, rounding, branches, plans, limits, ...); the test of OCA
  `account_journal_restrict_mode` runs when that module is installed too. The tests create
  their own companies.
- The files in `tests/data` are synthetic: an invented company and organisation number.

## Disclaimer

Check the reconciliation report after every import and keep the SIE files. See the repository
README.
