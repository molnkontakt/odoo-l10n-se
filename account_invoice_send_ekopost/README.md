# Send invoices by post via Ekopost

Adds **Brev via Ekopost** next to E-mail in the invoice *Send* dialog (and as the
per-customer default under *Invoice sending*). Odoo renders the invoice PDF; the
module creates one Ekopost campaign per send with one envelope per invoice,
addressed from the customer's address fields, and closes the campaign (print +
postage). Envelope and campaign ids are logged on the invoice. Letters are sent
*before* e-mail, so an API error stops the dialog before anything is mailed.

## Configuration (`ekopost.*` system parameters)

| Key | Meaning |
|---|---|
| `ekopost.user`, `ekopost.password` or `ekopost.api_key` | Authentication (api-key wins). Empty = the checkbox is hidden |
| `ekopost.url` | `https://api.ekopost.se` (sandbox: `http://api.sandbox.ekopost.se`) |
| `ekopost.color` (1), `ekopost.postage` (economy / priority) | Print options |
| `ekopost.close` (1) | 0 = leave the campaign open (nothing printed) for review in Ekopost's portal |

## Address window

The address is taken from the PDF (`use_coverpage=False`), so the invoice layout
must place the address block inside Ekopost's window (112–207 mm from the left,
35–71 mm from the top on the first page). Odoo's standard layouts do.

## Extending

`_ekopost_postal_address(partner)` builds the address (override for custom name
lines); `_ekopost_post_pdfs(items)` posts any PDFs (used by
`account_invoice_reminder_ekopost` for payment reminders).
