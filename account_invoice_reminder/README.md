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
  amount and income account, mail template and a text shown in the PDF and e-mail. Levels are walked in sequence: an invoice gets the next
  level after the last one sent once the days are reached. A level is never
  skipped (an invoice 50 days overdue that was never reminded gets level 1).
- **Reminder** (`account.reminder`, *Customers → Payment reminders*): one customer,
  one level, all invoices eligible for that level. The fee is a line *on the
  reminder* (PDF, e-mail, SMS), not a separate invoice, as in Fortnox/Visma/Bokio:
  nothing is booked until the customer pays it. Can be *prepared* first and sent
  later.
- **Booking the fee**: for each level with a fee and income account the module
  keeps a manual reconciliation model (a button in the bank reconciliation view,
  *Påminnelseavgift (level)*) that books the surplus on the bank line to the income
  account. Invoice residual + fee → click the invoice, click the button, reconcile.
- **Delivery** (`channel`): *E-mail* (template + PDF + the invoices) or *Manual*
  (no sending; PDF kept on the reminder for printing). Default per customer via
  `_default_channel(partner)`. Other modules add channels with `selection_add`
  and a `_send_<code>()` method.
- **Invoice**: fields *Next reminder level* (computed for today), *Last level*,
  *Last reminded*, *Count*; filters *Reminder to send* and *Reminded*; a chatter
  note per reminder.

## Workflow

Invoices → filter *Reminder to send* → select → *Action → Send payment reminder*
(or *Customers → Send reminders* for everything eligible). The dialog shows one
selectable row per customer and level (the reminder) with e-mail, channel, number
of invoices, overdue amount and the fee once, with its basis (*1 avgift per
påminnelse*, or *60,00 + 60,00 tidigare* when unpaid fees from earlier reminders on
the same invoices are carried over), and below it one row per invoice with invoice
date, due date, days overdue, what is left to pay and the level; deselecting a
customer greys out its invoices. A note explains that the fee is a line on the
reminder, booked on the level's income account only when paid, and one per reminder
regardless of the number of invoices. *Create and send* or *Only prepare*. There is
no cron: nothing leaves the system without a click.

## Debt collection demands (inkassokrav) without an agency

Collecting your own claims needs no permit in Sweden (inkassolagen only licenses
collection on behalf of others), but the demand must meet 5 § inkassolagen. The
level settings and the document cover the formal requirements: creditor with
registration number, the claim's basis (invoice, date, *Avser*), principal and
each fee separately (fees from earlier reminders on the same invoices are carried
over as their own lines), a *pay by* date (`days_to_pay`, at least 8 days for a
demand), the consequences in the level text, and *Kräver brev* so the default
channel is a letter. Interest is not handled.

## Swedish defaults

The shipped level text and mail template reference *lag (1981:739) om ersättning
för inkassokostnader* (60 kr reminder fee, 180 kr debt collection demand). Both
are plain data and can be edited per level or template.

## Notes

- What the e-mail carries on top of the template comes from
  `account.reminder._email_values(attachments)` (by default the invoices' PDFs). It is applied
  after the template's `partner_to`, so a module can send the reminder to other recipients by
  overriding it and returning `recipient_ids: [Command.set(partner_ids)]`.
- The e-mail is sent with `force_send`: at once on an ordinary outgoing server. It goes through
  `mail.mail.send()`, so an outgoing-server rate limit that defers mail sent outside the mail
  queue cron can hold it in the queue; it then leaves with the cron. The reminder is marked sent
  when the dialog runs; a delivery failure afterwards shows on the e-mail (*Settings → Technical
  → Emails*, filter *Exception*), not on the reminder.
- Sending checks every reminder of the run first (`account.reminder._check_send()`) and sends
  nothing if one of them cannot go out on its channel, with one error listing all of them. SMS and
  letters leave at once and cannot be called back, so an error halfway would otherwise roll back
  their reminders and send them again on the next try. A module with its own requirements for a
  channel overrides `_check_send`.
- The mail template has `use_default_to = False` on purpose: with the default,
  Odoo ignores `partner_to` and computes default recipients, which never include
  an address that is one of the system's own aliases (e.g. a distribution-list
  alias on the customer), leaving the mail without recipients.
- The reminder fee is VAT-exempt in Sweden; account 3930 *Påminnelseavgifter* is
  customary. Reminders created before 1.2.0 keep their separate fee invoice in
  *Avgiftsfaktura (äldre)*.
