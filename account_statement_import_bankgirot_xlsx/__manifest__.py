{
    "name": "Bankgirot Insättningsuppgifter Import",
    "version": "19.0.1.3.0",
    "category": "Accounting",
    "summary": "Berika Swedbank-bankrader med detaljerade insättningsuppgifter från Bankgirot (XLSX)",
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
