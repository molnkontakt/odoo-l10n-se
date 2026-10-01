{
    "name": "Swedish Compliance Calendar",
    "version": "19.0.1.0.0",
    "category": "Productivity/Calendar",
    "summary": "Statutory dates for Swedish companies and associations as all-day events in "
    "Calendar: VAT, employer declaration, F-tax, income tax return, AGM, annual report, "
    "plus custom fixed and relative dates",
    "description": """
An annual cycle of statutory dates for Swedish companies and associations in Odoo's Calendar.

* The dates are computed in the module from the law and the company's settings: company form,
  VAT period, turnover class, EU trade, EC sales list, employer, F-tax and financial year.
  No third-party feed and no network access.
* Dates on a weekend, public holiday, Midsummer Eve, Christmas Eve or New Year's Eve move to the
  next weekday where Skatteverket says so.
* Custom rules: a fixed day every year, or N days/weeks/months before or after an anchor date
  entered per year (e.g. the annual meeting).
* A nightly job and an *Update Now* button create, update and remove all-day events with a
  stable key; the only attendee is a contact per company that can never have an e-mail address.
    """,
    "author": "Molnkontakt AB",
    "website": "https://github.com/molnkontakt/odoo-l10n-se",
    "license": "LGPL-3",
    "depends": ["account", "calendar"],
    "data": [
        "security/ir.model.access.csv",
        "security/security.xml",
        "data/ir_cron.xml",
        "views/calendar_event_views.xml",
        "views/compliance_custom_rule_views.xml",
        "views/compliance_anchor_views.xml",
        "views/res_company_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
