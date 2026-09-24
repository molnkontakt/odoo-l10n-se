{
    "name": "Sweden - ISO 20022 supplier payments (pain.001)",
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations",
    "summary": "Swedish domestic payments (Bankgiro, Plusgiro, bank account, OCR) in OCA pain.001 payment files",
    "description": """
Extends OCA account_banking_sepa_credit_transfer with the Swedish domestic rules of the bank's
ISO 20022 implementation guide. Activated per payment mode by choosing a bank profile.

* Bankgiro: CdtrAgt ClrSysMmbId SESBA/9900, CdtrAcct Othr + SchmeNm/Prtry BGNR
* Plusgiro: CdtrAgt ClrSysMmbId SESBA/9960, CdtrAcct Othr + SchmeNm/Cd BBAN
* Bank account (clearing + account): CdtrAcct Othr + SchmeNm/Cd BBAN
* OCR (Luhn-valid numeric payment reference on the vendor bill) as structured SCOR reference
* Service level MPNS, initiating party / debtor identified by the bank's customer id (SchmeNm/Cd BANK)

Bank profiles: SEB (MIG pain.001.001.03, 7.10.x). Swedbank to follow.
    """,
    "author": "Molnkontakt AB",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "license": "LGPL-3",
    "depends": ["account_banking_sepa_credit_transfer"],
    "data": [
        "views/res_partner_bank_views.xml",
        "views/account_payment_mode_views.xml",
    ],
    "installable": True,
}
