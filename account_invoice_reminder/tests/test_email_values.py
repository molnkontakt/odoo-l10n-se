from unittest import mock

from odoo import Command
from odoo.addons.mail.tests.common import MockEmail
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestEmailValues(TransactionCase, MockEmail):
    """_email_values is the hook for what the reminder e-mail carries on top of the template; a module can
    change the recipients there. The e-mail is handed to mail.mail.send() at once (force_send).

    The assertions are on the mail.mail the template creates and on the call to send(), not on SMTP: what
    send() does with it depends on the database's outgoing mail servers (a rate-limited server keeps it in the
    queue for the cron), and the test must not depend on them. send() is mocked, so nothing is sent."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Reminder e-mail test", "email": "billing@example.com"})
        cls.customer = cls.env["res.partner"].create({"name": "Reminder Customer", "email": "customer@example.com"})
        cls.other = cls.env["res.partner"].create({"name": "Other Recipient", "email": "other@example.com"})
        cls.level = cls.env["account.reminder.level"].create({"name": "Reminder 1", "company_id": cls.company.id})
        cls.reminder = cls.env["account.reminder"].create({
            "company_id": cls.company.id, "partner_id": cls.customer.id, "level_id": cls.level.id})

    def _send_email(self):
        """reminder._send_email() with mail.mail.send mocked; returns (created mails, send mock)."""
        MailMail = type(self.env["mail.mail"])
        with self.mock_mail_gateway(), mock.patch.object(MailMail, "send", autospec=True) as send:
            self.reminder._send_email()
        return self._new_mails, send

    def test_default_values_attach_the_invoices(self):
        attachment = self.env["ir.attachment"].create({"name": "invoice.pdf", "raw": b"%PDF-1.4"})
        self.assertEqual(self.reminder._email_values(attachment), {"attachment_ids": [Command.link(attachment.id)]})
        self.assertEqual(self.reminder._email_values(self.env["ir.attachment"]), {"attachment_ids": []})

    def test_template_recipient_by_default(self):
        self.assertTrue(self.level.mail_template_id, "the level gets the shipped template")
        mails, send = self._send_email()
        self.assertEqual(len(mails), 1)
        self.assertEqual(mails.recipient_ids, self.customer)
        self.assertFalse(mails.email_to)
        self.assertEqual(send.call_count, 1, "handed to send() at once (force_send), not left for the queue cron")
        self.assertEqual(send.call_args.args[0], mails)
        self.assertFalse(self._mails, "send() is mocked: nothing reaches SMTP")

    def test_override_replaces_the_recipients(self):
        Reminder = type(self.env["account.reminder"])
        original = Reminder._email_values
        other = self.other

        def email_values(reminder, attachments):
            return dict(original(reminder, attachments), recipient_ids=[Command.set(other.ids)])

        with mock.patch.object(Reminder, "_email_values", new=email_values):
            mails, _send = self._send_email()
        self.assertEqual(mails.recipient_ids, self.other,
                         "email_values are applied after partner_to, so the customer is not a recipient")


@tagged("post_install", "-at_install")
class TestCheckBeforeSending(TransactionCase):
    """action_send checks every reminder before sending any: an SMS or a letter already sent cannot be undone
    when a later reminder of the same run fails and the transaction is rolled back."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Reminder check test"})
        cls.level = cls.env["account.reminder.level"].create({"name": "Reminder 1", "company_id": cls.company.id})
        Partner = cls.env["res.partner"]
        cls.with_email = Partner.create({"name": "Has e-mail", "email": "has@example.com"})
        cls.without_email = Partner.create({"name": "No e-mail"})

    def _reminder(self, partner, channel):
        return self.env["account.reminder"].create({
            "company_id": self.company.id, "partner_id": partner.id, "level_id": self.level.id, "channel": channel})

    def test_check_send(self):
        self.assertEqual(self._reminder(self.with_email, "email")._check_send(), "")
        self.assertEqual(self._reminder(self.without_email, "manual")._check_send(), "")
        self.assertTrue(self._reminder(self.without_email, "email")._check_send())

    def test_nothing_is_sent_when_one_reminder_cannot_be(self):
        first = self._reminder(self.without_email, "manual")
        second = self._reminder(self.without_email, "email")
        Reminder = type(self.env["account.reminder"])
        with mock.patch.object(Reminder, "_send", autospec=True) as send, self.assertRaises(UserError) as caught:
            (first | second).action_send()
        send.assert_not_called()
        self.assertIn(self.without_email.name, str(caught.exception))

    def test_all_sent_when_all_can_be(self):
        reminders = self._reminder(self.without_email, "manual") | self._reminder(self.with_email, "email")
        Reminder = type(self.env["account.reminder"])
        with mock.patch.object(Reminder, "_send", autospec=True) as send:
            reminders.action_send()
        self.assertEqual(send.call_count, 2)
        self.assertEqual(set(reminders.mapped("state")), {"sent"})
