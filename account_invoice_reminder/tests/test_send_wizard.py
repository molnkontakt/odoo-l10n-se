from datetime import timedelta

from odoo import Command, fields
from odoo.tests import Form, TransactionCase, tagged
from odoo.tools import formatLang


@tagged("post_install", "-at_install")
class TestSendWizard(TransactionCase):
    """The review dialog: one selectable row per customer and level with the fee once per reminder, and one
    row per invoice with dates, days overdue and what is left to pay. Fictive customers and invoices in a
    company of its own."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Reminder wizard test"})
        cls.env["account.chart.template"].try_loading("generic_coa", company=cls.company, install_demo=False)
        # a language, as every request has one: _() in the wizard warns without it
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id], lang="en_US"))
        cls.today = fields.Date.context_today(cls.env["account.reminder"])
        income = cls.env["account.account"].search(
            [("company_ids", "in", cls.company.id), ("account_type", "=", "income")], limit=1)
        Level = cls.env["account.reminder.level"]
        cls.level1 = Level.create({"name": "Påminnelse", "sequence": 10, "days": 14, "fee_amount": 60,
                                   "fee_account_id": income.id, "company_id": cls.company.id})
        cls.level2 = Level.create({"name": "Inkassokrav", "sequence": 20, "days": 30, "fee_amount": 180,
                                   "fee_account_id": income.id, "company_id": cls.company.id})
        Partner = cls.env["res.partner"]
        cls.partner_a = Partner.create({"name": "Exempelbolaget AB", "email": "ekonomi@example.com"})
        cls.partner_b = Partner.create({"name": "Annat Exempel AB", "email": "faktura@example.com"})

    @classmethod
    def _invoice(cls, partner, days_overdue, amount):
        """A posted customer invoice that fell due ``days_overdue`` days ago."""
        due = cls.today - timedelta(days=days_overdue)
        move = cls.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": partner.id, "company_id": cls.company.id,
            "invoice_date": due - timedelta(days=30), "invoice_date_due": due,
            "invoice_line_ids": [Command.create({"name": "Tjänst", "quantity": 1, "price_unit": amount, "tax_ids": []})],
        })
        move.action_post()
        return move

    def _wizard(self, moves):
        return self.env["account.reminder.send.wizard"].with_context(
            active_model="account.move", active_ids=moves.ids).create({})

    def _fmt(self, amount):
        return formatLang(self.env, amount, digits=self.company.currency_id.decimal_places)

    def test_one_row_per_reminder_and_one_per_invoice(self):
        a1 = self._invoice(self.partner_a, 40, 1000)
        a2 = self._invoice(self.partner_a, 20, 250)
        b1 = self._invoice(self.partner_b, 30, 500)
        wizard = self._wizard(a1 | a2 | b1)

        self.assertEqual(len(wizard.line_ids), 2, "one reminder per customer and level")
        line_b, line_a = wizard.line_ids  # ordered by customer name: Annat Exempel AB, Exempelbolaget AB
        self.assertEqual((line_a.partner_id, line_a.level_id, line_a.move_ids), (self.partner_a, self.level1, a1 | a2))
        self.assertEqual((line_b.partner_id, line_b.move_ids), (self.partner_b, b1))
        self.assertEqual((line_a.move_count, line_b.move_count), (2, 1))
        self.assertEqual((line_a.amount_overdue, line_b.amount_overdue), (1250, 500))
        self.assertEqual((line_a.fee_amount, line_b.fee_amount), (60, 60), "one fee per reminder, not per invoice")
        self.assertEqual(line_a.fee_basis, "1 avgift per påminnelse")

        self.assertEqual(len(wizard.invoice_line_ids), 3, "one row per invoice")
        self.assertEqual(wizard.invoice_line_ids.mapped("move_id"), b1 | a1 | a2,
                         "customer order, then due date (oldest first)")
        self.assertEqual(wizard.invoice_line_ids.mapped("days_overdue"), [30, 40, 20])
        self.assertEqual(wizard.invoice_line_ids.mapped("amount_residual"), [500, 1000, 250])
        self.assertEqual(wizard.invoice_line_ids.mapped("partner_id"), self.partner_b | self.partner_a)
        self.assertEqual(wizard.invoice_line_ids.mapped("level_id"), self.level1)
        row = wizard.invoice_line_ids[1]
        self.assertEqual((row.name, row.invoice_date, row.invoice_date_due),
                         (a1.name, a1.invoice_date, a1.invoice_date_due))
        self.assertTrue(all(wizard.invoice_line_ids.mapped("selected")))

    def test_days_overdue_counts_from_due_date(self):
        inv = self._invoice(self.partner_a, 15, 100)
        wizard = self._wizard(inv)
        self.assertEqual(wizard.invoice_line_ids.days_overdue, (self.today - inv.invoice_date_due).days)
        self.assertEqual(wizard.invoice_line_ids.days_overdue, 15)

    def test_fee_note_names_the_account_and_the_button(self):
        wizard = self._wizard(self._invoice(self.partner_a, 20, 100))
        self.assertIn("ingen ny faktura skapas", wizard.fee_note)
        self.assertIn(self.level1.fee_account_id.display_name, wizard.fee_note)
        self.assertIn('"Påminnelseavgift (Påminnelse)"', wizard.fee_note)
        self.assertIn("per påminnelse", wizard.fee_note)

    def test_previous_fee_is_carried_over_and_shown_as_basis(self):
        inv = self._invoice(self.partner_a, 40, 1000)
        Reminder = self.env["account.reminder"]
        first = Reminder.create_for(self.partner_a, self.level1, inv)
        first._mark_sent()
        self.assertEqual(inv.reminder_level_id, self.level1)

        wizard = self._wizard(inv)
        line = wizard.line_ids
        self.assertEqual(line.level_id, self.level2, "40 days overdue and already reminded once: next level")
        self.assertEqual(line.fee_amount, 240, "180 for this demand plus the unpaid 60 from the first reminder")
        self.assertEqual(line.fee_basis, f"{self._fmt(180)} + {self._fmt(60)} tidigare")
        self.assertEqual(wizard.invoice_line_ids.level_id, self.level2)

        wizard.only_prepare = True
        wizard.action_send()
        second = Reminder.search([("partner_id", "=", self.partner_a.id), ("level_id", "=", self.level2.id)])
        self.assertEqual(len(second), 1)
        self.assertEqual((second.fee_amount, second.previous_fee_amount), (180, 60))
        self.assertEqual(second.amount_total, 1000 + 180 + 60)

    def test_deselecting_a_customer_creates_only_the_other_reminder(self):
        a1 = self._invoice(self.partner_a, 40, 1000)
        a2 = self._invoice(self.partner_a, 20, 250)
        b1 = self._invoice(self.partner_b, 30, 500)
        wizard = self._wizard(a1 | a2 | b1)
        wizard.line_ids.filtered(lambda line: line.partner_id == self.partner_b).selected = False
        self.assertEqual(wizard.count, 1)
        self.assertEqual([(r.move_id, r.selected) for r in wizard.invoice_line_ids],
                         [(b1, False), (a1, True), (a2, True)], "the invoice rows follow the customer's toggle")

        wizard.only_prepare = True
        action = wizard.action_send()
        reminders = self.env["account.reminder"].search(action["domain"])
        self.assertEqual(len(reminders), 1, "one reminder: the selected customer")
        self.assertEqual((reminders.partner_id, reminders.move_ids, reminders.state), (self.partner_a, a1 | a2, "draft"))
        self.assertEqual(reminders.move_count, 2)
        self.assertEqual(b1.reminder_count, 0)
        self.assertFalse(b1.reminder_ids)

    def test_dialog_rows_follow_the_toggle_before_saving(self):
        """The dialog as the client sees it (new record, onchange protocol): both lists are filled from the
        defaults, and toggling a customer's row updates its invoice rows' selected without a save."""
        a1 = self._invoice(self.partner_a, 40, 1000)
        a2 = self._invoice(self.partner_a, 20, 250)
        b1 = self._invoice(self.partner_b, 30, 500)
        Wizard = self.env["account.reminder.send.wizard"].with_context(
            active_model="account.move", active_ids=(a1 | a2 | b1).ids)
        with Form(Wizard) as form:
            self.assertEqual(len(form.line_ids), 2)
            self.assertEqual(len(form.invoice_line_ids), 3)
            self.assertEqual([r["selected"] for r in form.invoice_line_ids._records], [True, True, True])
            self.assertIn("ingen ny faktura skapas", form.fee_note)
            with form.line_ids.edit(0) as row:
                self.assertEqual(row.partner_id, self.partner_b)
                row.selected = False
            self.assertEqual([r["selected"] for r in form.invoice_line_ids._records], [False, True, True])
            self.assertEqual(form.count, 1)
        wizard = form.record
        self.assertEqual(wizard.line_ids.filtered("selected").move_ids, a1 | a2)
        self.assertEqual(wizard.invoice_line_ids.mapped("move_id"), b1 | a1 | a2)

    def test_not_overdue_invoice_is_listed_as_skipped(self):
        due_tomorrow = self._invoice(self.partner_a, -1, 100)
        overdue = self._invoice(self.partner_a, 20, 100)
        wizard = self._wizard(due_tomorrow | overdue)
        self.assertEqual(wizard.line_ids.move_ids, overdue)
        self.assertEqual(wizard.invoice_line_ids.move_id, overdue)
        self.assertIn(f"{due_tomorrow.name}: inte förfallen", wizard.skipped)

