{
    "name": "Sweden - Bank accounts, Bankgiro, Plusgiro, OCR",
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations",
    "summary": "Swedish account types and check digits for payment files: Bankgiro, Plusgiro, "
    "bank account (clearing number rules), IBAN, OCR and RF references",
    "description": """
Shared Swedish account-number rules for payment-file modules.

* Swedish account type on bank accounts (Bankgiro, Plusgiro, bank account, IBAN), guessed from the
  number and editable
* Bankgiro and Plusgiro check digits (mod 10), OCR references (Luhn, optional length digit),
  RF creditor references (ISO 11649), IBAN check digits
* Bank accounts written the way the banks' ISO 20022 guides require (SEB MIG Annex 5): per
  clearing number range, e.g. Handelsbanken without clearing number, Swedbank 8-series with a
  5-digit clearing number zero-padded to 15 digits
* A pure-Python library (``lib/se_bank.py``) that payment-file writers can use without the ORM
    """,
    "author": "Molnkontakt AB",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "license": "LGPL-3",
    "depends": ["account"],
    "data": [
        "views/res_partner_bank_views.xml",
        "views/account_move_views.xml",
    ],
    "installable": True,
}
