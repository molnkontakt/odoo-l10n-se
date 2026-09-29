"""Rate limit per outgoing mail server, on top of Odoo 19's mail queue.

How it hooks into core (odoo/addons/mail/models/mail_mail.py, Odoo 19.0):

* ``process_email_queue`` is only called by the *Mail: Email Queue Manager* cron, which ir.cron
  runs one at a time (a manual *Run* takes the same job lock). It marks the context as the
  *runner*: only the runner sends through a rate-limited server, so the counter needs no lock.
* ``send`` is the only caller of the generator ``_split_by_mail_configuration``, which yields
  ``(mail_server_id, alias_domain_id, smtp_from, batch_ids)``. For a rate-limited server the
  override yields only what fits the window; the rest keeps state *outgoing*, gets a
  ``scheduled_date`` when the window has room (``process_email_queue`` skips it until then) and
  the cron is triggered for that time. Outside the runner (sending right after a post, *Send
  Now*, a template sent with ``force_send``) nothing is sent: the mail stays in the queue and the
  cron is woken up.
* ``_send`` loops over the recipients of one mail. A 4xx answer raised by ``send_email`` is a
  ``MailDeliveryException``: it leaves that loop with the recipients already reached in
  ``success_pids`` / ``success_emails`` and ends in ``_postprocess_sent_message`` with
  ``failure_type='unknown'``. The override puts such a mail back in the queue without the
  recipients it reached, so a retry never sends anyone a second copy. A mail that fails for good
  loses the reached recipients too, so a *Retry* by hand does not reach them twice either.
* ``unlink`` of a mail that is not a notification also deletes its ``mail.message``, and with it
  (``ondelete='cascade'``) every other mail on that message. The parts of a split mail share the
  message, so the override hands it over to a part that is still there.
"""

import logging
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.tools import email_split

from ..lib import rate_window

_logger = logging.getLogger(__name__)

RUNNER_KEY = "mail_rate_limit_runner"
SERVER_KEY = "mail_rate_limit_server_id"


class MailMail(models.Model):
    _inherit = "mail.mail"

    rate_limit_retries = fields.Integer(
        string="Rate Limit Retries",
        copy=False,
        readonly=True,
        help="How many times this mail went back to the queue after a temporary error from a "
        "rate-limited mail server.",
    )

    @api.model
    def process_email_queue(self, email_ids=(), batch_size=1000):
        return super(MailMail, self.with_context(**{RUNNER_KEY: True})).process_email_queue(
            email_ids=email_ids, batch_size=batch_size)

    def mark_outgoing(self):
        # Retry by hand: the mail gets its full number of automatic retries again.
        self.filtered("rate_limit_retries").sudo().write({"rate_limit_retries": 0})
        return super().mark_outgoing()

    def unlink(self):
        # Core deletes the mail.message of a mail that is not a notification (a template mail, which
        # owns its message), and the database then deletes every other mail on that message. After a
        # split the parts share the message: whichever is deleted first would take the others, with
        # the recipients they have not reached yet. Hand the message over to a mail that stays; the
        # last one deletes it.
        owners = self.sudo().filtered(lambda m: not m.is_notification and m.mail_message_id)
        if owners:
            others = self.sudo().search([
                ("mail_message_id", "in", owners.mail_message_id.ids),
                ("id", "not in", self.ids),
            ], order="id")
            heirs = {}
            for other in others:
                heirs.setdefault(other.mail_message_id.id, other)
            if heirs:
                owners.filtered(lambda m: m.mail_message_id.id in heirs).write({"is_notification": True})
                self.browse([heir.id for heir in heirs.values()]).sudo().write({"is_notification": False})
        return super().unlink()

    # ------------------------------------------------------------------
    # Pacing
    # ------------------------------------------------------------------

    def _rate_limit_cost(self):
        """Messages this mail counts as: one per recipient address, at least one."""
        self.ensure_one()
        return max(1, len(self.recipient_ids) + len(email_split(self.email_to or "")) + len(email_split(self.email_cc or "")))

    def _split_by_mail_configuration(self):
        runner = self.env.context.get(RUNNER_KEY)
        for mail_server_id, alias_domain_id, smtp_from, batch_ids in super()._split_by_mail_configuration():
            server = self.env["ir.mail_server"].sudo().browse(mail_server_id)
            if not server or not server._rate_limit_active():
                yield mail_server_id, alias_domain_id, smtp_from, batch_ids
                continue
            if not runner:
                # Leave it to the queue cron, the only sender that keeps count.
                server._rate_limit_wake_queue()
                _logger.info("Mail server %s: %s mail(s) left in the queue for the rate-limited cron",
                             server.id, len(batch_ids))
                continue
            send_ids = self.browse(batch_ids)._rate_limit_take(server, fields.Datetime.now())
            if send_ids:
                yield mail_server_id, alias_domain_id, smtp_from, send_ids

    def _rate_limit_take(self, server, now):
        """Ids of the mails (oldest first) that fit what is left of the window; count them and
        defer the others. A mail with more recipients than the whole limit is split, the way
        core splits one for a personal mail server."""
        mails = self.sudo().filtered(lambda m: m.state == "outgoing").sorted("id")
        limit = server.rate_limit_count
        room = server._rate_limit_budget(now)
        send_ids = []
        deferred = self.browse()
        spent = 0
        for index, mail in enumerate(mails):
            cost = mail._rate_limit_cost()
            if cost <= room - spent:
                send_ids.append(mail.id)
                spent += cost
                continue
            if cost > limit and room - spent > 0:
                part = mail._rate_limit_split(room - spent)
                if part:
                    send_ids.append(part.id)
                    spent += part._rate_limit_cost()
                elif room - spent == limit:
                    # Cannot be split (addresses in To/Cc) and the window is empty: send it whole
                    # rather than never.
                    _logger.warning("mail.mail %s: %s recipient addresses exceed the rate limit of mail server %s",
                                    mail.id, cost, server.id)
                    send_ids.append(mail.id)
                    spent += cost
                    deferred = mails[index + 1:]
                    break
            deferred = mails[index:]
            break
        server._rate_limit_consume(spent, now)
        server._rate_limit_defer(deferred, now)
        if deferred:
            _logger.info("Mail server %s: rate limit: %s sent / %s deferred", server.id, len(send_ids), len(deferred))
        return send_ids

    def _rate_limit_split(self, room):
        """Move as many recipient partners as fit ``room`` (with the To/Cc addresses) to a copy
        of this mail and return the copy; empty if nothing can be split off."""
        self.ensure_one()
        generic = len(email_split(self.email_to or "")) + len(email_split(self.email_cc or ""))
        keep = room - generic
        partners = self.recipient_ids
        if keep < 1 or len(partners) <= keep:
            return self.browse()
        first = partners[:keep]
        part = self.with_user(self.create_uid or self.env.user).sudo().copy({
            "headers": self.headers,
            "mail_message_id": self.mail_message_id.id,
            # The part shares the message: as a notification, deleting it (auto_delete once sent)
            # never deletes the message, and with it this mail and the recipients it still has.
            "is_notification": True,
            "recipient_ids": [Command.set(first.ids)],
        })
        self.sudo().write({
            "recipient_ids": [Command.set((partners - first).ids)],
            "email_to": False,
            "email_cc": False,
        })
        self.env["mail.notification"].sudo().search([
            ("notification_type", "=", "email"),
            ("mail_mail_id", "=", self.id),
            ("notification_status", "not in", ("sent", "canceled")),
            "|", ("res_partner_id", "in", first.ids), ("res_partner_id", "=", False),
        ]).mail_mail_id = part
        return part.with_env(self.env)

    def _send(self, auto_commit=False, raise_exception=False, smtp_session=None, alias_domain_id=False,
              mail_server=False, post_send_callback=None):
        if not mail_server or not mail_server.sudo()._rate_limit_active():
            return super()._send(
                auto_commit=auto_commit, raise_exception=raise_exception, smtp_session=smtp_session,
                alias_domain_id=alias_domain_id, mail_server=mail_server, post_send_callback=post_send_callback)
        # One mail at a time, so that the rest of the batch is not tried once the server has
        # answered "too many mails": it waits for the window instead of using up its retries.
        mails = self.with_context(**{SERVER_KEY: mail_server.id})
        for index, mail in enumerate(mails):
            retries = mail.rate_limit_retries
            super(MailMail, mail)._send(
                auto_commit=auto_commit, raise_exception=raise_exception, smtp_session=smtp_session,
                alias_domain_id=alias_domain_id, mail_server=mail_server, post_send_callback=post_send_callback)
            if mail.exists() and mail.state == "outgoing" and mail.rate_limit_retries > retries:
                rest = self[index + 1:].exists().filtered(lambda m: m.state == "outgoing")
                mail_server.sudo()._rate_limit_defer(rest, fields.Datetime.now())
                break
        return True

    # ------------------------------------------------------------------
    # Temporary errors
    # ------------------------------------------------------------------

    def _postprocess_sent_message(self, success_pids, success_emails, failure_reason=False, failure_type=None):
        server_id = self.env.context.get(SERVER_KEY)
        if not server_id or failure_type != "unknown":
            return super()._postprocess_sent_message(
                success_pids, success_emails, failure_reason=failure_reason, failure_type=failure_type)
        server = self.env["ir.mail_server"].sudo().browse(server_id)
        retry = self.filtered(lambda m: (
            server._rate_limit_active()
            and m.rate_limit_retries < server.rate_limit_retry_max
            and rate_window.is_temporary_smtp_error(failure_reason or m.failure_reason)
        ))
        requeued = retry._rate_limit_requeue(server, success_pids, success_emails)
        rest = self - requeued
        if rest:
            # Failed for good: a Retry by hand (mark_outgoing) builds one message per recipient
            # again, so the ones already reached are taken off. Core only uses success_pids for the
            # notification status, not recipient_ids.
            rest._rate_limit_strip_reached(success_pids, success_emails)
            super(MailMail, rest)._postprocess_sent_message(
                success_pids, success_emails, failure_reason=failure_reason, failure_type=failure_type)
        return True

    def _rate_limit_requeue(self, server, success_pids, success_emails):
        """Put mails that got a temporary error back in the queue, without the recipients they
        already reached, and count a full window against the server. Returns the requeued mails."""
        if not self:
            return self
        now = fields.Datetime.now()
        retry_at = now + timedelta(minutes=max(server.rate_limit_retry_minutes, 1))
        reached_pids, reached_emails, generic_done = _reached(success_pids, success_emails)
        requeued = self.browse()
        for mail in self.sudo():
            left_partners = mail.recipient_ids.filtered(lambda p: p.id not in reached_pids)
            left_generic = not generic_done and bool((mail.email_to or "").strip() or (mail.email_cc or "").strip())
            if not left_partners and not left_generic:
                continue
            vals = {
                "state": "outgoing",
                "failure_type": False,
                "failure_reason": False,
                "rate_limit_retries": mail.rate_limit_retries + 1,
                "scheduled_date": retry_at,
            }
            if left_partners != mail.recipient_ids:
                vals["recipient_ids"] = [Command.set(left_partners.ids)]
            if generic_done:
                vals.update(email_to=False, email_cc=False)
            notifications = self.env["mail.notification"].sudo().search([
                ("notification_type", "=", "email"),
                ("mail_mail_id", "=", mail.id),
                ("notification_status", "not in", ("sent", "canceled")),
            ])
            reached = notifications.filtered(lambda n: (
                n.res_partner_id.id in reached_pids
                or (n.mail_email_address and n.mail_email_address in reached_emails)
                or (generic_done and not n.res_partner_id)
            ))
            reached.write({"notification_status": "sent", "failure_type": False, "failure_reason": False})
            (notifications - reached).write({"notification_status": "ready", "failure_type": False, "failure_reason": False})
            mail.write(vals)
            requeued |= mail
            _logger.warning(
                "mail.mail %s: temporary error from mail server %s; retry %s of %s at %s (UTC), %s recipient(s) already reached",
                mail.id, server.id, vals["rate_limit_retries"], server.rate_limit_retry_max, retry_at,
                len(reached_pids) + len(reached_emails),
            )
        if requeued:
            # The server is refusing: count a full window so nothing else is tried before it has passed.
            server._rate_limit_consume(server.rate_limit_count, now)
            server._rate_limit_wake_queue(retry_at)
        return requeued.with_env(self.env)

    def _rate_limit_strip_reached(self, success_pids, success_emails):
        """Take the recipients a failed send already reached off these mails: the partners in
        ``success_pids``, and the To/Cc addresses once anything was reached."""
        reached_pids, reached_emails, generic_done = _reached(success_pids, success_emails)
        if not generic_done:
            return
        for mail in self.sudo():
            vals = {}
            left_partners = mail.recipient_ids.filtered(lambda p: p.id not in reached_pids)
            if left_partners != mail.recipient_ids:
                vals["recipient_ids"] = [Command.set(left_partners.ids)]
            if mail.email_to or mail.email_cc:
                vals.update(email_to=False, email_cc=False)
            if vals:
                mail.write(vals)
                _logger.info("mail.mail %s: failed after reaching %s recipient(s); they are taken off so that a "
                             "retry does not reach them twice", mail.id, len(reached_pids) + len(reached_emails))


def _reached(success_pids, success_emails):
    """``(partner ids, addresses, generic_done)`` reached before ``_send`` stopped on a mail.

    Core appends res.partner records to ``success_pids`` and formatted addresses to
    ``success_emails``. ``_prepare_outgoing_list`` sends the To/Cc message before the per-partner
    ones, so once anything was reached the To/Cc addresses were handled too (sent, or skipped as
    invalid): ``generic_done``.
    """
    reached_pids = {getattr(pid, "id", pid) for pid in success_pids or ()}
    reached_emails = set(success_emails or ())
    return reached_pids, reached_emails, bool(reached_pids or reached_emails)
