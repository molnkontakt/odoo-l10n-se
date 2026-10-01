# Swedish Compliance Calendar

An annual cycle (*årshjul*) of statutory dates for Swedish companies and associations, as all-day
events in Odoo's **Calendar**. The dates are computed in the module from the law and the
company's settings. There is no third-party feed and no network access. Depends on `account`
(for the financial year) and `calendar`.

## What it shows

| Date | Who | Rule | Moved to next weekday |
|---|---|---|---|
| VAT return, month, VAT base up to SEK 40 million | VAT period *Month* | 12th of the second month after the period; 17th in January and August (26 kap. 26 § SFL) | yes |
| VAT return, month, over SEK 40 million | VAT period *Month* | 26th of the month after; 27 December for November (26 kap. 30 § SFL) | yes |
| VAT return, quarter | VAT period *Quarter* | 12th of the second month after the quarter; 17 August (26 kap. 26 § SFL) | yes |
| VAT return, financial year | VAT period *Financial year* | see below (26 kap. 33–33 b §§ SFL) | yes |
| EC sales list | EU trade + *EC Sales List* | 25th of the month after the month/quarter, electronic filing (35 kap. 9 § SFL) | no |
| Employer declaration (AGI) | *Employer*, up to SEK 40 million | 12th of the month after the salary month; 17th in January and August; also the payment date | yes |
| Employer declaration (AGI) | *Employer*, over SEK 40 million | declaration the 26th of the month after (27 December); separate payment date the 12th, 17 January | yes |
| Preliminary tax (F-skatt) | *Pays F-tax* | every month the 12th; 17 January; 17 August only up to SEK 40 million (62 kap. 3 § SFL) | yes |
| Income tax return | AB, economic association (INK2), HB (INK4) | by end month of the financial year: Jan–Apr 1 Dec, May–Jun 15 Jan, Jul–Aug 1 Apr, Sep–Dec 1 Aug (digital filing) | yes |
| Income tax return | Sole trader (INK1 with NE) | 2 May the year after | yes |
| Annual general meeting | AB | six months after the end of the financial year (7 kap. 10 § ABL) | no |
| Annual report to Bolagsverket | AB | seven months after the end of the financial year (8 kap. 3 § ÅRL) | no |

Annual VAT return (financial year as VAT period, VAT base up to SEK 1 million):

| Company form | Without EU trade | With EU trade |
|---|---|---|
| AB, economic association | by end month: Jan–Apr 12 Dec, May–Jun 17 Jan, Jul–Aug 12 Apr, Sep–Dec 17 Aug (digital) | 26th of the second month after the year (27 December) |
| Sole trader | 12 May the year after | 26 February the year after (calendar year) |
| Trading partnership | 26th of the second month after the year | same |
| Non-profit / joint property association | 26th of the second month after the year (legal person without an income tax return) | same |

**Moved to the next weekday.** Skatteverket: a date for declaring and paying VAT and employer
contributions that falls on a Saturday, Sunday, public holiday, Midsummer Eve, Christmas Eve or
New Year's Eve moves to the next weekday. The module applies this to VAT, the employer
declaration, F-tax and the income tax return (Skatteverket shows e.g. 2 August 2027 for
1 August). The EC sales list, the annual general meeting and the annual report keep the
nominal date: shifting is not confirmed for them in the sources used, and the earlier date is
never late. Public holidays are computed in the module (Easter, Ascension Day, Whitsun, Midsummer,
All Saints' Day and the fixed ones); the event description says when a date was moved.

**Not covered.** Extensions (*anstånd*, *byråanstånd*) and decisions for the individual company;
paper filing (earlier dates); INK3 for non-profit and joint property associations; statutory
dates for associations' meetings and annual reports (use custom rules); excise duties, ROT/RUT,
OSS. Preliminary tax is shown every month of the year, regardless of a broken financial year or a
seasonal decision.

## Configuration

*Calendar → Compliance Calendar → Settings* (administrators) opens the settings of the current
company:

- **Use Compliance Calendar** (off by default), company form (default AB), VAT period (default not
  registered), VAT base per year, EU trade, EC sales list, employer (default no), F-tax (default
  no). The financial year is read from Accounting (*fiscal year last day/month*).
- **Dates to Show**: every type can be switched off.
- **Default Reminder**: optional, empty by default.
- **Show for Groups**: users in these groups get a selected calendar filter on the company's
  calendar contact, so the dates show in their calendar.
- **Update Now** runs the update at once; the scheduled action *Compliance Calendar: update dates*
  runs nightly.

### Custom rules and anchor dates

*Calendar → Compliance Calendar → Custom Rules*, for one company or all of them:

- **Fixed day every year**: month and day; a day after the end of the month means its last day
  (day 31 in February = 28/29 February). Example: *Year-end accounts* 15 February; *Motions*
  February, day 31 (= the end of February).
- **Relative to an anchor date**: N days, weeks or months before or after every anchor date with a
  given name. Anchor dates (*Anchor Dates* menu) have a name, a date and optionally a company,
  one per name and year. Example: anchor *Annual meeting* 2027-04-24; *Accounts to the auditor*
  6 weeks before; *Notice of the meeting* 14 days before.
- **On a Weekend or Holiday**: keep the date, previous weekday (the safe choice for a deadline) or
  next weekday.

## How it works

- `rules.py` is plain Python (no Odoo import) and returns `(key, date, title, description, legal
  basis)` for a profile and a date interval.
- The update creates all-day `calendar.event` records for the current year and the next two, with
  a stable key per rule and period (`vat:2026-q2`, `employer:2026-03`, `income_tax:fy2026-12-31`,
  `custom:<id>:<year>`, ...) in its own field. A second run updates instead of duplicating;
  an event whose key is no longer produced is removed. Only events with this module's key and
  company are touched, and events dated before 1 January of the current year are kept as history.
- The only attendee is a contact per company, *Compliance calendar – &lt;company&gt;* (*Årshjul –
  …* in Swedish). A constraint forbids an e-mail address on it. Events are written with
  `no_mail_to_attendees` and `dont_notify`, without organizer, as *Only internal users* and *Free*.
- *Compliance Calendar → Dates* lists dates only, upcoming first; the standard list shows all-day
  events as 09:00–19:00 because they are stored as 08:00–18:00 UTC.

## Tests

- `tests/pytest/` (no Odoo): the rule engine against Skatteverket's published dates and rules for
  2026 and 2027 (employer declaration tables, VAT per month/quarter/year, INK2/INK4 dates), public
  holidays and moved dates. Run from the repository root:
  `python3 -m pytest -p no:cacheprovider --confcutdir=l10n_se_compliance_calendar/tests/pytest l10n_se_compliance_calendar/tests/pytest`
- `tests/test_compliance_calendar.py` (Odoo, `--test-tags /l10n_se_compliance_calendar`): creating,
  updating and removing events, two runs without duplicates, no `mail.mail`, the contact without
  e-mail, fixed and relative custom rules, multi-company isolation, calendar filters, access and
  Swedish titles. The tests create their own companies.

## Disclaimer

The dates are a reminder, not advice. Check them against Skatteverket and Bolagsverket; the module
does not know about extensions or decisions for the company. See the repository README.
