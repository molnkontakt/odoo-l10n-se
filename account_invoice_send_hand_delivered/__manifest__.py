{
    "name": "Faktura lämnad för hand",
    "version": "19.0.1.0.0",
    "category": "Accounting",
    "summary": "Sändningssättet 'Lämnad för hand' (lagd i brevlådan) i Skicka-dialogen + åtgärd för flera fakturor",
    "description": """
För fakturor som lämnas fysiskt (brevlåda, över disk): kryssrutan *Lämnad för hand* i Skicka-dialogen
markerar fakturan som skickad och skriver en notering med datum och användare, utan att skicka något.
Åtgärden *Markera som lämnad för hand* i fakturalistan gör samma sak för flera markerade fakturor.
    """,
    "author": "Molnkontakt AB",
    "license": "LGPL-3",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "depends": ["account"],
    "data": ["data/actions.xml"],
    "installable": True,
}
