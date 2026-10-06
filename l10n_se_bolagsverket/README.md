# Sweden – Bolagsverket

Company data and status watch from Bolagsverket's free **Värdefulla datamängder** (high-value datasets) API, for
Swedish customers and suppliers. Depends on `account`.

## What it does

| Feature | Where | What happens |
|---|---|---|
| **Fetch from Bolagsverket** | Contact, next to *Company ID* | Registered name and postal address (street, c/o, postcode, city) replace what's in Odoo after a confirmation; company form, registration date, industry codes (SNI), registered business and advertising block are stored on the *Bolagsverket* tab. The change is noted in the chatter |
| **Status watch** | Scheduled job every 30 minutes | Companies that are customers or suppliers (company contacts with a customer or supplier rank – never private persons) with an organisation number are checked when the number is added or changed, then weekly. *Bankruptcy, liquidation or reorganisation* and *Deregistered* give a red banner on the contact and on its invoices and bills, a note on the contact and a to-do for the follow-up user of the contact's company (for a shared contact: of every company that has one), in that user's language. On a customer invoice the banner reminds you to follow up the receivable; on a bill, to check before paying. Settled and cancelled documents show nothing |
| **Name check** | Contact and vendor bills | When the name in Odoo is none of the names registered for the number (company name or secondary business names), the contact and its bills get a warning – a wrong number, e.g. the buyer's own number read from an invoice, or an invoice pretending to come from a known company |
| **Filters** | Contacts search | *Bolagsverket: bankrupt, liquidated or deregistered*, *Bolagsverket: name differs* |

The organisation number comes from **Company ID** (`company_registry`, contacts in Sweden or without a country) or,
when that is empty, from a **Swedish VAT number** (`SE5595880419 01` → `5595880419`). Numbers must pass the Luhn
check and look like a real number (an organisation number has a third digit of 2 or more, a personal identity number a valid date). Sole traders work too: the number is sent with the century as Bolagsverket expects, the business that isn't deregistered and matches the name is used, and which one it is is remembered when you fetch. A personal identity number is never written into chatter, to-dos or logs.

A status check never changes the contact's own data; only the button does.

## Setup

1. Apply for API access at Bolagsverket (*API för värdefulla datamängder*). It's free, no agreement; you get a
   client id and secret for test and for production.
2. *Accounting → Configuration → Settings → Bolagsverket*: tick the box, enter the client id, choose the
   environment and the user who gets to-dos.
3. Set the secret as the system parameter `l10n_se_bolagsverket.client_secret` (it is never shown in the UI).

Bolagsverket closes an API account that hasn't been used for **six months**; when there is nothing to check, the
job sends one `/isalive` per day to keep it open.

## What the API gives – and doesn't

Name(s), company form, legal form, postal address, SNI codes, registered business, registration date, whether the
organisation trades, deregistration with reason, ongoing bankruptcy/liquidation/reorganisation with dates,
advertising block, and digitally filed annual reports (not used by this version). **Not included**: signatories,
board members, owners – those need Bolagsverket's paid *Företagsinformation* API.

Technical notes (in `lib/bolagsverket.py`, pure Python): OAuth2 client credentials at
`https://portal.api.bolagsverket.se/oauth2/token` (test: `portal-accept2`), API at
`https://gw.api.bolagsverket.se/vardefulla-datamangder/v1` (test: `gw-accept2`). An unknown number is answered with
HTTP 200 and `fel.typ = ORGANISATION_FINNS_EJ` on every part; a malformed one with 400. Tokens are cached for their
lifetime (3600 s) and renewed once on a 401.

**An error never changes a status.** When a data source behind the API is unavailable (`OTILLGANGLIG_UPPGIFTSKALLA`, `TIMEOUT` on a part that decides the status) the whole answer is ignored; network errors, 429, 5xx, token problems and unreadable answers stop the run until the next one; a refused number (400) or an unexpected error on one contact is noted on that contact and retried a day later while the queue continues. The job never raises, so Odoo doesn't switch it off.

## Copies of the database

`odoo neutralize` removes the client secret and switches the integration off.

## Tests

`tests/pytest` (library, no Odoo): `python -m pytest -p no:cacheprovider --confcutdir=l10n_se_bolagsverket/tests/pytest
l10n_se_bolagsverket/tests/pytest`. `tests/test_bolagsverket.py` (Odoo): `--test-tags /l10n_se_bolagsverket`. The
JSON fixtures have the exact structure of live answers with invented names, numbers and addresses.
