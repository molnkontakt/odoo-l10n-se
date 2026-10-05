# The SIE files in tests/data are synthetic: an invented company and organisation number.
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from odoo import Command
from odoo.addons.l10n_se_sie4.lib import sie
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user
from odoo.tools import mute_logger

from .common import DATA, SieCase


@tagged("post_install", "-at_install", "l10n_se")
class TestSieImport(SieCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.f2024 = sie.parse((DATA / "fortnox_2024.se").read_bytes())
        cls.f2025 = sie.parse((DATA / "fortnox_2025.se").read_bytes())

    # -- preview ---------------------------------------------------------------------------------

    def test_preview(self):
        wizard = self._wizard("fortnox_2025.se")
        self.assertEqual(wizard.state, "preview")
        self.assertEqual(wizard.year_ids.mapped("label"), ["2025"])
        self.assertEqual(wizard.series_ids.mapped("series"), ["A", "B"])
        self.assertTrue(wizard.import_analytics, "the file has objects on its lines")
        self.assertFalse(wizard.blocked)
        self.assertEqual(wizard.entry_count, 8, "the opening balance and seven vouchers")
        html = str(wizard.preview_html)
        for text in ("Fortnox 3.57.11", "559000-0000", "Exempelbolaget Norrsken AB", "3001",
                     "Kostnadsställe", "Projekt", "cp437"):
            self.assertIn(text, html)
        self.assertNotIn("4010", html, "4010 is only used in 2024")

    def test_preview_reports_problems_with_lines(self):
        wizard = self._wizard("broken.se")
        self.assertTrue(wizard.blocked)
        html = str(wizard.preview_html)
        self.assertIn("broken.se, line 18", html)
        self.assertIn("broken.se, line 10", html)
        with self.assertRaises(UserError):
            wizard.action_import()

    def test_not_an_sie_file(self):
        with self.assertRaises(UserError):
            self._wizard(files=[("notes.txt", b"hello")])

    # -- import ----------------------------------------------------------------------------------

    def test_import_year(self):
        record = self._import("fortnox_2025.se")
        self.assertEqual(record.state, "done")
        self.assertEqual(record.mode, "import")
        moves = record.move_ids
        self.assertEqual(len(moves), 8)
        self.assertEqual(set(moves.mapped("state")), {"posted"})
        self.assertEqual(set(moves.mapped("move_type")), {"entry"})
        self.assertEqual(moves.journal_id.code, "SIE")
        self.assertFalse(moves.journal_id.restrict_mode_hash_table)
        sale = moves.filtered(lambda m: m.l10n_se_sie_key == "2025|A|1")
        self.assertEqual(sale.ref, "SIE 2025 A1 — Försäljning, Åtta kunder")
        self.assertEqual(sale.date, date(2025, 1, 10))
        opening = moves.filtered(lambda m: m.l10n_se_sie_key == "2025|IB|")
        self.assertEqual(opening.date, date(2025, 1, 1))
        self.assertFalse(moves.line_ids.tax_ids, "SIE carries no tax codes")
        # every account agrees with #UB/#RES
        self.assertEqual(record.deviation_count, 0)
        self.assertEqual(record.checked_count, 12)
        self.assertEqual(record.year_ids.status, "ok")
        self.assertEqual(record.year_ids.source, "closing")
        balances = self._balances(self.company, date(2025, 12, 31))
        for code, amount in self.f2025.amounts("closing").items():
            self.assertEqual(balances.get(code, Decimal(0)), amount, code)
        results = self._balances(self.company, date(2025, 12, 31), date(2025, 1, 1))
        for code, amount in self.f2025.amounts("results").items():
            self.assertEqual(results.get(code, Decimal(0)), amount, code)

    def test_rtrans_and_btrans(self):
        """#RTRANS is followed by an identical #TRANS and is booked once; #BTRANS not at all."""
        record = self._import("fortnox_2025.se")
        porto = record.move_ids.filtered(lambda m: m.l10n_se_sie_key == "2025|A|2")
        self.assertEqual(len(porto.line_ids), 6)
        bank = porto.line_ids.filtered(lambda line: line.account_id.code == "1930")
        self.assertEqual(sum(bank.mapped("balance")), -1000.0)
        payment = record.move_ids.filtered(lambda m: m.l10n_se_sie_key == "2025|A|3")
        self.assertEqual(sorted(payment.line_ids.mapped("balance")), [-25000.0, 25000.0])

    def test_missing_accounts_created(self):
        self.assertFalse(self._account("3001"))
        record = self._import("fortnox_2025.se")
        account = self._account("3001")
        self.assertEqual(account.name, "Försäljning inom Sverige, 25 % moms")
        self.assertEqual(account.account_type, "income")
        self.assertIn(account, record.created_account_ids)

    def test_missing_account_types(self):
        """Accounts missing in Odoo's basic Swedish chart get the type of their neighbours."""
        record = self._import("fortnox_2024.se")
        self.assertEqual(sorted(record.created_account_ids.mapped("code")),
                         ["2731", "3001", "4010", "6110", "7510"])
        self.assertEqual(
            {a.code: a.account_type for a in record.created_account_ids},
            {"2731": "liability_current", "3001": "income", "4010": "expense_direct_cost",
             "6110": "expense", "7510": "expense"})

    def test_missing_accounts_abort(self):
        wizard = self._wizard("fortnox_2025.se", missing_accounts="abort")
        self.assertTrue(wizard.blocked)
        self.assertIn("3001", str(wizard.preview_html))
        with self.assertRaises(UserError):
            wizard.action_import()
        self.assertFalse(self._account("3001"))

    def test_analytics(self):
        record = self._import("fortnox_2025.se")
        plans = self.env["account.analytic.plan"].sudo().search([("l10n_se_sie_dimension", "in", (1, 6))])
        self.assertEqual(sorted(plans.mapped("name")), ["Kostnadsställe", "Projekt"])
        store = self.env["account.analytic.account"].sudo().search([("code", "=", "10"),
                                                             ("plan_id", "in", plans.ids)])
        self.assertEqual(store.name, "Butik Östra")
        sale = record.move_ids.filtered(lambda m: m.l10n_se_sie_key == "2025|A|1")
        line = sale.line_ids.filtered(lambda ln: ln.account_id.code == "3001")
        project = self.env["account.analytic.account"].sudo().search([("code", "=", "P1")])
        key = ",".join(str(i) for i in sorted((store | project).ids))
        self.assertEqual(line.analytic_distribution, {key: 100})
        self.assertFalse(sale.line_ids.filtered(lambda ln: ln.account_id.code == "1510")
                         .analytic_distribution)

    def test_without_analytics(self):
        record = self._import("fortnox_2025.se", import_analytics=False)
        self.assertFalse(record.move_ids.line_ids.filtered("analytic_distribution"))
        self.assertFalse(record.created_analytic_ids)

    def test_idempotent(self):
        first = self._import("fortnox_2025.se")
        wizard = self._wizard("fortnox_2025.se")
        self.assertEqual(wizard.entry_count, 0)
        self.assertIn("already imported", str(wizard.preview_html))
        with self.assertRaises(UserError):
            wizard.action_import()
        self.assertEqual(self.env["account.move"].search_count(
            [("l10n_se_sie_key", "!=", False), ("company_id", "=", self.company.id)]), 8)
        self.assertEqual(len(first.move_ids), 8)

    def test_idempotent_with_earlier_script_references(self):
        """Entries of an earlier import script (reference 'SIE 2025 A1 — text', no key) are
        recognised."""
        self._entry(date(2025, 1, 10), [("1510", 25000.0), ("2611", -25000.0)],
                    ref="SIE 2025 A1 — Försäljning, Åtta kunder")
        wizard = self._wizard("fortnox_2025.se")
        self.assertEqual(wizard.entry_count, 7)

    def test_two_years(self):
        record = self._import("fortnox_2024.se", "fortnox_2025.se")
        self.assertEqual(record.state, "done")
        self.assertEqual(record.year_ids.mapped("label"), ["2024", "2025"])
        keys = record.move_ids.mapped("l10n_se_sie_key")
        self.assertIn("2024|IB|", keys)
        self.assertNotIn("2025|IB|", keys, "only the first year gets an opening balance")
        self.assertEqual(len(record.move_ids), 1 + 13 + 7)
        self.assertEqual(record.deviation_count, 0)
        self.assertEqual(record.year_ids.mapped("status"), ["ok", "ok"])
        closing = record.move_ids.filtered(lambda m: m.l10n_se_sie_key == "2024|J|1")
        self.assertEqual(sorted(closing.line_ids.account_id.mapped("code")), ["2099", "8999"])

    def test_one_year_at_a_time(self):
        self._import("fortnox_2024.se")
        wizard = self._wizard("fortnox_2025.se", import_opening=False)
        self.assertFalse(wizard.blocked)
        self.assertNotIn("differs from the closing balance", str(wizard.preview_html))
        record = self.env["l10n_se.sie.import"].browse(wizard.action_import()["res_id"])
        self.assertEqual(record.deviation_count, 0)

    def test_later_year_without_its_opening_balance(self):
        """Without the year before in Odoo, the vouchers alone do not give the opening balance."""
        wizard = self._wizard("fortnox_2025.se", import_opening=False)
        self.assertIn("differs from the closing balance Odoo before 2025-01-01", str(
            wizard.preview_html).replace("&#39;", "'"))
        wizard.opening_differences = True
        record = self.env["l10n_se.sie.import"].browse(wizard.action_import()["res_id"])
        self.assertTrue(record.move_ids.filtered(lambda m: m.l10n_se_sie_key == "2025|IBDIFF|"))
        self.assertEqual(record.deviation_count, 0)

    def test_same_year_twice_is_refused(self):
        data = self._data("fortnox_2025.se")
        wizard = self._wizard(files=[("a.se", data), ("b.se", data)])
        self.assertTrue(wizard.blocked)
        self.assertIn("two files", str(wizard.preview_html))

    def test_opening_balance_differences(self):
        """A program that moved the result into retained earnings in the next year's opening
        balance: the difference can be booked."""
        data = self._data("fortnox_2025.se").replace(
            b"#IB 0 2091 -100000.00", b"#IB 0 2091 -55719.50").replace(
            b"#IB 0 2099 44280.50", b"").replace(
            b"#TRANS 2091 {} 44280.50", b"#TRANS 2091 {} 0.00").replace(
            b"#TRANS 2099 {} -44280.50", b"#TRANS 2099 {} 0.00")
        files = [("fortnox_2024.se", self._data("fortnox_2024.se")), ("moved.se", data)]
        wizard = self._wizard(files=files)
        self.assertIn("differs from the closing balance", str(wizard.preview_html))
        wizard.opening_differences = True
        record = self.env["l10n_se.sie.import"].browse(wizard.action_import()["res_id"])
        diff = record.move_ids.filtered(lambda m: m.l10n_se_sie_key == "2025|IBDIFF|")
        self.assertEqual(diff.date, date(2025, 1, 1))
        self.assertEqual(sorted((ln.account_id.code, ln.balance) for ln in diff.line_ids),
                         [("2091", 44280.5), ("2099", -44280.5)])

    def test_opening_differences_need_a_closed_year(self):
        """Without the year-end closing of 2024 the differences do not balance: refused."""
        data_2024 = self._data("fortnox_2024.se")
        start = data_2024.index(b"#VER J 1")
        data_2024 = data_2024[:start] + data_2024[data_2024.index(b"\r\n}\r\n", start) + 5:]
        self._import(files=[("open_2024.se", data_2024)])
        wizard = self._wizard("fortnox_2025.se", import_opening=False)
        self.assertIn("2099 (44280.50)", str(wizard.preview_html))
        self.assertFalse(wizard.blocked)
        wizard.opening_differences = True
        self.assertTrue(wizard.blocked)
        self.assertIn("year-end closing", str(wizard.preview_html))

    def test_unbalanced_voucher(self):
        wizard = self._wizard("unbalanced.se")
        self.assertTrue(wizard.blocked)
        self.assertIn("unbalanced.se, line 16", str(wizard.preview_html))
        with self.assertRaises(UserError):
            wizard.action_import()
        wizard.skip_invalid = True
        self.assertFalse(wizard.blocked)
        record = self.env["l10n_se.sie.import"].browse(wizard.action_import()["res_id"])
        self.assertEqual(record.move_ids.mapped("l10n_se_sie_key"), ["2025|A|1"])

    def test_import_file_without_years(self):
        """A 4I file without #RAR and without numbers: the company's financial year, a key from
        the content, and a second import is still recognised."""
        record = self._import("kassa.si")
        self.assertEqual(len(record.move_ids), 2)
        self.assertEqual(record.year_ids.label, "2025")
        self.assertEqual(record.year_ids.status, "nobalances")
        self.assertTrue(all("|#" in k for k in record.move_ids.mapped("l10n_se_sie_key")))
        self.assertEqual(self._wizard("kassa.si").entry_count, 0)

    def test_drafts(self):
        record = self._import("fortnox_2025.se", post_moves=False)
        self.assertEqual(set(record.move_ids.mapped("state")), {"draft"})
        self.assertEqual(record.deviation_count, 0, "the check counts the drafts")

    def test_journal_per_series(self):
        journal_a = self.env["account.journal"].create({
            "name": "Huvudbok A", "code": "HBA", "type": "general", "company_id": self.company.id})
        wizard = self._wizard("fortnox_2025.se", journal_mode="series")
        wizard.series_ids.filtered(lambda s: s.series == "A").journal_id = journal_a
        record = self.env["l10n_se.sie.import"].browse(wizard.action_import()["res_id"])
        series_a = record.move_ids.filtered(lambda m: "|A|" in m.l10n_se_sie_key)
        series_b = record.move_ids.filtered(lambda m: "|B|" in m.l10n_se_sie_key)
        self.assertEqual(series_a.journal_id, journal_a)
        self.assertEqual(series_b.journal_id.name, "SIE series B")
        self.assertIn(series_b.journal_id, record.created_journal_ids)
        opening = record.move_ids.filtered(lambda m: m.l10n_se_sie_key == "2025|IB|")
        self.assertEqual(opening.journal_id.code, "SIE")

    def test_orgnr_mismatch_is_a_warning(self):
        self.company.sudo().company_registry = "556000-0000"
        wizard = self._wizard("fortnox_2025.se")
        self.assertIn("not the company", str(wizard.preview_html))
        self.assertFalse(wizard.blocked)
        self.company.sudo().company_registry = "5590000000"
        self.assertNotIn("not the company", str(self._wizard("fortnox_2025.se").preview_html))

    def test_other_currency_is_refused(self):
        data = self._data("fortnox_2025.se").replace(b"#VALUTA SEK", b"#VALUTA EUR")
        wizard = self._wizard(files=[("eur.se", data)])
        self.assertTrue(wizard.blocked)

    def test_utf8_file(self):
        record = self._import("utf8_bom.se")
        self.assertEqual(record.move_ids.filtered(
            lambda m: m.l10n_se_sie_key == "2025|A|1").ref, "SIE 2025 A1 — Frimärken åäö")

    def test_checksum_mismatch_is_a_warning(self):
        data = sie.write(self.f2025, checksum_records=True).replace(
            b"#TRANS 6570 {} 95.00", b"#TRANS 6570 {} 96.00").replace(
            b"#TRANS 1930 {} -95.00", b"#TRANS 1930 {} -96.00")
        wizard = self._wizard(files=[("ksumma.se", data)])
        self.assertIn("checksum", str(wizard.preview_html))
        self.assertFalse(wizard.blocked)

    # -- lock dates ------------------------------------------------------------------------------

    def test_lock_date_stops_the_import(self):
        self.company.sudo().fiscalyear_lock_date = date(2025, 3, 31)
        wizard = self._wizard("fortnox_2025.se")
        self.assertTrue(wizard.blocked)
        self.assertIn("locked period", str(wizard.preview_html))
        with self.assertRaises(UserError):
            wizard.action_import()
        self.assertFalse(self.env["account.move"].search_count(
            [("l10n_se_sie_key", "!=", False)]))

    def test_hard_lock_date_stops_the_import(self):
        self.company.sudo().hard_lock_date = date(2024, 12, 31)
        wizard = self._wizard("fortnox_2024.se")
        self.assertTrue(wizard.blocked)
        self.assertFalse(self._wizard("fortnox_2025.se").blocked)

    def test_tax_lock_date_is_a_warning(self):
        self.company.sudo().tax_lock_date = date(2025, 3, 31)
        wizard = self._wizard("fortnox_2025.se")
        self.assertFalse(wizard.blocked)
        self.assertIn("tax return lock date", str(wizard.preview_html))

    # -- undo ------------------------------------------------------------------------------------

    def test_undo(self):
        record = self._import("fortnox_2025.se")
        moves = record.move_ids
        record.action_undo()
        self.assertEqual(record.state, "undone")
        self.assertFalse(moves.exists())
        self.assertFalse(self._account("3001"), "the created account is removed again")
        self.assertFalse(record.check_ids)
        again = self._import("fortnox_2025.se")
        self.assertEqual(len(again.move_ids), 8)

    def test_undo_refused_when_locked(self):
        record = self._import("fortnox_2025.se")
        self.company.sudo().fiscalyear_lock_date = date(2025, 1, 31)
        with self.assertRaises(UserError):
            record.action_undo()
        self.assertEqual(len(record.move_ids), 8)
        self.assertEqual(record.state, "done")

    def test_large_undo_runs_in_the_background(self):
        record = self._import("fortnox_2025.se")
        moves = record.move_ids
        with patch("odoo.addons.l10n_se_sie4.models.l10n_se_sie_import.UNDO_SYNC_LIMIT", 3), \
                patch("odoo.addons.l10n_se_sie4.models.l10n_se_sie_import.UNDO_BATCH_SIZE", 3):
            record.action_undo()
            self.assertEqual(record.state, "undoing")
            Import = self.env["l10n_se.sie.import"].sudo()
            for _i in range(5):
                Import._cron_process()
        self.assertEqual(record.state, "undone")
        self.assertFalse(moves.exists())
        self.assertEqual(record.undone_count, 8)
        self.assertFalse(self._account("3001"))

    def test_record_with_entries_cannot_be_deleted(self):
        record = self._import("fortnox_2025.se")
        with self.assertRaises(UserError):
            record.unlink()
        record.action_undo()
        record.unlink()

    # -- background ------------------------------------------------------------------------------

    def test_large_import_runs_in_the_background(self):
        with patch("odoo.addons.l10n_se_sie4.wizard.sie_import_wizard.SYNC_LIMIT", 3), \
                patch("odoo.addons.l10n_se_sie4.models.l10n_se_sie_import.CRON_BATCH_SIZE", 3):
            record = self._import("fortnox_2025.se")
            self.assertEqual(record.state, "queued")
            self.assertFalse(record.move_ids)
            Import = self.env["l10n_se.sie.import"].sudo()
            for _i in range(5):
                Import._cron_process()
                if record.state == "done":
                    break
        self.assertEqual(record.state, "done")
        self.assertEqual(len(record.move_ids), 8)
        self.assertEqual(record.entry_done, 8)
        self.assertEqual(record.deviation_count, 0)
        self.assertEqual(record.move_ids.create_uid, self.user, "created as the importing user")

    @mute_logger("odoo.addons.l10n_se_sie4.models.l10n_se_sie_import")
    def test_background_failure_is_recorded(self):
        with patch("odoo.addons.l10n_se_sie4.wizard.sie_import_wizard.SYNC_LIMIT", 3):
            record = self._import("fortnox_2025.se")
        self.company.sudo().fiscalyear_lock_date = date(2025, 12, 31)
        self.env["l10n_se.sie.import"].sudo()._cron_process()
        self.assertEqual(record.state, "failed")
        self.assertIn("locked period", record.error_message)
        self.assertFalse(record.move_ids)

    # -- access ----------------------------------------------------------------------------------

    def test_only_accounting_administrators(self):
        record = self._import("fortnox_2025.se")
        bookkeeper = new_test_user(
            self.env(su=True), login="sie_bookkeeper",
            groups="base.group_user,account.group_account_user",
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)],
        )
        env = self.env(user=bookkeeper)
        with self.assertRaises(AccessError):
            env["l10n_se.sie.import.wizard"].create({})
        with self.assertRaises(AccessError):
            env["l10n_se.sie.export.wizard"].create({})
        with self.assertRaises(AccessError):
            record.with_user(bookkeeper).read(["name"])

    def test_other_company_cannot_see_the_import(self):
        record = self._import("fortnox_2025.se")
        other = self._create_company("Annat bolag AB")
        env = self.env(context=dict(self.env.context, allowed_company_ids=other.ids))
        self.user.sudo().company_ids |= other
        self.assertFalse(env["l10n_se.sie.import"].search([("id", "=", record.id)]))


@tagged("post_install", "-at_install", "l10n_se")
class TestSieReconcile(SieCase):
    """Reconciliation only: the year is booked in Odoo by hand, the file is the check."""

    def _book_the_year(self):
        # Booked "by hand": here through an import, then the import record is forgotten.
        record = self._import("fortnox_2025.se")
        return record.move_ids

    def _reconcile(self, name="fortnox_2025.se", **vals):
        wizard = self._wizard(name, mode="reconcile", **vals)
        self.assertFalse(wizard.blocked, wizard.preview_html)
        action = wizard.action_import()
        return self.env["l10n_se.sie.import"].browse(action["res_id"])

    def test_agrees(self):
        moves = self._book_the_year()
        record = self._reconcile()
        self.assertEqual(record.state, "reconciled")
        self.assertEqual(record.mode, "reconcile")
        self.assertFalse(record.move_ids)
        self.assertEqual(self.env["account.move"].search_count(
            [("company_id", "=", self.company.id)]), len(moves), "nothing created")
        self.assertEqual(record.deviation_count, 0)
        self.assertEqual(record.checked_count, 12)
        self.assertIn("agree", str(record.report_html))

    def test_deviations(self):
        self._book_the_year()
        self._entry(date(2025, 6, 30), [("6570", 100.0), ("1930", -100.0)])
        record = self._reconcile()
        self.assertEqual(record.deviation_count, 2)
        lines = record.deviation_ids.sorted("account_code")
        self.assertEqual(lines.mapped("account_code"), ["1930", "6570"])
        self.assertEqual(lines.mapped("kind"), ["balance", "result"])
        bank, fees = lines
        self.assertEqual(bank.difference, -100.0)
        self.assertEqual(fees.sie_amount, 95.0)
        self.assertEqual(fees.odoo_amount, 195.0)
        self.assertEqual(fees.difference, 100.0)
        self.assertTrue(record.check_ids.filtered(lambda c: not c.is_deviation),
                        "the agreeing accounts are listed too")
        # correct the books and check again
        self._entry(date(2025, 6, 30), [("6570", -100.0), ("1930", 100.0)], ref="Correction")
        record.action_check_again()
        self.assertEqual(record.deviation_count, 0)

    def test_nothing_booked_yet(self):
        record = self._reconcile()
        self.assertEqual(record.deviation_count, record.checked_count)
        self.assertGreater(record.deviation_count, 0)

    def test_as_of_month_end_uses_psaldo(self):
        self._book_the_year()
        record = self._reconcile(check_date=date(2025, 1, 31))
        self.assertEqual(record.year_ids.source, "psaldo")
        self.assertEqual(record.year_ids.check_date, date(2025, 1, 31))
        self.assertEqual(record.deviation_count, 0)
        bank = record.check_ids.filtered(lambda c: c.account_code == "1930")
        self.assertEqual(bank.sie_amount, 103900.5 - 1000 + 25000)

    def test_as_of_a_day_uses_the_vouchers(self):
        self._book_the_year()
        self._entry(date(2025, 1, 21), [("6570", 10.0), ("1930", -10.0)])
        record = self._reconcile(check_date=date(2025, 1, 20))
        self.assertEqual(record.year_ids.source, "vouchers")
        self.assertEqual(record.deviation_count, 0, "the entry after the date does not count")
        record = self._reconcile(check_date=date(2025, 1, 21))
        self.assertEqual(record.deviation_count, 2)

    def test_date_outside_the_year(self):
        wizard = self._wizard("fortnox_2025.se", mode="reconcile", check_date=date(2026, 1, 31))
        self.assertTrue(wizard.blocked)

    def test_file_without_balances(self):
        wizard = self._wizard("kassa.si", mode="reconcile")
        self.assertTrue(wizard.blocked)
        self.assertIn("no closing balances", str(wizard.preview_html))

    def test_errors_in_the_file_do_not_stop_a_reconciliation(self):
        wizard = self._wizard("unbalanced.se", mode="reconcile")
        self.assertFalse(wizard.blocked)

    def test_draft_entries_do_not_count(self):
        self._book_the_year()
        self._entry(date(2025, 6, 30), [("6570", 100.0), ("1930", -100.0)], post=False)
        self.assertEqual(self._reconcile().deviation_count, 0)


@tagged("post_install", "-at_install", "l10n_se")
class TestSieSwedish(SieCase):
    def test_swedish(self):
        env = self.env(su=True)
        env["res.lang"]._activate_lang("sv_SE")
        env["ir.module.module"]._load_module_terms(["l10n_se_sie4"], ["sv_SE"])
        menu = env.ref("l10n_se_sie4.menu_l10n_se_sie_import").with_context(lang="sv_SE")
        self.assertEqual(menu.name, "SIE-import")
        self.assertEqual(env.ref("l10n_se_sie4.menu_l10n_se_sie_export").with_context(
            lang="sv_SE").name, "SIE-export")
        sv = self.env(context=dict(self.env.context, lang="sv_SE"))
        wizard = self._wizard("fortnox_2025.se", env=sv)
        html = str(wizard.preview_html)
        for text in ("Räkenskapsår", "Konton som saknas i Odoo", "8 verifikat skapas",
                     "SIE-filer innehåller inga momskoder"):
            self.assertIn(text, html)
        record = sv["l10n_se.sie.import"].browse(wizard.action_import()["res_id"])
        self.assertIn("Alla 12 konton stämmer med filerna", str(record.report_html))
        opening = record.move_ids.filtered(lambda m: m.l10n_se_sie_key == "2025|IB|")
        self.assertEqual(opening.ref, "SIE 2025 IB — Ingående balans")
