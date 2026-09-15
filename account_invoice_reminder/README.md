# Payment reminders (Betalningspåminnelser)

Payment reminders for Odoo 19 Community. Odoo's *Follow-up* app is Enterprise-only
and the OCA alternatives are not available on 19.

> [!WARNING]
> Under active development, use at your own risk. Reminders and fees go to real
> customers; review the dialog before sending. See the repository README for the
> full disclaimer.

## Concepts

- **Reminder level** (`account.reminder.level`, *Accounting → Configuration →
  Reminder levels*): per company, with name, *days after due date*, optional fee
  product and amount, journal for the fee invoice, mail template and a text shown
  in the PDF and e-mail. Levels are walked in sequence: an invoice gets the next
  level after the last one sent once the days are reached. A level is never
  skipped (an invoice 50 days overdue that was never reminded gets level 1).
- **Reminder** (`account.reminder`, *Customers → Payment reminders*): one customer,
  one level, all invoices eligible for that level. Creates the fee as a separate
  posted customer invoice (`reminder_fee_for_id`; fee invoices are never reminded
  themselves), renders the PDF (`report_reminder`) and delivers it. Can be
  *prepared* first and sent later. Cancelling a prepared reminder credits the fee
  invoice (posted entries may be hashed).
- **Delivery** (`channel`): *E-mail* (template + PDF + the invoices) or *Manual*
  (no sending; PDF kept on the reminder for printing). Default per customer via
  `_default_channel(partner)`. Other modules add channels with `selection_add`
  and a `_send_<code>()` method.
- **Invoice**: fields *Next reminder level* (computed for today), *Last level*,
  *Last reminded*, *Count*; filters *Reminder to send* and *Reminded*; a chatter
  note per reminder.

## Workflow

Invoices → filter *Reminder to send* → select → *Action → Send payment reminder*
(or *Customers → Send reminders* for everything eligible). The dialog shows one row
per customer and level with invoices, overdue amount, fee, e-mail and channel;
rows can be deselected. *Create and send* or *Only prepare*. There is no cron:
nothing leaves the system without a click.

## Swedish defaults

The shipped level text and mail template reference *lag (1981:739) om ersättning
för inkassokostnader* (60 kr reminder fee, 180 kr debt collection demand). Both
are plain data and can be edited per level or template.

## Notes

- The mail template has `use_default_to = False` on purpose: with the default,
  Odoo ignores `partner_to` and computes default recipients, which never include
  an address that is one of the system's own aliases (e.g. a distribution-list
  alias on the customer), leaving the mail without recipients.
- Fee invoices use the fee product's income account and taxes; the reminder fee
  is VAT-exempt in Sweden (account 3930 is customary).
