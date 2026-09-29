# Mail Server Rate Limit

A rate limit for an outgoing mail server: at most **N messages per M minutes**. Mail over the
limit waits in Odoo's mail queue and is sent automatically when the window has room. A temporary
refusal from the server (4xx, such as `451 4.7.1 Too many mails`) puts the mail back in the queue,
**only for the recipients it has not reached**, instead of failing it. Depends only on `mail`;
nothing here is specific to Sweden.

## Why

Many SMTP providers accept only so many messages per few minutes from one account and answer
`451` above that. Odoo sends the whole queue at once and treats a `451` as a failure: the mail
ends in *Delivery Failed* and is never retried. Sending 40 invoices in one go then fails after the
first 20-25. When a notification with several recipients is refused halfway, a plain *Retry*
also sends a second copy to the recipients who already had it.

## Configuration

*Settings → Technical → Email → Outgoing Mail Servers*, tab **Rate Limit** (needs developer mode
for the menu):

| Field | Default | Meaning |
|---|---|---|
| Rate Limit | 0 | Most messages per window; 0 = no limit (Odoo's own behaviour). Each recipient address counts as one message |
| Window (Minutes) | 5 | Length of the sliding window |
| Retry After (Minutes) | 6 | When the server answers with a temporary error, the mail is tried again after this delay |
| Retries | 5 | How many times a mail goes back to the queue before it fails as before |
| Used in Current Window | | Messages counted in the current window (read only) |

Set the limit somewhat below what the provider allows: mail that leaves the account in other ways
counts against the provider's limit but is not seen here (see *Limitations*). The tab is hidden
for personal mail servers (*Owner* set): Odoo already throttles those per minute, and they are
left alone.

## How it works

- **Only the queue cron sends through a limited server.** *Mail: Email Queue Manager* is the one
  sender that counts. Everything else that sends at once (a chatter post right after commit,
  *Send Now*, a template sent with `force_send`, a reminder or invoice run) leaves the mail in the
  queue with state *Outgoing* and triggers the cron, which runs within seconds. ir.cron never runs
  the same job twice at a time (a manual *Run* takes the same lock), so the count needs no lock.
- **The window.** Each run counts the messages sent through the server in the last *Window*
  minutes (a small log, `mail.server.rate.slot`, because sent e-mails are usually deleted), sends
  the oldest mails that fit and gives the others a *Scheduled Send Date* for when there is room.
  The cron is triggered for each of those dates (a run only picks up mail that is due, so one
  trigger for the first date would leave the later ones to the cron's hourly run). A mail with
  more recipients than the whole limit is split into a part that fits and a rest, the way Odoo
  splits one for a personal mail server; its notifications follow the recipients. The parts share
  one message, and deleting a sent part (or the rest) never deletes the others with it.
- **Temporary errors.** Odoo sends a notification once per recipient. When the server refuses one
  of them with a 4xx answer (`(4xx, ...)` from smtplib, an enhanced status `4.x.x`, or "too many
  mails"), the mail goes back to the queue without the recipients already reached. Their
  notifications are marked *Sent*, the others go back to *Ready* (a grey envelope, not a red one).
  The server is then counted as full for a whole window, and the mails left in that batch are not
  tried but wait for it. After *Retries* retries the mail fails with the server's answer, as in
  standard Odoo, but without the recipients it already reached: *Retry* on the failed e-mail
  gives it its retries back and sends only to the ones left.
- Permanent errors (5xx), invalid addresses and connection failures are handled by Odoo as before.

The e-mail list (*Settings → Technical → Emails*) shows waiting mail as *Outgoing* with a
scheduled date; the *Advanced* tab of an e-mail shows its rate limit retries. The server log has
a line per deferral (`rate limit: N sent / M deferred`) and a warning per temporary error.

## Limitations

- **Mail sent outside Odoo shares the provider's budget.** Messages sent from the same account in
  webmail or a mail client during a run are not counted here. The provider then refuses earlier;
  the retry covers that, but a long run can use up a mail's retries. Keep a margin below the
  provider's limit, and avoid sending from the same account during a large run.
- **A dropped connection is not retried.** If the server closes the connection
  (`SMTPServerDisconnected`), Odoo stops the batch; the mail being sent at that moment stays
  *Delivery Failed* ("Error without exception...") because it may or may not have been delivered,
  and retrying it could send a duplicate. The mails after it stay in the queue. Check for failed
  e-mails after a large run and retry them by hand if the recipients did not get them.
- A temporary error while connecting or logging in (not per message) fails the batch as in
  standard Odoo; so does a 4xx answer that follows an invalid address in the same mail, because
  Odoo then keeps the invalid-address reason.
- The count is per mail server record, not per provider account: two server records that log in to
  the same account each get the full limit.
- The window is counted when a batch is handed to the SMTP connection, including messages that
  then fail. That errs on the slow side.
- Sending outside the cron becomes asynchronous for a limited server. A module that checks the
  e-mail's state right after `send()` sees *Outgoing*.
- The module overrides private methods of `mail.mail` in Odoo 19.0 (`_split_by_mail_configuration`,
  `_send`, `_postprocess_sent_message`) and depends on how `_send` reports a refused recipient.
  The tests pin these signatures and that behaviour; run them after an Odoo update.

## Tests

- `tests/test_rate_limit.py` (Odoo, `--test-tags /mail_server_rate_limit`): pacing, deferral and
  a cron trigger for every deferral date, sending outside the cron, splitting (a notification and a
  template mail that owns its message, also when the rest is sent before a retried part), a `451`
  on the second of three recipients, the retry maximum and a *Retry* by hand after it, personal
  servers and the core signatures. SMTP is mocked
  (`MockSmtplibCase`); the tests create their own server on an `example.com` domain and only run
  the queue on their own mails, so they can also run inside an existing database and are rolled
  back.
- `tests/pytest/` (no Odoo): the window arithmetic and the temporary-error detection in
  `lib/rate_window.py`.
