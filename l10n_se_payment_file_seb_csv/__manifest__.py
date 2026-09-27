{
    "name": "Sweden - SEB payment file (CSV)",
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations",
    "summary": "Export vendor bills to SEB's CSV upload for domestic payments (Bankgiro, "
    "Plusgiro, bank account, IBAN, OCR) without registering payments",
    "description": """
Pay Swedish vendor bills through SEB's internet bank by uploading a CSV file in SEB's
"Domestic" template.

* Action *Export to SEB (CSV)* on the vendor bill list: every selected bill is checked (bank
  account, Bankgiro/Plusgiro check digits, OCR, currency, amount, payment date, open credit
  notes) and the reason is shown for each bill that cannot be paid
* An export batch per file with its lines, the generated CSV as attachment and chatter; a bill is
  in at most one active batch, so it cannot be exported twice until the batch is cancelled
* No payment is registered: the bill stays unpaid until the bank statement line is reconciled.
  Each line stores the references written to the file for that reconciliation
* The paying account is a bank journal flagged for SEB CSV export

SEB does not publish a specification of the CSV format. The value formats that are not
documented are kept in one block of constants in ``lib/seb_csv.py`` and must be verified with a
test upload; see the README.
    """,
    "author": "Molnkontakt AB",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "license": "LGPL-3",
    "depends": ["account", "l10n_se_bank_account"],
    "data": [
        "security/ir.model.access.csv",
        "security/security.xml",
        "data/ir_sequence.xml",
        "views/l10n_se_payment_export_views.xml",
        "views/account_journal_views.xml",
        "views/res_partner_bank_views.xml",
        "views/account_move_views.xml",
        "wizard/payment_export_wizard_views.xml",
    ],
    "installable": True,
}
