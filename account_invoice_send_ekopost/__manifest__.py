{
    "name": "Skicka faktura som brev via Ekopost",
    "version": "19.0.1.0.0",
    "category": "Accounting",
    "summary": "Sändningssättet 'Brev via Ekopost' i fakturans Skicka-dialog: PDF:en postas via Ekoposts API",
    "description": """
Lägger till *Brev via Ekopost* bredvid E-post i fakturans Skicka-dialog (och som förval per kund under
Fakturaskick). Odoo genererar faktura-PDF:en, modulen skapar en Ekopost-kampanj med ett kuvert per
faktura, adressen från kundens adressfält, och stänger kampanjen (utskrift + porto). Kuvert-/kampanj-id
loggas på fakturan. Auth och val i systemparametrarna ekopost.* (se README).
    """,
    "author": "Molnkontakt AB",
    "license": "LGPL-3",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "depends": ["account"],
    "external_dependencies": {"python": ["requests"]},
    "data": ["data/ir_config_parameter.xml"],
    "installable": True,
}
