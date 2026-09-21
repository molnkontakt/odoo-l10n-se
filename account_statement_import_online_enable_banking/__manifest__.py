{
    "name": "Online Bank Statements: Enable Banking",
    "version": "19.0.1.3.0",
    "category": "Accounting",
    "summary": "Pull bank transactions through Enable Banking (PSD2 account information) into the OCA online statement framework",
    "depends": ["account_statement_import_online", "mail"],
    "external_dependencies": {"python": ["cryptography"]},
    "author": "Molnkontakt AB",
    # AGPL because the OCA base module it extends is AGPL-3.
    "license": "AGPL-3",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "data": [
        "data/ir_cron.xml",
        "views/online_bank_statement_provider.xml",
    ],
    "installable": True,
}
