import logging
from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..lib import rate_window

_logger = logging.getLogger(__name__)


class IrMailServer(models.Model):
    _inherit = "ir.mail_server"

    rate_limit_count = fields.Integer(
        string="Rate Limit",
        default=0,
        help="Most messages this server may send per window; 0 means no limit. Each recipient "
        "address counts as one message. Mail over the limit waits in the queue and is sent by the "
        "email queue cron as soon as the window has room. Not used for personal mail servers.",
    )
    rate_limit_minutes = fields.Integer(
        string="Window (Minutes)",
        default=5,
        help="Length of the sliding window the rate limit counts over.",
    )
    rate_limit_retry_minutes = fields.Integer(
        string="Retry After (Minutes)",
        default=6,
        help="When the server answers with a temporary error (4xx, e.g. 451 too many mails), the "
        "mail goes back to the queue and is sent again after this many minutes, only to the "
        "recipients it has not reached yet.",
    )
    rate_limit_retry_max = fields.Integer(
        string="Retries",
        default=5,
        help="How many times a mail goes back to the queue after a temporary error before it is "
        "marked as failed.",
    )
    rate_limit_used = fields.Integer(
        string="Used in Current Window",
        compute="_compute_rate_limit_used",
        help="Messages counted in the current window. A temporary error from the server counts "
        "as a full window.",
    )

    @api.constrains("rate_limit_count", "rate_limit_minutes", "rate_limit_retry_minutes", "rate_limit_retry_max")
    def _check_rate_limit(self):
        for server in self:
            if server.rate_limit_count < 0 or server.rate_limit_retry_max < 0:
                raise ValidationError(self.env._("The rate limit and the number of retries cannot be negative."))
            if server.rate_limit_count and (server.rate_limit_minutes < 1 or server.rate_limit_retry_minutes < 1):
                raise ValidationError(self.env._("The rate limit window and the retry delay must be at least one minute."))

    def _compute_rate_limit_used(self):
        now = fields.Datetime.now()
        for server in self:
            server.rate_limit_used = (
                rate_window.used(server._rate_limit_slots(now), now, server._rate_limit_window())
                if server.id and server._rate_limit_active() else 0
            )

    # ------------------------------------------------------------------
    # Helpers used by mail.mail
    # ------------------------------------------------------------------

    def _rate_limit_active(self):
        """Whether mail through this server is paced. Personal servers keep core's own throttle."""
        self.ensure_one()
        return self.rate_limit_count > 0 and not self.owner_user_id

    def _rate_limit_window(self):
        self.ensure_one()
        return timedelta(minutes=max(self.rate_limit_minutes, 1))

    def _rate_limit_slots(self, now):
        """``(at, count)`` of the slots in the window that ends at ``now``."""
        self.ensure_one()
        slots = self.env["mail.server.rate.slot"].sudo().search([
            ("server_id", "=", self.id),
            ("at", ">", now - self._rate_limit_window()),
        ])
        return [(slot.at, slot.count) for slot in slots]

    def _rate_limit_budget(self, now):
        """Messages that may still be sent in the window that ends at ``now``."""
        self.ensure_one()
        return self.rate_limit_count - rate_window.used(self._rate_limit_slots(now), now, self._rate_limit_window())

    def _rate_limit_consume(self, count, now):
        self.ensure_one()
        if count > 0:
            self.env["mail.server.rate.slot"].sudo().create({"server_id": self.id, "at": now, "count": count})

    def _rate_limit_defer(self, mails, now):
        """Give ``mails`` (first in line first) a scheduled date when the window has room for them,
        and wake the email queue cron at each of those dates.

        Every date needs its own trigger: a queue run only selects mails that are due, so when the
        ones due at the first date fit the window nothing is deferred again and no later trigger
        would be made; mails for the dates after it would wait for the cron's own (hourly) run."""
        self.ensure_one()
        if not mails:
            return
        times = rate_window.schedule(
            self._rate_limit_slots(now),
            [mail._rate_limit_cost() for mail in mails],
            now,
            self._rate_limit_window(),
            self.rate_limit_count,
        )
        by_time = defaultdict(list)
        for mail, at in zip(mails, times, strict=True):
            by_time[at].append(mail.id)
        for at, mail_ids in by_time.items():
            mails.browse(mail_ids).sudo().write({"scheduled_date": at})
        self._rate_limit_wake_queue(sorted(by_time))
        _logger.info(
            "Mail server %s: rate limit %s per %s min, %s mail(s) deferred, first at %s, last at %s (UTC)",
            self.id, self.rate_limit_count, self.rate_limit_minutes, len(mails), min(times), max(times),
        )

    @api.model
    def _rate_limit_wake_queue(self, at=None):
        """Ask the email queue cron to run at ``at``: a datetime, a list of datetimes (one run at
        each), or None for now."""
        cron = self.env.ref("mail.ir_cron_mail_scheduler_action", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger(at)
