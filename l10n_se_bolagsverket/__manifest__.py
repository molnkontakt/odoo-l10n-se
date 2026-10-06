{
    "name": "Sweden - Bolagsverket",
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations",
    "summary": "Fetch company name and address from the organisation number and watch customers and suppliers for "
    "bankruptcy, liquidation, reorganisation and deregistration (Bolagsverket's free API)",
    "description": """
Uses Bolagsverket's free *Värdefulla datamängder* (high-value datasets) API.

* **Fetch from Bolagsverket** on a company contact: registered name and postal address from the organisation
  number (Company ID, or a Swedish VAT number), plus company form, registration date, industry codes (SNI),
  registered business and advertising block.
* **Status watch**: a scheduled job checks customers and suppliers with an organisation number when they are added
  and then weekly. Bankruptcy, liquidation, reorganisation or deregistration gives a red banner on the contact and on
  its invoices and bills, a note on the contact and a to-do for a chosen user.
* **Name check**: when the name in Odoo is not one of the names registered for the number (e.g. a supplier created
  from an invoice with the buyer's number, or an invoice pretending to come from a known company), the contact and
  its bills get a warning.

Credentials (client id and secret, test or production) are free from Bolagsverket. An unused API account is closed
after six months; the job keeps it alive with a daily ping when there is nothing to check.
    """,
    "author": "Molnkontakt AB",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "license": "LGPL-3",
    "depends": ["account"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "data/ir_cron.xml",
        "views/res_partner_views.xml",
        "views/account_move_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": False,
}
