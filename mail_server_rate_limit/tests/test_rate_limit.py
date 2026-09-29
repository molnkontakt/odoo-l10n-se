import inspect
import smtplib
from contextlib import contextmanager
from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo import Command
from odoo.addons.base.tests.common import MockSmtplibCase
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

DOMAIN = "rate-limit.example.com"
SENDER = f"sender@{DOMAIN}"
NOW = datetime(2026, 1, 5, 10, 0, 0)
WINDOW = timedelta(minutes=5)
MARGIN = timedelta(seconds=15)
TOO_MANY = (451, b"4.7.1 [R2] Too many mails received from test within the last 5 minutes")
UNKNOWN_USER = (550, b"5.1.1 No such user")


@tagged("post_install", "-at_install")
class TestMailServerRateLimit(TransactionCase, MockSmtplibCase):
    """Pacing through a rate-limited outgoing mail server, with a mocked SMTP connection.

    Written so that it can also run inside a production database (rolled back): its own mail
    server on an example.com domain, its own mails, and the queue is only run on those mails.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = cls.env["ir.mail_server"].create({
            "name": "Rate limit test server",
            "smtp_host": f"smtp.{DOMAIN}",
            "smtp_port": 25,
            "smtp_encryption": "none",
            "from_filter": DOMAIN,
            "rate_limit_count": 3,
            "rate_limit_minutes": 5,
            "rate_limit_retry_minutes": 6,
            "rate_limit_retry_max": 2,
        })
        cls.cron = cls.env.ref("mail.ir_cron_mail_scheduler_action")
        cls.partners = cls.env["res.partner"].create([
            {"name": f"Rate limit recipient {i}", "email": f"recipient{i}@{DOMAIN}"} for i in range(5)
        ])

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mails(self, count):
        return self.env["mail.mail"].create([{
            "subject": f"Rate limit test {i}",
            "body_html": "<p>Test</p>",
            "email_from": SENDER,
            "email_to": f"to{i}@{DOMAIN}",
        } for i in range(count)])

    def _notification_mail(self, partners):
        """A mail.mail for a chatter message, with one email notification per partner."""
        thread = self.env["res.partner"].with_context(mail_create_nosubscribe=True).create({"name": "Rate limit thread"})
        message = self.env["mail.message"].create({
            "model": "res.partner",
            "res_id": thread.id,
            "message_type": "comment",
            "subject": "Invoice",
            "body": "<p>Invoice</p>",
            "email_from": SENDER,
        })
        mail = self.env["mail.mail"].create({
            "mail_message_id": message.id,
            "body_html": "<p>Invoice</p>",
            "recipient_ids": [Command.set(partners.ids)],
        })
        self.env["mail.notification"].create([{
            "mail_message_id": message.id,
            "mail_mail_id": mail.id,
            "res_partner_id": partner.id,
            "notification_type": "email",
            "notification_status": "ready",
        } for partner in partners])
        return mail

    def _template_mail(self, partners):
        """A mail.mail as mail.template.send_mail makes one: no notification, its own message
        (created through _inherits), deleted once sent."""
        mail = self.env["mail.mail"].create({
            "subject": "Template mail",
            "body_html": "<p>Template</p>",
            "email_from": SENDER,
            "recipient_ids": [Command.set(partners.ids)],
            "auto_delete": True,
        })
        self.assertFalse(mail.is_notification)
        return mail

    def _notifications(self, mail):
        return self.env["mail.notification"].search([("mail_message_id", "=", mail.mail_message_id.id)])

    @contextmanager
    def _mock_smtp(self, refuse=(), answer=TOO_MANY):
        """Mocked SMTP; the n-th message sent (1-based, or every one with refuse='all') is
        refused with ``answer``. Accepted messages are in self.emails, every try in self.smtp_attempts."""
        with self.mock_smtplib_connection():
            accept = self.testing_smtp_session.send_message
            self.smtp_attempts = []

            def send_message(message, smtp_from, smtp_to_list):
                self.smtp_attempts.append(smtp_to_list)
                if refuse == "all" or len(self.smtp_attempts) in refuse:
                    raise smtplib.SMTPRecipientsRefused({address: answer for address in smtp_to_list})
                return accept(message, smtp_from, smtp_to_list)

            self.testing_smtp_session.send_message = send_message
            yield

    def _run_queue(self, mails):
        """What the Email Queue Manager cron does, limited to ``mails``."""
        with mute_logger("odoo.addons.mail.models.mail_mail"):
            self.env["mail.mail"].with_context(filters=[("id", "in", mails.ids)]).process_email_queue()

    def _slots(self, server=None):
        return self.env["mail.server.rate.slot"].search([("server_id", "=", (server or self.server).id)])

    def _triggers(self, at):
        return self.env["ir.cron.trigger"].search([("cron_id", "=", self.cron.id), ("call_at", "=", at)])

    # ------------------------------------------------------------------
    # (a) the queue sends up to the limit and schedules the rest
    # ------------------------------------------------------------------

    def test_queue_sends_up_to_the_limit_and_defers_the_rest(self):
        mails = self._mails(7)
        with freeze_time(NOW), self._mock_smtp():
            self._run_queue(mails)
            self.server.invalidate_recordset(["rate_limit_used"])
            self.assertEqual(self.server.rate_limit_used, 3)
        self.assertEqual(len(self.emails), 3)
        self.assertEqual(mails[:3].mapped("state"), ["sent"] * 3)
        self.assertEqual(mails[3:].mapped("state"), ["outgoing"] * 4)
        first = NOW + WINDOW + MARGIN
        second = first + WINDOW + MARGIN
        self.assertEqual(mails[3:].mapped("scheduled_date"), [first, first, first, second])
        self.assertTrue(self._triggers(first), "the queue cron is triggered for the first deferred mail")
        self.assertTrue(self._triggers(second), "and for each later date: at `first` nothing is deferred again")
        self.assertEqual(self._slots().mapped("count"), [3])

        # Deferred mail is not picked up before its time.
        with freeze_time(first - timedelta(seconds=1)), self._mock_smtp():
            self._run_queue(mails)
        self.assertFalse(self.emails)

        with freeze_time(first), self._mock_smtp():
            self._run_queue(mails)
        self.assertEqual(len(self.emails), 3)
        self.assertEqual(mails[3:6].mapped("state"), ["sent"] * 3)
        self.assertEqual(mails[6].state, "outgoing")

        with freeze_time(second), self._mock_smtp():
            self._run_queue(mails)
        self.assertEqual(len(self.emails), 1)
        self.assertEqual(set(mails.mapped("state")), {"sent"})

    def test_budget_left_in_the_window_is_used(self):
        self.env["mail.server.rate.slot"].create({"server_id": self.server.id, "at": NOW - timedelta(minutes=4), "count": 2})
        mails = self._mails(3)
        with freeze_time(NOW), self._mock_smtp():
            self._run_queue(mails)
        self.assertEqual(len(self.emails), 1)
        # the older slot leaves the window after one minute, which makes room for both
        at = NOW + timedelta(minutes=1) + MARGIN
        self.assertEqual(mails[1:].mapped("scheduled_date"), [at, at])

    # ------------------------------------------------------------------
    # (b) outside the queue cron nothing is sent; without a limit core is unchanged
    # ------------------------------------------------------------------

    def test_send_outside_the_queue_waits_for_the_cron(self):
        mails = self._mails(2)
        with freeze_time(NOW), self._mock_smtp():
            mails.send()
        self.assertFalse(self.smtp_attempts)
        self.assertEqual(mails.mapped("state"), ["outgoing"] * 2)
        self.assertFalse(any(mails.mapped("scheduled_date")))
        self.assertFalse(self._slots())
        if self.cron.active:
            self.assertTrue(self._triggers(NOW), "the queue cron is woken up")

    def test_no_limit_keeps_core_behaviour(self):
        self.server.rate_limit_count = 0
        mails = self._mails(4)
        with self._mock_smtp():
            mails.send()
        self.assertEqual(len(self.emails), 4)
        queued = self._mails(4)
        with self._mock_smtp():
            self._run_queue(queued)
        self.assertEqual(len(self.emails), 4)
        self.assertEqual(set((mails | queued).mapped("state")), {"sent"})
        self.assertFalse(self._slots())

    # ------------------------------------------------------------------
    # (c) a temporary error is retried, only for the recipients not reached
    # ------------------------------------------------------------------

    def test_temporary_error_retries_only_unreached_recipients(self):
        partners = self.partners[:3]
        mail = self._notification_mail(partners)
        with freeze_time(NOW), self._mock_smtp(refuse=(2,)):
            self._run_queue(mail)
        self.assertEqual(len(self.smtp_attempts), 2)
        self.assertEqual(len(self.emails), 1)
        reached = partners.filtered(lambda p: self.emails[0]["smtp_to_list"] == [p.email])
        self.assertEqual(len(reached), 1)

        self.assertEqual(mail.state, "outgoing")
        self.assertFalse(mail.failure_type)
        self.assertEqual(mail.rate_limit_retries, 1)
        self.assertEqual(mail.scheduled_date, NOW + timedelta(minutes=6))
        self.assertEqual(mail.recipient_ids, partners - reached, "the reached recipient is not sent to again")
        notifications = self._notifications(mail)
        self.assertEqual(notifications.filtered(lambda n: n.res_partner_id == reached).notification_status, "sent")
        self.assertEqual(set((notifications - notifications.filtered(lambda n: n.res_partner_id == reached)).mapped("notification_status")), {"ready"})
        self.assertEqual(self._slots().mapped("count"), [3, 3], "the batch, then a full window as penalty")
        self.assertTrue(self._triggers(NOW + timedelta(minutes=6)))

        with freeze_time(NOW + timedelta(minutes=6)), self._mock_smtp():
            self._run_queue(mail)
        self.assertEqual(mail.state, "sent")
        self.assertEqual(sorted(email["smtp_to_list"][0] for email in self.emails), sorted((partners - reached).mapped("email")))
        self.assertEqual(set(self._notifications(mail).mapped("notification_status")), {"sent"})

    def test_temporary_error_stops_the_batch(self):
        mails = self._mails(3)
        with freeze_time(NOW), self._mock_smtp(refuse=(1,)):
            self._run_queue(mails)
        self.assertEqual(len(self.smtp_attempts), 1, "after a 4xx the rest of the batch is not tried")
        self.assertEqual(mails[0].state, "outgoing")
        self.assertEqual(mails[0].rate_limit_retries, 1)
        self.assertEqual(mails[0].scheduled_date, NOW + timedelta(minutes=6))
        self.assertEqual(mails[1:].mapped("state"), ["outgoing"] * 2)
        self.assertEqual(mails[1:].mapped("rate_limit_retries"), [0, 0])
        self.assertEqual(mails[1:].mapped("scheduled_date"), [NOW + WINDOW + MARGIN] * 2)

    def test_retry_max_then_exception(self):
        self.server.rate_limit_retry_max = 1
        mail = self._mails(1)
        with freeze_time(NOW), self._mock_smtp(refuse="all"):
            self._run_queue(mail)
        self.assertEqual((mail.state, mail.rate_limit_retries), ("outgoing", 1))
        with freeze_time(NOW + timedelta(minutes=6)), self._mock_smtp(refuse="all"):
            self._run_queue(mail)
        self.assertEqual(mail.state, "exception")
        self.assertEqual(mail.failure_type, "unknown")
        self.assertIn("451", mail.failure_reason)
        self.assertEqual(mail.rate_limit_retries, 1)

    def test_permanent_error_is_not_retried(self):
        mail = self._mails(1)
        with freeze_time(NOW), self._mock_smtp(refuse="all", answer=UNKNOWN_USER):
            self._run_queue(mail)
        self.assertEqual(mail.state, "exception")
        self.assertEqual(mail.rate_limit_retries, 0)

    def test_retry_by_hand_after_the_last_retry_reaches_nobody_twice(self):
        self.server.rate_limit_retry_max = 1
        partners = self.partners[:3]
        mail = self._notification_mail(partners)
        with freeze_time(NOW), self._mock_smtp(refuse=(2,)):
            self._run_queue(mail)
        first = partners.filtered(lambda p: [p.email] == self.emails[0]["smtp_to_list"])
        self.assertEqual((mail.state, mail.rate_limit_retries), ("outgoing", 1))

        # the last retry: one more recipient reached, then 451 again
        with freeze_time(NOW + timedelta(minutes=6)), self._mock_smtp(refuse=(2,)):
            self._run_queue(mail)
        second = (partners - first).filtered(lambda p: [p.email] == self.emails[0]["smtp_to_list"])
        self.assertEqual(len(second), 1)
        self.assertEqual(mail.state, "exception")
        self.assertEqual(mail.recipient_ids, partners - first - second, "the reached recipients are taken off")
        notifications = self._notifications(mail)
        self.assertEqual(notifications.filtered(lambda n: n.res_partner_id == second).notification_status, "sent")
        self.assertEqual(notifications.filtered(lambda n: n.res_partner_id == mail.recipient_ids).notification_status, "exception")

        # Retry by hand: only the recipient never reached gets it
        never_reached = partners - first - second
        mail.mark_outgoing()
        with freeze_time(NOW + timedelta(minutes=12)), self._mock_smtp():
            self._run_queue(mail)
        self.assertEqual([email["smtp_to_list"] for email in self.emails], [never_reached.mapped("email")])
        self.assertEqual(mail.state, "sent")
        self.assertEqual(set(self._notifications(mail).mapped("notification_status")), {"sent"})

    def test_retry_by_hand_resets_the_retries(self):
        mail = self._mails(1)
        mail.write({"state": "exception", "rate_limit_retries": 2})
        mail.mark_outgoing()
        self.assertEqual((mail.state, mail.rate_limit_retries), ("outgoing", 0))

    # ------------------------------------------------------------------
    # (d) a mail with more recipients than the limit is split
    # ------------------------------------------------------------------

    def test_mail_with_more_recipients_than_the_limit_is_split(self):
        partners = self.partners[:5]
        mail = self._notification_mail(partners)
        with freeze_time(NOW), self._mock_smtp():
            self._run_queue(mail)
        self.assertEqual(len(self.emails), 3)
        part = self.env["mail.mail"].search([("mail_message_id", "=", mail.mail_message_id.id)]) - mail
        self.assertEqual(len(part), 1)
        self.assertEqual(part.state, "sent")
        self.assertEqual(len(part.recipient_ids), 3)
        self.assertEqual(mail.state, "outgoing")
        self.assertEqual(mail.recipient_ids, partners - part.recipient_ids)
        self.assertEqual(mail.scheduled_date, NOW + WINDOW + MARGIN)
        notifications = self._notifications(mail)
        self.assertEqual(notifications.filtered(lambda n: n.mail_mail_id == part).res_partner_id, part.recipient_ids)
        self.assertEqual(set(notifications.filtered(lambda n: n.mail_mail_id == part).mapped("notification_status")), {"sent"})
        self.assertEqual(set(notifications.filtered(lambda n: n.mail_mail_id == mail).mapped("notification_status")), {"ready"})

        with freeze_time(NOW + WINDOW + MARGIN), self._mock_smtp():
            self._run_queue(mail)
        self.assertEqual(len(self.emails), 2)
        self.assertEqual(mail.state, "sent")

    def test_split_template_mail_keeps_the_rest(self):
        """A template mail owns its message; deleting the sent part must not delete the rest."""
        partners = self.partners[:5]
        mail = self._template_mail(partners)
        message = mail.mail_message_id
        with freeze_time(NOW), self._mock_smtp():
            self._run_queue(mail)
        self.assertEqual(len(self.emails), 3)
        self.assertTrue(mail.exists(), "the rest is still in the queue")
        self.assertTrue(message.exists())
        self.assertEqual(len(mail.recipient_ids), 2)
        self.assertEqual(mail.state, "outgoing")
        self.assertFalse(self.env["mail.mail"].search([("mail_message_id", "=", message.id)]) - mail,
                         "the sent part is deleted (auto_delete)")
        rest = sorted(mail.recipient_ids.mapped("email"))

        with freeze_time(NOW + WINDOW + MARGIN), self._mock_smtp():
            self._run_queue(mail)
        self.assertEqual(sorted(email["smtp_to_list"][0] for email in self.emails), rest)
        self.assertFalse(mail.exists())
        self.assertFalse(message.exists(), "the last mail on the message deletes it")

    def test_split_template_mail_rest_sent_before_the_retried_part(self):
        """The part gets a 451 and waits for its retry; the rest goes out first and is deleted. The
        part, which shares the message, must survive that."""
        partners = self.partners[:5]
        mail = self._template_mail(partners)
        message = mail.mail_message_id
        with freeze_time(NOW), self._mock_smtp(refuse=(2,)):
            self._run_queue(mail)
        part = self.env["mail.mail"].search([("mail_message_id", "=", message.id)]) - mail
        self.assertEqual(len(part), 1)
        self.assertEqual((part.state, len(part.recipient_ids)), ("outgoing", 2))
        self.assertEqual(part.scheduled_date, NOW + timedelta(minutes=6))
        self.assertEqual(mail.scheduled_date, NOW + WINDOW + MARGIN)

        with freeze_time(NOW + WINDOW + MARGIN), self._mock_smtp():
            self._run_queue(mail | part)
        self.assertEqual(len(self.emails), 2)
        self.assertFalse(mail.exists())
        self.assertTrue(part.exists(), "the part waiting for its retry is not deleted with the message")
        self.assertTrue(message.exists())

        left = sorted(part.recipient_ids.mapped("email"))
        with freeze_time(NOW + timedelta(minutes=11)), self._mock_smtp():
            self._run_queue(part)
        self.assertEqual(sorted(email["smtp_to_list"][0] for email in self.emails), left)
        self.assertFalse(part.exists())
        self.assertFalse(message.exists(), "the last mail on the message deletes it")

    # ------------------------------------------------------------------
    # (e) personal mail servers are left to core's own throttle
    # ------------------------------------------------------------------

    def test_personal_server_is_left_to_core(self):
        if self.env["ir.config_parameter"].sudo().get_param("mail.disable_personal_mail_servers"):
            self.skipTest("personal mail servers are disabled in this database")
        owner = new_test_user(self.env, login="rate_limit_owner", email=f"owner@{DOMAIN}")
        personal = self.env["ir.mail_server"].create({
            "name": "Rate limit personal server",
            "smtp_host": f"smtp.{DOMAIN}",
            "from_filter": owner.email,
            "owner_user_id": owner.id,
            "rate_limit_count": 1,
        })
        mails = self.env["mail.mail"].with_user(owner).sudo().create([{
            "subject": f"Personal {i}",
            "body_html": "<p>Test</p>",
            "email_from": owner.email,
            "email_to": f"to{i}@{DOMAIN}",
            "mail_server_id": personal.id,
        } for i in range(3)])
        for runner in (False, True):
            batches = list(mails.with_context(mail_rate_limit_runner=runner)._split_by_mail_configuration())
            self.assertEqual([(batch[0], sorted(batch[3])) for batch in batches], [(personal.id, sorted(mails.ids))])
        self.assertFalse(self._slots(personal))
        self.assertFalse(any(mails.mapped("scheduled_date")))

    # ------------------------------------------------------------------
    # Core contracts the overrides depend on (private methods of mail.mail, Odoo 19.0)
    # ------------------------------------------------------------------

    def test_core_contracts(self):
        core = next(cls for cls in type(self.env["mail.mail"]).__mro__
                    if cls.__module__ == "odoo.addons.mail.models.mail_mail")
        self.assertTrue(inspect.isgeneratorfunction(core._split_by_mail_configuration))
        self.assertEqual(list(inspect.signature(core.process_email_queue).parameters),
                         ["self", "email_ids", "batch_size"])
        self.assertEqual(list(inspect.signature(core._postprocess_sent_message).parameters),
                         ["self", "success_pids", "success_emails", "failure_reason", "failure_type"])
        self.assertEqual(list(inspect.signature(core._send).parameters),
                         ["self", "auto_commit", "raise_exception", "smtp_session", "alias_domain_id",
                          "mail_server", "post_send_callback"])
        # the generator yields (mail_server_id, alias_domain_id, smtp_from, batch_ids)
        self.server.rate_limit_count = 0
        mails = self._mails(2)
        batches = list(mails._split_by_mail_configuration())
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 4)
        self.assertEqual(batches[0][0], self.server.id)
        self.assertEqual(batches[0][2], SENDER)
        self.assertEqual(sorted(batches[0][3]), sorted(mails.ids))

    # ------------------------------------------------------------------
    # Settings and housekeeping
    # ------------------------------------------------------------------

    def test_settings_are_checked(self):
        with self.assertRaises(ValidationError):
            self.server.rate_limit_count = -1
        with self.assertRaises(ValidationError):
            self.server.rate_limit_minutes = 0

    def test_old_slots_are_vacuumed(self):
        Slot = self.env["mail.server.rate.slot"]
        with freeze_time(NOW):
            old = Slot.create({"server_id": self.server.id, "at": NOW - timedelta(days=2), "count": 1})
            recent = Slot.create({"server_id": self.server.id, "at": NOW - timedelta(minutes=2), "count": 2})
            Slot._gc_rate_slots()
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())
            self.server.invalidate_recordset(["rate_limit_used"])
            self.assertEqual(self.server.rate_limit_used, 2)
