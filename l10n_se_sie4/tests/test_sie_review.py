"""Regression tests for the findings of the review of 19.0.1.0.0 (one or more per finding).

The SIE files are synthetic: an invented company and organisation number.
"""

import base64
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from odoo.addons.l10n_se_sie4.lib import sie
from odoo.addons.l10n_se_sie4.models import sie_analysis
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import SieCase

HEADER = ["#FLAGGA 0", '#PROGRAM "Exempelprogram" 1.0', "#FORMAT PC8", "#GEN 20260201",
          "#SIETYP 4", '#FNAMN "Exempelbolaget Norrsken AB"']
MODEL = "odoo.addons.l10n_se_sie4.models.l10n_se_sie_import"


def sie_bytes(*records):
    return ("\r\n".join(records) + "\r\n").encode("cp437")


@tagged("post_install", "-at_install", "l10n_se")
class TestReviewOpeningBalance(SieCase):
    """Findings 1-3: the opening balance is never booked twice and the differences entry books
    what the preview showed."""

    def _record(self, wizard):
        return self.env["l10n_se.sie.import"].browse(wizard.action_import()["res_id"])

    # 1
    def test_reverse_order_is_refused(self):
        self._import("fortnox_2025.se")
        for opening in (True, False):
            wizard = self._wizard("fortnox_2024.se", import_opening=opening)
            self.assertTrue(wizard.blocked, f"import_opening={opening}")
            with self.assertRaises(UserError):
                wizard.action_import()
        balances = self._balances(self.company, date(2025, 12, 31))
        self.assertEqual(balances["2081"], Decimal("-50000.00"), "share capital not doubled")

    def test_opening_balance_refused_with_later_drafts(self):
        self._entry(date(2024, 6, 1), [("6570", 10.0), ("1930", -10.0)], post=False)
        wizard = self._wizard("fortnox_2024.se", import_opening=True)
        self.assertTrue(wizard.blocked)
        self.assertIn("already has entries", str(wizard.preview_html))

    def test_later_years_are_checked_again(self):
        """A later year already in Odoo is reconciled again when an earlier year is added."""
        later = self._import("fortnox_2025.se", import_opening=False)
        bank_before = later.check_ids.filtered(lambda c: c.account_code == "1930").odoo_amount
        earlier = self._import("fortnox_2024.se", import_opening=False)
        bank_after = later.check_ids.filtered(lambda c: c.account_code == "1930").odoo_amount
        self.assertNotEqual(bank_before, bank_after, "the 2025 import was checked again")
        self.assertIn("2025", str(earlier.report_html))
        self.assertIn(later.name, str(earlier.report_html))

    # 2
    def test_second_year_opening_balance_is_off_and_refused(self):
        self._import("fortnox_2024.se")
        wizard = self._wizard("fortnox_2025.se")
        self.assertFalse(wizard.import_opening, "unticked: the company already has history")
        self.assertFalse(wizard.blocked)
        wizard.import_opening = True
        self.assertTrue(wizard.blocked)
        with self.assertRaises(UserError):
            wizard.action_import()

    # 3
    def test_opening_differences_stop_when_the_year_before_is_incomplete(self):
        data_2024 = self._data("fortnox_2024.se").replace(
            b"#TRANS 8310 {} -125.50", b"#TRANS 8310 {} -125.00")  # A11 no longer balances
        moved = self._data("fortnox_2025.se").replace(
            b"#IB 0 2091 -100000.00", b"#IB 0 2091 -55719.50").replace(
            b"#IB 0 2099 44280.50", b"").replace(
            b"#TRANS 2091 {} 44280.50", b"#TRANS 2091 {} 0.00").replace(
            b"#TRANS 2099 {} -44280.50", b"#TRANS 2099 {} 0.00")
        wizard = self._wizard(files=[("y2024.se", data_2024), ("y2025.se", moved)],
                              skip_invalid=True, opening_differences=True)
        self.assertFalse(wizard.blocked)
        with self.assertRaises(UserError) as exc:
            wizard.action_import()
        self.assertIn("1930", str(exc.exception))
        self.assertIn("#UB", str(exc.exception))
        self.assertFalse(self.env["account.move"].search_count(
            [("company_id", "=", self.company.id)]), "nothing is kept")

    def test_opening_differences_book_the_preview(self):
        moved = self._data("fortnox_2025.se").replace(
            b"#IB 0 2091 -100000.00", b"#IB 0 2091 -55719.50").replace(
            b"#IB 0 2099 44280.50", b"").replace(
            b"#TRANS 2091 {} 44280.50", b"#TRANS 2091 {} 0.00").replace(
            b"#TRANS 2099 {} -44280.50", b"#TRANS 2099 {} 0.00")
        wizard = self._wizard(files=[("y2024.se", self._data("fortnox_2024.se")),
                                     ("y2025.se", moved)], opening_differences=True)
        record = self._record(wizard)
        diff = record.move_ids.filtered(lambda m: m.l10n_se_sie_key == "2025|IBDIFF|")
        self.assertEqual(sorted((ln.account_id.code, ln.balance) for ln in diff.line_ids),
                         [("2091", 44280.5), ("2099", -44280.5)])
        self.assertEqual(record.deviation_count, 0)


@tagged("post_install", "-at_install", "l10n_se")
class TestReviewUndo(SieCase):
    """Finding 4: undo never destroys or leaves behind other data, and is all or nothing."""

    def test_refused_when_reconciled(self):
        record = self._import("fortnox_2025.se")
        moves = record.move_ids.filtered(lambda m: m.l10n_se_sie_key in ("2025|A|1", "2025|A|3"))
        lines = moves.line_ids.filtered(lambda ln: ln.account_id.code == "1510")
        lines.reconcile()
        self.assertTrue(lines.full_reconcile_id)
        with self.assertRaises(UserError):
            record.action_undo()
        self.assertTrue(lines.exists().full_reconcile_id)

    def test_refused_when_reversed(self):
        record = self._import("fortnox_2025.se")
        move = record.move_ids.filtered(lambda m: m.l10n_se_sie_key == "2025|A|5")
        move._reverse_moves()
        with self.assertRaises(UserError):
            record.action_undo()
        self.assertTrue(move.exists())

    def test_refused_when_edited(self):
        record = self._import("fortnox_2025.se", post_moves=False)
        move = record.move_ids.filtered(lambda m: m.l10n_se_sie_key == "2025|A|5")
        line = move.line_ids.filtered(lambda ln: ln.account_id.code == "6570")
        line.account_id = self._account("6580")
        with self.assertRaises(UserError):
            record.action_undo()
        self.assertTrue(move.exists())

    def test_posting_drafts_is_not_an_edit(self):
        record = self._import("fortnox_2025.se", post_moves=False)
        record.move_ids.action_post()
        record.action_undo()
        self.assertEqual(record.state, "undone")

    def test_keeps_analytic_accounts_used_elsewhere(self):
        record = self._import("fortnox_2025.se")
        analytic = record.created_analytic_ids.sudo().filtered(lambda a: a.code == "20")
        move = self._entry(date(2025, 8, 1), [("6570", 10.0), ("1930", -10.0)], post=False)
        move.line_ids.filtered(lambda ln: ln.account_id.code == "6570").analytic_distribution = {
            str(analytic.id): 100}
        record.action_undo()
        self.assertTrue(analytic.exists(), "still used by a draft entry")
        self.assertFalse((record.created_analytic_ids - analytic).sudo().exists())

    def test_background_undo_is_all_or_nothing(self):
        record = self._import("fortnox_2025.se")
        moves = record.move_ids
        with patch(f"{MODEL}.UNDO_SYNC_LIMIT", 3):
            record.action_undo()
            self.assertEqual(record.state, "undoing")
            self.env["l10n_se.sie.import"].sudo()._cron_process()
            left = len(moves.exists())
            self.assertIn(left, (0, len(moves)), "never half undone")
            self.company.sudo().fiscalyear_lock_date = date(2025, 12, 31)
            self.env["l10n_se.sie.import"].sudo()._cron_process()
        self.assertIn(len(moves.exists()), (0, len(moves)))

    @mute_logger(MODEL)
    def test_background_undo_checks_again(self):
        record = self._import("fortnox_2025.se")
        moves = record.move_ids
        with patch(f"{MODEL}.UNDO_SYNC_LIMIT", 3):
            record.action_undo()
        self.company.sudo().fiscalyear_lock_date = date(2025, 12, 31)
        self.env["l10n_se.sie.import"].sudo()._cron_process()
        self.assertEqual(record.state, "failed")
        self.assertEqual(len(moves.exists()), len(moves), "nothing deleted")

    def test_lock_dates_read_once_per_journal(self):
        record = self._import("fortnox_2025.se")
        Company = type(self.env["res.company"])
        original = Company._get_user_fiscal_lock_date
        calls = []

        def counting(company, journal, *args, **kwargs):
            calls.append(journal.id)
            return original(company, journal, *args, **kwargs)

        with patch.object(Company, "_get_user_fiscal_lock_date", counting):
            record._undo_blockers()
        self.assertLessEqual(len(calls), len(record.move_ids.journal_id))


@tagged("post_install", "-at_install", "l10n_se")
class TestReviewFile(SieCase):
    """Findings 5, 6, 8, 9 and the low ones on keys, references and characters."""

    # 5
    def test_duplicate_voucher_numbers_are_refused(self):
        data = sie_bytes(*HEADER, "#RAR 0 20250101 20251231",
                         '#VER A 1 20250105 "Första"', "{", "#TRANS 6250 {} 10.00",
                         "#TRANS 1930 {} -10.00", "}",
                         '#VER A 1 20250106 "Andra"', "{", "#TRANS 6250 {} 20.00",
                         "#TRANS 1930 {} -20.00", "}")
        wizard = self._wizard(files=[("dup.se", data)])
        self.assertTrue(wizard.blocked)
        html = str(wizard.preview_html)
        self.assertIn("dup.se, line 13", html)
        self.assertIn("line 8", html)

    def test_identical_vouchers_without_numbers_are_all_imported(self):
        voucher = ['#VER "" "" 20250105 "Porto"', "{", "#TRANS 6250 {} 10.00",
                   "#TRANS 1930 {} -10.00", "}"]
        data = sie_bytes(*HEADER, *voucher, *voucher)
        record = self._import(files=[("kassa.si", data)])
        self.assertEqual(len(record.move_ids), 2)
        self.assertEqual(self._wizard(files=[("kassa.si", data)]).entry_count, 0,
                         "a second import of the same file creates nothing")

    # 6
    def test_vouchers_outside_the_year_are_shown(self):
        data = self._data("fortnox_2025.se") + sie_bytes(
            '#VER C 1 20241215 "Föregående år"', "{", "#TRANS 6570 {} 5.00",
            "#TRANS 1930 {} -5.00", "}")
        wizard = self._wizard(files=[("late.se", data)])
        html = str(wizard.preview_html)
        self.assertIn("outside the financial year", html)
        self.assertIn("C1", html)

    # 8
    def test_opening_balance_must_balance_after_rounding(self):
        data = sie_bytes(*HEADER, "#RAR 0 20250101 20251231", "#IB 0 1930 0.005",
                         "#IB 0 1910 0.005", "#IB 0 2091 -0.010")
        wizard = self._wizard(files=[("ib.se", data)], post_moves=False)
        self.assertTrue(wizard.blocked)
        with self.assertRaises(UserError):
            wizard.action_import()
        self.assertFalse(self.env["account.move"].search_count(
            [("company_id", "=", self.company.id)]))

    # 9
    def test_file_size_limit(self):
        self.env["ir.config_parameter"].sudo().set_param("l10n_se_sie4.max_file_mb", "0.001")
        with self.assertRaises(UserError):
            self._wizard("fortnox_2025.se")

    def test_one_analysis_per_import(self):
        calls = []
        original = sie_analysis.analyse

        def counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        wizard = self._wizard("fortnox_2025.se")
        with patch.object(sie_analysis, "analyse", counting), \
                patch("odoo.addons.l10n_se_sie4.wizard.sie_import_wizard.SYNC_LIMIT", 3), \
                patch(f"{MODEL}.CRON_BATCH_SIZE", 2):
            record = self.env["l10n_se.sie.import"].browse(wizard.action_import()["res_id"])
            for _i in range(10):
                if record.state == "done":
                    break
                self.env["l10n_se.sie.import"].sudo()._cron_process()
        self.assertEqual(record.state, "done")
        self.assertLessEqual(len(calls), 2, "the wizard's check and one per background run")

    # low: Unicode digits
    def test_superscript_digit_does_not_break_the_preview(self):
        data = sie_bytes(*HEADER, "#RAR 0 20250101 20251231", '#VER A 1 20250105 "x"', "{",
                         '#TRANS 6250 {² "10"} 10.00', "#TRANS 1930 {} -10.00", "}")
        wizard = self._wizard(files=[("sup.se", data)])
        self.assertTrue(wizard.blocked)
        self.assertIn("sup.se, line", str(wizard.preview_html))

    # low: references
    def test_series_ending_in_a_digit_is_not_mixed_up(self):
        self._entry(date(2025, 1, 10), [("1930", 1.0), ("2091", -1.0)],
                    ref="SIE 2025 A12 — Något annat")
        data = sie_bytes(*HEADER, "#RAR 0 20250101 20251231", '#VER A1 2 20250105 "x"', "{",
                         "#TRANS 6250 {} 10.00", "#TRANS 1930 {} -10.00", "}")
        wizard = self._wizard(files=[("a1.se", data)], import_opening=False)
        self.assertEqual(wizard.entry_count, 1)
        record = self.env["l10n_se.sie.import"].browse(wizard.action_import()["res_id"])
        self.assertNotEqual(record.move_ids.ref.split(" — ")[0], "SIE 2025 A12")

    def test_cancelled_entries_are_imported_again(self):
        record = self._import("fortnox_2025.se")
        move = record.move_ids.filtered(lambda m: m.l10n_se_sie_key == "2025|A|5")
        move.button_draft()
        move.button_cancel()
        wizard = self._wizard("fortnox_2025.se", import_opening=False)
        self.assertEqual(wizard.entry_count, 1)
        again = self.env["l10n_se.sie.import"].browse(wizard.action_import()["res_id"])
        self.assertEqual(again.move_ids.l10n_se_sie_key, "2025|A|5")

    # low: formula injection
    def test_spreadsheet_formulas_are_neutralised(self):
        data = self._data("fortnox_2025.se").replace(
            '#KONTO 3001 "Försäljning inom Sverige, 25 % moms"'.encode("cp437"),
            b'#KONTO 3001 "=HYPERLINK(1)"')
        record = self._import(files=[("f.se", data)])
        names = record.check_ids.mapped("account_name")
        self.assertFalse([n for n in names if n and n[0] in "=+-@"])

    # low: uploaded files
    def test_uploaded_files_are_not_left_behind(self):
        record = self._import("fortnox_2025.se")
        left = self.env["ir.attachment"].sudo().search_count(
            [("res_model", "=", "l10n_se.sie.import.wizard")])
        self.assertEqual(left, 0)
        self.assertEqual(len(record.attachment_ids), 1)

    def test_abandoned_uploads_are_removed(self):
        att = self._attachments(self.env, [("old.se", b"#FLAGGA 0")])
        self.env.cr.execute("UPDATE ir_attachment SET create_date = %s WHERE id = %s",
                            (datetime.now() - timedelta(days=3), att.id))
        att.invalidate_recordset()
        self.env["l10n_se.sie.import.wizard"].sudo()._gc_uploads()
        self.assertFalse(att.exists())


@tagged("post_install", "-at_install", "l10n_se")
class TestReviewReport(SieCase):
    """Findings 7 and 10."""

    # 7
    def test_no_green_when_a_year_was_not_checked(self):
        data_2026 = sie_bytes(*HEADER, '#VER "" "" 20260105 "Porto"', "{",
                              "#TRANS 6250 {} 10.00", "#TRANS 1930 {} -10.00", "}")
        record = self._import("fortnox_2025.se", files=[("kassa2026.si", data_2026)])
        self.assertEqual(record.deviation_count, 0)
        self.assertEqual(sorted(record.year_ids.mapped("status")), ["nobalances", "ok"])
        self.assertNotIn("alert-success", str(record.report_html))

    # 10
    def test_html_cannot_carry_scripts(self):
        record = self._import("fortnox_2025.se")
        record.write({"preview_html": "<p>x</p><script>alert(1)</script>"})
        self.assertNotIn("<script", str(record.preview_html))
        self.assertFalse(record._fields["report_html"].store)
        wizard = self.env["l10n_se.sie.export.wizard"].create({
            "date_from": date(2025, 1, 1), "date_to": date(2025, 12, 31)})
        wizard.write({"summary_html": "<p>x</p><script>alert(1)</script>"})
        self.assertNotIn("<script", str(wizard.summary_html))


@tagged("post_install", "-at_install", "l10n_se")
class TestReviewExport(SieCase):
    """Findings 11, 12 and the low ones on the export."""

    def _export(self, company=None, **vals):
        company = company or self.company
        env = self.env(context=dict(self.env.context, allowed_company_ids=company.ids))
        wizard = env["l10n_se.sie.export.wizard"].create({
            "date_from": date(2025, 1, 1), "date_to": date(2025, 12, 31), **vals})
        wizard.action_export()
        return wizard, sie.parse(base64.b64decode(wizard.file_data))

    # 11
    def test_distribution_over_two_plans(self):
        self._import("fortnox_2025.se")
        Analytic = self.env["account.analytic.account"].sudo()
        store = Analytic.search([("code", "=", "10")])
        project = Analytic.search([("code", "=", "P1")])
        move = self._entry(date(2025, 6, 1), [("6570", 100.0), ("1930", -100.0)], post=False)
        move.line_ids.filtered(lambda ln: ln.account_id.code == "6570").analytic_distribution = {
            str(store.id): 100, str(project.id): 100}
        move.action_post()
        _w, parsed = self._export()
        voucher = [v for v in parsed.vouchers if v.date == date(2025, 6, 1)][0]
        self.assertTrue(voucher.is_balanced)
        fees = [(t.objects, t.amount) for t in voucher.transactions if t.account == "6570"]
        self.assertEqual(fees, [((("1", "10"), ("6", "P1")), Decimal("100.00"))])

    # 12
    def test_branches(self):
        self._import("fortnox_2025.se")
        branch = self.env(su=True)["res.company"].create({
            "name": "Filialen", "parent_id": self.company.id})
        self.user.sudo().company_ids |= branch
        self._entry(date(2025, 6, 1), [("6570", 7.0), ("1930", -7.0)], company=branch)
        _w, parsed = self._export()
        self.assertEqual(parsed.amounts("results")["6570"], Decimal("102.00"))
        self.assertEqual(len(parsed.vouchers), 8)
        record = self.env["l10n_se.sie.import"].search([], limit=1)
        record.action_check_again()
        self.assertEqual(record.deviation_count, 2, "the branch's entry is in the books")

    # low: #RAR -1
    def test_previous_year_is_editable_and_checked(self):
        company = self.company.sudo()
        company.write({"fiscalyear_last_month": "6", "fiscalyear_last_day": 30})
        wizard = self.env["l10n_se.sie.export.wizard"].create({
            "date_from": date(2025, 7, 1), "date_to": date(2026, 6, 30)})
        self.assertEqual((wizard.prev_date_from, wizard.prev_date_to),
                         (date(2024, 7, 1), date(2025, 6, 30)))
        wizard.write({"prev_date_from": date(2025, 1, 1), "prev_date_to": date(2025, 6, 30)})
        wizard.action_export()
        parsed = sie.parse(base64.b64decode(wizard.file_data))
        self.assertEqual((parsed.year(-1).start, parsed.year(-1).end),
                         (date(2025, 1, 1), date(2025, 6, 30)))
        other = self.env["l10n_se.sie.export.wizard"].create({
            "date_from": date(2025, 9, 1), "date_to": date(2026, 6, 30)})
        other.action_export()
        self.assertIn("not a financial year", str(other.summary_html))

    # low: several unclosed years
    def test_several_unclosed_years_are_explained(self):
        self._entry(date(2023, 5, 1), [("1930", 100.0), ("3740", -100.0)])
        self._entry(date(2024, 5, 1), [("1930", 200.0), ("3740", -200.0)])
        wizard, parsed = self._export()
        self.assertEqual(parsed.amounts("opening")["2099"], Decimal("-300.00"))
        summary = str(wizard.summary_html)
        self.assertIn("2091", summary)
        self.assertIn("2098", summary)


@tagged("post_install", "-at_install", "l10n_se")
class TestReviewSecurity(SieCase):
    """Low findings: record rules on the year and series lines."""

    def test_year_and_series_lines_follow_the_company(self):
        record = self._import("fortnox_2025.se", journal_mode="series")
        other = self._create_company("Annat bolag AB")
        self.user.sudo().company_ids |= other
        env = self.env(context=dict(self.env.context, allowed_company_ids=other.ids))
        self.assertFalse(env["l10n_se.sie.import.year"].search([("id", "in", record.year_ids.ids)]))
        self.assertFalse(env["l10n_se.sie.import.series"].search(
            [("id", "in", record.series_ids.ids)]))


@tagged("post_install", "-at_install", "l10n_se")
class TestReviewRestrictMode(SieCase):
    """Low finding: OCA account_journal_restrict_mode makes every general journal hashed."""

    def test_journal_with_oca_restrict_mode(self):
        if not self.env["ir.module.module"].search_count(
                [("name", "=", "account_journal_restrict_mode"), ("state", "=", "installed")]):
            self.skipTest("account_journal_restrict_mode is not installed")
        env = self.env(context=dict(self.env.context, test_account_journal_restrict_mode=True))
        wizard = self._wizard("fortnox_2025.se", env=env)
        self.assertIn("hash", str(wizard.preview_html))
        record = env["l10n_se.sie.import"].browse(wizard.action_import()["res_id"])
        self.assertEqual(record.state, "done")
        self.assertTrue(record.move_ids.journal_id.restrict_mode_hash_table)
        with self.assertRaises(UserError):
            record.action_undo()

