from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestCompanyRules(TransactionCase):
    """A reminder and a reminder level belong to one company and are only seen with that company selected: the
    list showed another company's reminder, and reading its invoices ended in an access error."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env["res.company"]
        cls.company_a = Company.create({"name": "Reminder test A"})
        cls.company_b = Company.create({"name": "Reminder test B"})
        cls.user = new_test_user(
            cls.env, login="reminder_rules", groups="account.group_account_invoice",
            company_id=cls.company_a.id, company_ids=[Command.set((cls.company_a | cls.company_b).ids)])
        cls.partner = cls.env["res.partner"].create({"name": "Customer"})
        Level = cls.env["account.reminder.level"]
        cls.level_a = Level.create({"name": "Reminder A", "company_id": cls.company_a.id})
        cls.level_b = Level.create({"name": "Reminder B", "company_id": cls.company_b.id})
        Reminder = cls.env["account.reminder"]
        cls.reminder_a = Reminder.create({"company_id": cls.company_a.id, "partner_id": cls.partner.id,
                                          "level_id": cls.level_a.id})
        cls.reminder_b = Reminder.create({"company_id": cls.company_b.id, "partner_id": cls.partner.id,
                                          "level_id": cls.level_b.id})

    def _as_user(self, *companies):
        return self.env(user=self.user, context={"allowed_company_ids": [c.id for c in companies]})

    def test_only_the_selected_companies(self):
        user_env = self._as_user(self.company_a)
        reminders = self.reminder_a | self.reminder_b
        self.assertEqual(user_env["account.reminder"].search([("id", "in", reminders.ids)]).ids, self.reminder_a.ids)
        levels = self.level_a | self.level_b
        self.assertEqual(user_env["account.reminder.level"].search([("id", "in", levels.ids)]).ids, self.level_a.ids)

    def test_both_companies_selected(self):
        user_env = self._as_user(self.company_a, self.company_b)
        reminders = self.reminder_a | self.reminder_b
        self.assertEqual(len(user_env["account.reminder"].search([("id", "in", reminders.ids)])), 2)
