{
    "name": "Betalningspåminnelser (nivåer, avgift, historik)",
    "version": "19.0.1.3.0",
    "category": "Accounting",
    "summary": "Påminnelsenivåer per bolag med dagar efter förfall, lagstadgad påminnelseavgift som egen faktura, PDF och e-post, historik per kund",
    "description": """
Odoo Community saknar påminnelser (Follow-up är Enterprise). Den här modulen ger:

* Påminnelsenivåer per bolag: namn, dagar efter förfallodatum, avgiftsprodukt (t.ex. 60 kr
  påminnelseavgift / 180 kr inkassokrav enligt lag 1981:739), text och mailmall.
* En påminnelse per kund och nivå som samlar alla förfallna fakturor, skapar avgiftsfakturan
  (bokförd, kopplad till påminnelsen), renderar en PDF och mailar kunden med PDF:en och
  fakturorna bifogade. Allt loggas på fakturorna och i påminnelsehistoriken.
* Fakturalistan: filtret "Påminnelse att skicka" och åtgärden "Skicka betalningspåminnelse"
  (öppnar en granskningsdialog innan något går ut). Meny Kunder → Betalningspåminnelser.
    """,
    "author": "Molnkontakt AB",
    "license": "LGPL-3",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "report/reminder_report.xml",
        "data/mail_template.xml",
        "views/reminder_level_views.xml",
        "views/reminder_views.xml",
        "views/account_move_views.xml",
        "wizard/reminder_send_wizard_views.xml",
        "data/actions.xml",
    ],
    "installable": True,
    "application": False,
}
