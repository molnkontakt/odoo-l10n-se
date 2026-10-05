{
    "name": "Sweden - SIE 4 import and export",
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations",
    "summary": "Read SIE 4 files from Fortnox, Visma/Spiris and others into the books (or only "
    "reconcile against them), and export the books as SIE 4 for the auditor",
    "description": """
SIE 4 (SIE-Gruppen's file format, version 4B/4C) for Swedish bookkeeping.

* *SIE Import*: one or more SIE files, a preview (program, company and organisation number,
  financial years, vouchers per series, dimensions, missing accounts, vouchers that do not
  balance, vouchers already imported, lock dates), then the opening balance and the vouchers as
  journal entries; missing accounts created from #KONTO/#KTYP and the BAS class; dimensions as
  analytic plans. Every import is kept with its files and entries, can be undone while none of
  them is locked, and ends with a check of every account against the files' closing balances
  (#UB) and results (#RES).
* *Reconciliation only*: compare the posted books with a file without creating anything, at the
  end of the year or on a date (monthly balances #PSALDO or the vouchers).
* *SIE Export*: a financial year as type 4E (or 1, 2, 3, 4I): chart of accounts, opening and
  closing balances of this and the previous year, results, monthly balances, objects and
  vouchers; code page 437, optional #KSUMMA.
* The parser and writer are plain Python (``lib/sie.py``) with their own tests.
    """,
    "author": "Molnkontakt AB",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "license": "LGPL-3",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "security/security.xml",
        "data/ir_cron.xml",
        "views/l10n_se_sie_import_views.xml",
        "wizard/sie_import_wizard_views.xml",
        "wizard/sie_export_wizard_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
}
