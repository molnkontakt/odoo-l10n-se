{
    "name": "Bank Reconciliation (OCA) – Mobile Layout",
    "version": "19.0.1.0.0",
    "category": "Accounting",
    "summary": "Makes the OCA bank reconciliation view readable on phones: stacked layout, wrapping buttons, card rows",
    "author": "Molnkontakt AB",
    "license": "LGPL-3",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "depends": ["account_reconcile_oca"],
    "assets": {
        "web.assets_backend": [
            "account_reconcile_oca_mobile/static/src/scss/reconcile_mobile.scss",
        ],
    },
    "installable": True,
    "application": False,
}
