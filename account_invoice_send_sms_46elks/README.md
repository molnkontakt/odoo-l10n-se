# Send invoices as SMS via 46elks

Adds **SMS via 46elks** to the invoice *Send* dialog (and as the per-customer
default). The text is built from the `sms_46elks.invoice_text` template and sent
to the customer's phone (`phone_sanitized`, E.164; falls back to the first contact
under the customer that has a phone). The portal link carries the invoice's
access token, so the PDF opens without logging in. Cost, parts and the 46elks id
are logged on the invoice. SMS goes out *before* e-mail so an API error stops the
dialog.

## Configuration (`sms_46elks.*` system parameters)

| Key | Meaning |
|---|---|
| `sms_46elks.username`, `sms_46elks.password` | API credentials; empty = the checkbox is hidden |
| `sms_46elks.sender` | Sender name (max 11 chars) or number |
| `sms_46elks.dryrun` (0) | 1 = 46elks validates and prices but does not send |
| `sms_46elks.public_base_url` | Base for the portal link; empty = `web.base.url` |
| `sms_46elks.invoice_text` | Template with `{company} {name} {partner} {amount} {due} {bank} {url}` |

Keep the text within two GSM-7 parts (306 characters); characters outside
GSM 03.38 are rejected rather than silently switching to UCS-2.

## Extending

`_sms_number(partner)`, `_sms_invoice_text(move)` and `_sms_post(to, msg)` are
meant to be overridden or reused; `account_invoice_reminder_sms_46elks` uses them
for payment reminders.
