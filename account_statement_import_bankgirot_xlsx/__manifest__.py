{
    "name": "Bankgirot Insättningsuppgifter Import",
    "version": "19.0.1.5.0",
    "category": "Accounting",
    "summary": "Berika bankrader med insättningsuppgifter per betalare: Bankgirot (XLSX) eller bankens camt.054 (ISO 20022)",
    "depends": ["account"],
    "external_dependencies": {"python": ["openpyxl"]},
    "author": "Molnkontakt AB",
    "license": "LGPL-3",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "data": [
        "security/ir.model.access.csv",
        "wizards/bankgirot_import_wizard_views.xml",
        "views/account_journal.xml",
    ],
    "installable": True,
}
