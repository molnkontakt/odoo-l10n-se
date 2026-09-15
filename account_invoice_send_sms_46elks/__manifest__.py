{
    "name": "Skicka faktura som SMS via 46elks",
    "version": "19.0.1.0.0",
    "category": "Accounting",
    "summary": "Sändningssättet 'SMS via 46elks' i fakturans Skicka-dialog: betalinfo + portallänk som SMS",
    "description": """
Lägger till *SMS via 46elks* i fakturans Skicka-dialog (och som förval per kund). Texten byggs från en mall
i systemparametern sms_46elks.invoice_text (belopp, förfallodag, bankkonto, portallänk som öppnar fakturan
utan inloggning) och skickas till kundens telefon (phone_sanitized, E.164). Kostnad och 46elks-id loggas.
    """,
    "author": "Molnkontakt AB",
    "license": "LGPL-3",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "depends": ["account", "phone_validation"],
    "external_dependencies": {"python": ["requests"]},
    "data": ["data/ir_config_parameter.xml"],
    "installable": True,
}
