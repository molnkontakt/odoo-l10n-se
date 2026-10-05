from psycopg2 import IntegrityError

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestLevelConstraint(TransactionCase):
    """A reminder level cannot have negative days. The check was declared with ``_sql_constraints``, which Odoo 19
    ignores (only a warning in the log), so the database never got it."""

    def test_negative_days_rejected(self):
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.env["account.reminder.level"].create({"name": "Negative", "days": -1})
            self.env.flush_all()

    def test_zero_days_allowed(self):
        level = self.env["account.reminder.level"].create({"name": "Same day", "days": 0})
        self.env.flush_all()
        self.assertEqual(level.days, 0)
