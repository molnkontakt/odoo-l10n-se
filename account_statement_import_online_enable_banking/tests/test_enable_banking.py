# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import contextlib
import hashlib
from datetime import datetime, timedelta
from unittest import mock

import requests

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..models.online_bank_statement_provider import _eb_error, _eb_short

MODULE = "odoo.addons.account_statement_import_online_enable_banking.models.online_bank_statement_provider"
PROVIDER = f"{MODULE}.OnlineBankStatementProvider"

# Shapes as Enable Banking returns them for a Swedish business account (values
# invented; the structure is what matters).
SWISH = {
    "transaction_amount": {"currency": "SEK", "amount": "1000.00"},
    "creditor": {"name": "TEST ASSOCIATION"},
    "creditor_account": {"iban": "SE0000000000000000000001"},
    "debtor": {"name": "1234567890"},
    "debtor_account": {"iban": None, "other": {"identification": "+46700000000", "scheme_name": "OTHI"}},
    "bank_transaction_code": {"description": "Swish"},
    "credit_debit_indicator": "CRDT", "status": "BOOK",
    "booking_date": "2026-09-14", "value_date": "2026-09-15",
    "remittance_information": ["1234567890"], "entry_reference": None, "transaction_id": None,
}
BANKGIRO = {
    "transaction_amount": {"currency": "SEK", "amount": "2000.00"},
    "creditor": {"name": "TEST ASSOCIATION"},
    "debtor": {"name": "         1234567"},
    "bank_transaction_code": {"description": "Bankgiro inbetalning"},
    "credit_debit_indicator": "CRDT", "status": "BOOK",
    "booking_date": "2026-09-15", "value_date": "2026-09-16",
    "remittance_information": ["         1234567"],
}
DEBIT = {
    "transaction_amount": {"currency": "SEK", "amount": "44445.00"},
    "creditor": {"name": "Water Utility"},
    "debtor": {"name": "TEST ASSOCIATION"},
    "debtor_account": {"iban": "SE0000000000000000000001"},
    "bank_transaction_code": {"description": "Bg-bet. via internet"},
    "credit_debit_indicator": "DBIT", "status": "BOOK",
    "booking_date": "2026-09-04", "value_date": "2026-09-04",
    "remittance_information": ["Water Utility"],
}
PENDING = dict(SWISH, status="PDNG", booking_date=None)
# Personal account: no transaction type, no counterparty id, the number first in the remittance.
SWISH_PERSONAL = {
    "transaction_amount": {"currency": "SEK", "amount": "20.00"},
    "credit_debit_indicator": "CRDT", "status": "BOOK",
    "booking_date": "2026-09-14", "value_date": "2026-09-14",
    "remittance_information": ["+46700000000    1833000000000001 swish mottagen"],
}


@tagged("post_install", "-at_install")
class TestEnableBanking(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.bank_account = cls.env["res.partner.bank"].create(
            {"acc_number": "SE00 0000 0000 0000 0000 0001", "partner_id": cls.company.partner_id.id}
        )
        cls.journal = cls.env["account.journal"].create(
            {"name": "EB Bank", "type": "bank", "code": "EBB", "company_id": cls.company.id,
             "bank_account_id": cls.bank_account.id}
        )
        cls.provider = cls.env["online.bank.statement.provider"].create(
            {"journal_id": cls.journal.id, "service": "enable_banking", "username": "app-id",
             "certificate_private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-key\n-----END PRIVATE KEY-----",
             "eb_aspsp_name": "Mock ASPSP", "eb_session_id": "sess", "eb_account_uid": "acc-uid",
             "eb_session_valid_until": fields.Datetime.now() + timedelta(days=30)}
        )
        cls.payer = cls.env["res.partner"].create({"name": "Swish Payer", "phone": "+46700000000"})

    def setUp(self):
        super().setUp()
        # Messages follow the company language; the assertions check the source strings.
        patcher = mock.patch(f"{PROVIDER}._enable_banking_lang", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _pull(self, transactions, balances=None, since=None, until=None):
        since = since or datetime(2026, 9, 1)
        until = until or datetime(2026, 10, 1)

        def fake_request(provider, method, path, params=None, body=None):
            if path.endswith("/transactions"):
                return {"transactions": transactions, "continuation_key": None}
            if path.endswith("/balances"):
                return {"balances": balances or []}
            raise AssertionError(path)

        with mock.patch(f"{PROVIDER}._eb_request", new=fake_request):
            return self.provider._obtain_statement_data(since, until)

    def test_line_mapping(self):
        lines, _values = self._pull([SWISH, BANKGIRO, DEBIT, PENDING])
        self.assertEqual(len(lines), 3, "pending transactions are skipped")
        swish, bankgiro, debit = lines
        self.assertEqual(swish["amount"], 1000.0)
        self.assertEqual(swish["payment_ref"], "1234567890 Swish +46700000000")
        self.assertFalse(swish["partner_name"], "a bare reference number is not a partner name")
        self.assertEqual(swish["partner_id"], self.payer.id, "Swish payer matched on phone_sanitized")
        self.assertEqual(swish["date"], fields.Date.from_string("2026-09-14"))
        self.assertEqual(bankgiro["payment_ref"], "Bankgiro inbetalning 1234567")
        self.assertEqual(debit["amount"], -44445.0)
        self.assertEqual(debit["partner_name"], "Water Utility")
        self.assertEqual(debit["payment_ref"], "Bg-bet. via internet Water Utility")
        self.assertFalse(debit["account_number"], "own IBAN is not the counterparty")

    def test_value_date(self):
        self.provider.eb_date_type = "value_date"
        lines, _ = self._pull([SWISH])
        self.assertEqual(lines[0]["date"], fields.Date.from_string("2026-09-15"))

    def test_identical_rows_get_distinct_ids(self):
        lines, _ = self._pull([SWISH, SWISH, SWISH, BANKGIRO])
        ids = [ln["unique_import_id"] for ln in lines]
        self.assertEqual(len(set(ids)), 4)
        self.assertTrue(ids[0].endswith("-1") and ids[2].endswith("-3"))
        # Deterministic across pulls.
        again, _ = self._pull([SWISH, SWISH, SWISH, BANKGIRO])
        self.assertEqual(ids, [ln["unique_import_id"] for ln in again])

    def test_bank_reference_wins(self):
        lines, _ = self._pull([dict(SWISH, entry_reference="REF-1")])
        self.assertEqual(lines[0]["unique_import_id"], "REF-1")

    def test_balance_only_when_period_covers_today(self):
        now = fields.Datetime.now()
        balances = [{"name": "Current booked balance", "balance_type": "CLBD", "balance_amount": {"amount": "34847.32"}}]
        _, values = self._pull([SWISH], balances, since=now - timedelta(days=1), until=now + timedelta(days=1))
        self.assertEqual(values.get("balance_end_real"), 34847.32)
        _, values = self._pull([SWISH], balances, since=datetime(2025, 1, 1), until=datetime(2025, 2, 1))
        self.assertNotIn("balance_end_real", values)

    def test_expired_consent_pulls_nothing(self):
        """A failure, not an empty pull: a manual pull says so; a scheduled one notes it once and keeps
        the period for when the bank is authorised again."""
        self.provider.eb_session_valid_until = fields.Datetime.now() - timedelta(days=1)
        with self.assertRaisesRegex(UserError, "expired"):
            self._pull([SWISH])
        since = datetime.combine(fields.Date.today() - timedelta(days=2), datetime.min.time())
        until = since + timedelta(days=2)
        self.provider.write({"statement_creation_mode": "daily", "last_successful_run": since, "next_run": until})
        before = self.provider.message_ids
        with self.assertLogs(MODULE, "WARNING"):
            self.provider.with_context(scheduled=True)._pull(since, until)
        notes = self.provider.message_ids - before
        self.assertEqual(len(notes), 1, "one note per run, not one per period")
        self.assertIn("expired", notes.body)
        self.assertEqual(self.provider.last_successful_run, since, "kept until the bank is authorised again")

    def test_finish_authorization_picks_journal_iban(self):
        session = {
            "session_id": "new-sess", "access": {"valid_until": "2026-12-15T10:00:00+00:00"},
            "accounts": [
                {"uid": "other", "account_id": {"iban": "SE0000000000000000000002"}},
                {"uid": "mine", "account_id": {"iban": "SE0000000000000000000001"}},
            ],
        }
        with mock.patch(f"{PROVIDER}._eb_request", return_value=session):
            self.assertTrue(self.provider._enable_banking_finish_authorization("code"))
        self.assertEqual(self.provider.eb_account_uid, "mine")
        self.assertEqual(self.provider.eb_session_id, "new-sess")
        self.assertEqual(self.provider.eb_session_valid_until, datetime(2026, 12, 15, 10, 0))

    def test_domestic_account_number_matches_iban(self):
        same = self.provider._enable_banking_same_account
        # Swedbank 8327-9, 123 456 789-7 and its IBAN (invented numbers)
        self.assertTrue(same("SE72 8000 0832 7912 3456 7897", "8327-9 123 456 789-7"))
        self.assertTrue(same("SE7280000832791234567897", "SE7280000832791234567897"))
        self.assertFalse(same("SE7280000832791234567897", "8327-9 123 456 788-9"))
        self.assertFalse(same("SE7280000832791234567897", "7897"), "too short to be an account number")
        # IBAN in the test data is all zeros; a domestic number that is its suffix must bind.
        session = {"session_id": "s", "access": {}, "accounts": [{"uid": "mine", "account_id": {"iban": "SE00 0000 0000 0000 1234 5678"}}]}
        self.bank_account.acc_number = "0000 1234 5678"
        with mock.patch(f"{PROVIDER}._eb_request", return_value=session):
            self.assertTrue(self.provider._enable_banking_finish_authorization("code"))
        self.assertEqual(self.provider.eb_account_uid, "mine")

    def test_finish_authorization_without_match_leaves_account_empty(self):
        session = {"session_id": "s", "access": {}, "accounts": [{"uid": "x", "account_id": {"iban": "SE0000000000000000000009"}}]}
        with mock.patch(f"{PROVIDER}._eb_request", return_value=session):
            self.assertFalse(self.provider._enable_banking_finish_authorization("code"))
        self.assertFalse(self.provider.eb_account_uid)
        self.assertIn("none matches", self.provider.message_ids[0].body)

    def test_phone_already_in_text_is_not_repeated(self):
        raw = dict(SWISH, remittance_information=["+46700000000 1832520068070127 swish +46700000000"])
        lines, _ = self._pull([raw])
        self.assertEqual(lines[0]["payment_ref"], "+46700000000 1832520068070127 swish +46700000000")
        self.assertEqual(lines[0]["partner_id"], self.payer.id)

    def test_consent_warning_activity(self):
        Provider = self.env["online.bank.statement.provider"]
        self.provider.eb_session_valid_until = fields.Datetime.now() + timedelta(days=30)
        Provider._enable_banking_check_consents()
        self.assertFalse(self.provider.activity_ids, "30 days left, 14-day warning: nothing yet")
        self.provider.eb_session_valid_until = fields.Datetime.now() + timedelta(days=10)
        Provider._enable_banking_check_consents()
        Provider._enable_banking_check_consents()
        activities = self.provider.activity_ids
        self.assertEqual(len(activities), 1, "one to-do, not one per day")
        self.assertEqual(activities.user_id, self.env.user)
        self.assertIn("expires in 10 days", activities.summary)
        self.assertIn("Authorise with the bank", activities.note)
        session = {"session_id": "renewed", "access": {"valid_until": "2027-03-16T10:00:00+00:00"},
                   "accounts": [{"uid": "mine", "account_id": {"iban": "SE0000000000000000000001"}}]}
        with mock.patch(f"{PROVIDER}._eb_request", return_value=session):
            self.provider._enable_banking_finish_authorization("code")
        self.assertFalse(self.provider.activity_ids, "renewal closes the to-do")

    def test_pull_leaves_a_trace(self):
        self._pull([])
        self.assertTrue(self.provider.eb_last_pull)
        self.assertIn("0 booked transaction", self.provider.eb_last_pull_summary)
        before = len(self.provider.message_ids)
        self._pull([SWISH, DEBIT])
        self.assertIn("2 booked transaction", self.provider.eb_last_pull_summary)
        self.assertEqual(len(self.provider.message_ids), before + 1, "chatter note only when something came in")
        self.assertIn("-43445.0", self.provider.message_ids[0].body)

    def test_balance_failure_keeps_transactions(self):
        now = fields.Datetime.now()

        def fake_request(provider, method, path, params=None, body=None):
            if path.endswith("/transactions"):
                return {"transactions": [SWISH], "continuation_key": None}
            raise _eb_error("Enable Banking: the bank's daily limit ...", status=429)

        with mock.patch(f"{PROVIDER}._eb_request", new=fake_request):
            lines, values = self.provider._obtain_statement_data(now - timedelta(days=1), now + timedelta(days=1))
        self.assertEqual(len(lines), 1)
        self.assertNotIn("balance_end_real", values)
        self.assertIn("balance unavailable (429)", self.provider.eb_last_pull_summary)

    def test_failed_pull_is_recorded(self):
        """The chatter gets a fixed text; the details of an unexpected error stay in the server log."""
        def boom(provider, method, path, params=None, body=None):
            raise RuntimeError("bank down SE0000000000000000000001")
        # Odoo's assertRaises rolls back to a savepoint, which would also undo the trace.
        raised = False
        with mock.patch(f"{PROVIDER}._eb_request", new=boom):
            try:
                self.provider._obtain_statement_data(datetime(2026, 9, 1), datetime(2026, 9, 2))
            except RuntimeError:
                raised = True
        self.assertTrue(raised)
        self.assertIn("FAILED", self.provider.eb_last_pull_summary)
        self.assertIn("unexpected error", self.provider.message_ids[0].body)
        self.assertNotIn("SE0000000000000000000001", self.provider.message_ids[0].body)

    # --- Scheduled pulls through the OCA base: one note, retry from the failed period -----------

    def _scheduled(self, since, days, fake):
        """A scheduled run as the OCA scheduler makes it, without _scheduled_pull (which would pull
        every provider in the database)."""
        until = since + timedelta(days=days)
        self.provider.write({"statement_creation_mode": "daily", "last_successful_run": since, "next_run": until})
        before = self.provider.message_ids
        with mock.patch(f"{PROVIDER}._eb_request", new=fake), self.assertLogs(MODULE, "WARNING") as logs:
            self.provider.with_context(scheduled=True)._pull(since, until)
        return self.provider.message_ids - before, "\n".join(logs.output)

    def test_scheduled_retry_resumes_at_the_failed_period(self):
        since = datetime.combine(fields.Date.today() - timedelta(days=3), datetime.min.time())
        day1, day2 = since.date(), since.date() + timedelta(days=1)
        booked = dict(SWISH, booking_date=str(day1), value_date=str(day1), entry_reference="EB-RETRY-1")
        requested = []

        def fake(provider, method, path, params=None, body=None):
            requested.append(params["date_from"])
            if params["date_from"] == str(day1):
                return {"transactions": [booked], "continuation_key": None}
            raise _eb_error("Enable Banking or the bank is temporarily unavailable (503).", status=503)

        notes, log = self._scheduled(since, 3, fake)
        self.assertEqual(requested, [str(day1), str(day2)], "stops at the first failure")
        failures = notes.filtered(lambda m: "failed" in m.body)
        self.assertEqual(len(failures), 1, notes.mapped("body"))
        self.assertIn("temporarily unavailable", failures.body)
        self.assertEqual(len(notes), 2, "the failure and day 1's new transaction")
        self.assertIn("503", log)
        imported = self.env["account.bank.statement.line"].search([("journal_id", "=", self.journal.id), ("date", "=", day1)])
        self.assertEqual(len(imported), 1, "the period before the failure is imported")
        self.assertEqual(self.provider.last_successful_run, datetime.combine(day2, datetime.min.time()),
                         "the next run starts at the failed period, not at the window start")
        # the next scheduled run, from last_successful_run as the OCA scheduler does
        requested.clear()
        with mock.patch(f"{PROVIDER}._eb_request", return_value={"transactions": [booked], "continuation_key": None, "balances": []}):
            self.provider.with_context(scheduled=True)._pull(self.provider.last_successful_run, self.provider.next_run)
        self.assertFalse(self.provider.eb_resume_from)
        self.assertGreater(self.provider.last_successful_run, datetime.combine(day2, datetime.min.time()))
        imported = self.env["account.bank.statement.line"].search([("journal_id", "=", self.journal.id), ("date", "=", day1)])
        self.assertEqual(len(imported), 1, "no duplicate")

    def test_scheduled_failure_that_repeats_is_skipped(self):
        """A failure that would repeat for the same data (here a page loop) must not block every later day."""
        since = datetime.combine(fields.Date.today() - timedelta(days=2), datetime.min.time())

        def loop(provider, method, path, params=None, body=None):
            return {"transactions": [], "continuation_key": "same"}

        notes, _log = self._scheduled(since, 2, loop)
        self.assertEqual(len(notes), 1, notes.mapped("body"))
        self.assertIn("same page twice", notes.body)
        self.assertIn("will not be pulled again automatically", notes.body)
        self.assertEqual(self.provider.last_successful_run, since + timedelta(days=1),
                         "only the failed day is skipped: the next run continues with day 2, never tried")

    def test_scheduled_retry_gives_up_after_14_days(self):
        since = datetime.combine(fields.Date.today() - timedelta(days=20), datetime.min.time())

        def limit(provider, method, path, params=None, body=None):
            raise _eb_error("Enable Banking: the bank's daily limit ... (429).", status=429)

        notes, _log = self._scheduled(since, 3, limit)
        self.assertEqual(len(notes), 1, notes.mapped("body"))
        self.assertIn("will not be pulled again automatically", notes.body)
        self.assertEqual(self.provider.last_successful_run, since + timedelta(days=1), "gives up on that day only")

    def test_scheduled_unexpected_error_outside_the_request(self):
        """An error in the line processing (outside the module's own try) is noted by the OCA hook,
        with a fixed text; the raw text only in the log."""
        since = datetime.combine(fields.Date.today() - timedelta(days=2), datetime.min.time())
        broken = dict(SWISH, booking_date=str(since.date()), transaction_amount={"currency": "SEK", "amount": "SE0000000000000000000001"})

        def fake(provider, method, path, params=None, body=None):
            return {"transactions": [broken], "continuation_key": None}

        notes, log = self._scheduled(since, 1, fake)
        self.assertEqual(len(notes), 1, notes.mapped("body"))
        bodies = notes.body
        self.assertIn("unexpected error", bodies)
        self.assertIn("will not be pulled again automatically", bodies, "a data error would repeat: skipped")
        self.assertNotIn("SE0000000000000000000001", bodies)
        self.assertNotIn("Failed to obtain statement data", bodies, "not the OCA base's own note")
        self.assertIn("SE0000000000000000000001", log)

    def test_user_error_text_is_kept(self):
        """A UserError is meant for the user (a missing key): its text is shown, not 'unexpected error'."""
        def no_key(provider, method, path, params=None, body=None):
            raise UserError("Enable Banking: application id and private key are required.")

        # not assertRaises: its savepoint rollback would also undo the note
        with mock.patch(f"{PROVIDER}._eb_request", new=no_key), contextlib.suppress(UserError):
            self.provider._obtain_statement_data(datetime(2026, 9, 1), datetime(2026, 9, 2))
        self.assertIn("private key are required", self.provider.message_ids[0].body)

    def test_failed_pull_shows_the_fixed_error_text(self):
        def limit(provider, method, path, params=None, body=None):
            raise _eb_error("Enable Banking: the bank's daily limit ...", status=429, code="RATE_LIMIT")
        raised = False
        with mock.patch(f"{PROVIDER}._eb_request", new=limit):
            try:
                self.provider._obtain_statement_data(datetime(2026, 9, 1), datetime(2026, 9, 2))
            except UserError:
                raised = True
        self.assertTrue(raised)
        self.assertIn("daily limit", self.provider.message_ids[0].body)

    # --- The API answer: errors never carry the bank's raw text into the chatter ----------------

    def _answer(self, status, json_body=None, text=None, exception=None):
        response = mock.Mock(status_code=status, text=text if text is not None else str(json_body))
        if json_body is None:
            response.json.side_effect = ValueError("not json")
        else:
            response.json.return_value = json_body
        patchers = [
            mock.patch(f"{PROVIDER}._eb_jwt", return_value="jwt"),
            mock.patch(f"{MODULE}.requests.request", side_effect=exception, return_value=response),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _error(self, **answer):
        self._answer(**answer)
        with self.assertLogs(MODULE, "WARNING") as logs, self.assertRaises(UserError) as caught:
            self.provider._eb_request("GET", "/accounts/acc-uid/transactions")
        return caught.exception, "\n".join(logs.output)

    def test_error_daily_limit(self):
        body = {"error": "ASPSP_RATE_LIMIT_EXCEEDED", "message": "limit for SE0000000000000000000001"}
        err, log = self._error(status=429, json_body=body)
        self.assertIn("daily limit", str(err))
        self.assertIn("429 ASPSP_RATE_LIMIT_EXCEEDED", str(err))
        self.assertIn("does not need to be renewed", str(err))
        self.assertNotIn("SE0000000000000000000001", str(err))
        self.assertIn("SE0000000000000000000001", log, "the raw answer is logged")
        self.assertEqual(_eb_short(err), "429 ASPSP_RATE_LIMIT_EXCEEDED")
        self.assertIs(type(err), UserError, "a plain UserError: the web client shows a warning, not a crash dialog")

    def test_error_kinds(self):
        err, _log = self._error(status=503, json_body={"error": "ASPSP_ERROR"})
        self.assertIn("temporarily unavailable (503 ASPSP_ERROR)", str(err))

    def test_error_refused_without_code(self):
        err, _log = self._error(status=401, text="<html>unauthorised</html>")
        self.assertIn("refused the request (401)", str(err))
        self.assertNotIn("html", str(err))

    def test_error_code_that_is_not_a_code_is_dropped(self):
        err, _log = self._error(status=422, json_body={"error": "account SE0000000000000000000001 is closed"})
        self.assertEqual(str(err), "Enable Banking rejected the request (422).")

    def test_error_network(self):
        err, _log = self._error(status=200, exception=requests.ConnectionError("host SE0000000000000000000001"))
        self.assertIn("could not be reached", str(err))
        self.assertNotIn("SE0", str(err))

    def test_error_unreadable_answer(self):
        err, _log = self._error(status=200, text="<html>maintenance</html>")
        self.assertIn("could not be read", str(err))

    def test_pagination_stops_on_a_repeated_page(self):
        def same_key(provider, method, path, params=None, body=None):
            return {"transactions": [SWISH], "continuation_key": "k"}

        with (
            mock.patch(f"{PROVIDER}._eb_request", new=same_key),
            self.assertRaisesRegex(UserError, "same page twice"),
        ):
            self.provider._enable_banking_request_transactions(datetime(2026, 9, 1), datetime(2026, 9, 2))

    def test_pagination_has_a_page_limit(self):
        counter = iter(range(1000))

        def new_key(provider, method, path, params=None, body=None):
            return {"transactions": [], "continuation_key": f"k{next(counter)}"}

        with (
            mock.patch(f"{PROVIDER}._eb_request", new=new_key),
            self.assertRaisesRegex(UserError, "more than 100 pages"),
        ):
            self.provider._enable_banking_request_transactions(datetime(2026, 9, 1), datetime(2026, 9, 2))
        self.assertEqual(next(counter), 100, "exactly 100 calls")

    # --- Shapes some banks send ------------------------------------------------------------

    def test_signed_amounts_are_not_flipped_twice(self):
        lines, _ = self._pull([
            dict(DEBIT, transaction_amount={"currency": "SEK", "amount": "-44445.00"}),
            dict(SWISH, transaction_amount={"currency": "SEK", "amount": "-1000.00"}),
            dict(DEBIT, credit_debit_indicator=None, transaction_amount={"currency": "SEK", "amount": "-12.50"}),
        ])
        self.assertEqual([line["amount"] for line in lines], [-44445.0, 1000.0, -12.5])

    def test_transaction_code_as_a_string(self):
        lines, _ = self._pull([dict(SWISH, bank_transaction_code="Swish"), dict(DEBIT, bank_transaction_code=None)])
        self.assertEqual(lines[0]["transaction_type"], "Swish")
        self.assertEqual(lines[0]["payment_ref"], "1234567890 Swish +46700000000", "Swish still recognised")
        self.assertEqual(lines[1]["transaction_type"], "")

    def test_remittance_as_a_string(self):
        lines, _ = self._pull([dict(BANKGIRO, remittance_information="Faktura 17")])
        self.assertEqual(lines[0]["payment_ref"], "Bankgiro inbetalning Faktura 17")

    def test_line_hash_is_unchanged(self):
        """The id of a line imported by earlier versions must not change, or it is imported again."""
        tr, day = SWISH, "2026-09-14"
        amount = tr["transaction_amount"]
        parts = [
            day, tr["credit_debit_indicator"], str(amount["amount"]), amount["currency"],
            "|".join(tr["remittance_information"]), tr["debtor"]["name"], tr["creditor"]["name"],
            tr["debtor_account"]["other"]["identification"], tr["bank_transaction_code"]["description"],
        ]
        expected = hashlib.sha1("\x1f".join(parts).encode()).hexdigest()[:20]
        self.assertEqual(self.provider._enable_banking_line_hash(tr, day), expected)

    # --- A session the bank has ended; one consent for several accounts ------------------------

    def _sibling(self, iban, code, aspsp="Mock ASPSP", currency=None, app="app-id"):
        bank = self.env["res.partner.bank"].create({"acc_number": iban, "partner_id": self.company.partner_id.id})
        journal = self.env["account.journal"].create({
            "name": f"EB {code}", "type": "bank", "code": code, "company_id": self.company.id,
            "bank_account_id": bank.id, **({"currency_id": currency.id} if currency else {}),
        })
        return self.env["online.bank.statement.provider"].create({
            "journal_id": journal.id, "service": "enable_banking", "username": app,
            "certificate_private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-key\n-----END PRIVATE KEY-----",
            "eb_aspsp_name": aspsp, "eb_session_id": "old", "eb_account_uid": "old-uid",
            "eb_session_valid_until": fields.Datetime.now() + timedelta(days=30),
        })

    def test_ended_session_is_marked_at_once(self):
        """401 EXPIRED_SESSION: every provider on the session gets a to-do now, not when the consent expires."""
        sibling = self._sibling("SE0000000000000000000002", "EBS")
        sibling.eb_session_id = self.provider.eb_session_id

        def ended(provider, method, path, params=None, body=None):
            raise _eb_error("Enable Banking refused the request (401 EXPIRED_SESSION).", status=401, code="EXPIRED_SESSION")

        since = datetime.combine(fields.Date.today() - timedelta(days=1), datetime.min.time())
        notes, _log = self._scheduled(since, 1, ended)
        now = fields.Datetime.now()
        for provider in self.provider | sibling:
            self.assertLessEqual(provider.eb_session_valid_until, now)
            self.assertTrue(provider._enable_banking_renewal_activities(), provider.journal_id.code)
            self.assertIn("ended the connection", provider.message_ids[0].body)
        self.assertEqual(self.provider.last_successful_run, since, "pulled again after the new authorisation")
        # the next run does not call the bank: no account data without a consent
        with mock.patch(f"{PROVIDER}._eb_request", side_effect=AssertionError("no call")), contextlib.suppress(UserError):
            self.provider._obtain_statement_data(since, since + timedelta(days=1))

    def test_ended_session_makes_the_open_reminder_urgent(self):
        """The reminder before expiry (due at the old end date) becomes the ended-connection to-do, due today."""
        self.provider.activity_schedule("mail.mail_activity_data_todo", summary="Enable Banking: renew the bank consent (expires in 10 days)",
                                        user_id=self.env.uid, date_deadline=fields.Date.today() + timedelta(days=10))

        def ended(provider, method, path, params=None, body=None):
            raise _eb_error("Enable Banking refused the request (401 EXPIRED_SESSION).", status=401, code="EXPIRED_SESSION")

        since = datetime.combine(fields.Date.today() - timedelta(days=1), datetime.min.time())
        self._scheduled(since, 1, ended)
        todo = self.provider._enable_banking_renewal_activities()
        self.assertEqual(len(todo), 1)
        self.assertIn("ended the connection", todo.summary)
        self.assertEqual(todo.date_deadline, fields.Date.today())
        body = self.provider.message_ids[0].body
        self.assertIn("<i>", body, "the note is HTML, not escaped text")
        self.assertNotIn("&lt;i&gt;", body)

    def test_error_code_is_read_case_insensitively(self):
        err, _log = self._error(status=401, json_body={"error": "expired_session"})
        self.assertEqual(err.eb_code, "EXPIRED_SESSION")

    def test_refused_without_a_session_code_is_not_an_ended_session(self):
        """A 401 without a session code (a bad application key) keeps the consent and makes no to-do."""
        valid_until = self.provider.eb_session_valid_until

        def refused(provider, method, path, params=None, body=None):
            raise _eb_error("Enable Banking refused the request (401).", status=401)

        since = datetime.combine(fields.Date.today() - timedelta(days=1), datetime.min.time())
        self._scheduled(since, 1, refused)
        self.assertEqual(self.provider.eb_session_valid_until, valid_until)
        self.assertFalse(self.provider._enable_banking_renewal_activities())

    def test_one_consent_connects_the_other_accounts(self):
        """SEB allows one session per person: the other providers whose account is in the consent join it."""
        savings = self._sibling("SE0000000000000000000002", "EBS")
        other_currency = self._sibling("SE0000000000000000000003", "EBE", currency=self.env.ref("base.EUR"))
        other_bank = self._sibling("SE0000000000000000000004", "EBO", aspsp="Other Bank")
        other_app = self._sibling("SE0000000000000000000005", "EBA", app="another-app")
        savings.activity_schedule("mail.mail_activity_data_todo", summary="Enable Banking: renew", user_id=self.env.uid)
        session = {"session_id": "shared", "access": {"valid_until": "2027-03-01T10:00:00+00:00"}, "accounts": [
            {"uid": "mine", "account_id": {"iban": "SE0000000000000000000001"}},
            {"uid": "savings", "account_id": {"iban": "SE0000000000000000000002"}},
            {"uid": "eur", "currency": "SEK", "account_id": {"iban": "SE0000000000000000000003"}},
            {"uid": "elsewhere", "account_id": {"iban": "SE0000000000000000000004"}},
            {"uid": "other-app", "account_id": {"iban": "SE0000000000000000000005"}},
        ]}
        with mock.patch(f"{PROVIDER}._eb_request", return_value=session):
            self.assertTrue(self.provider._enable_banking_finish_authorization("code"))
        self.assertEqual((savings.eb_session_id, savings.eb_account_uid), ("shared", "savings"))
        self.assertEqual(savings.eb_session_valid_until, self.provider.eb_session_valid_until)
        self.assertFalse(savings._enable_banking_renewal_activities(), "its to-do is done")
        self.assertTrue(any("same consent" in m.body for m in savings.message_ids), savings.message_ids.mapped("body"))
        self.assertIn("EB EBS", self.provider.message_ids[0].body)
        self.assertEqual(other_currency.eb_session_id, "old", "another currency is not connected")
        self.assertEqual(other_bank.eb_session_id, "old", "another bank is not connected")
        self.assertEqual(other_app.eb_session_id, "old", "a session belongs to its Enable Banking application")

    def test_consent_without_this_account_still_connects_the_others(self):
        """Authorised from a provider whose own account is not in the consent: the others still join."""
        savings = self._sibling("SE0000000000000000000002", "EBS")
        session = {"session_id": "shared", "access": {}, "accounts": [
            {"uid": "savings", "account_id": {"iban": "SE0000000000000000000002"}}]}
        with mock.patch(f"{PROVIDER}._eb_request", return_value=session):
            self.assertFalse(self.provider._enable_banking_finish_authorization("code"))
        self.assertEqual((savings.eb_session_id, savings.eb_account_uid), ("shared", "savings"))

    # --- A journal also fed by the Swedbank CSV import ---------------------------------------

    def _file_line(self, day, amount, text="Swish", n=0):
        """A line the Swedbank CSV import made (its id carries SWED-)."""
        stmt = self.env["account.bank.statement"].create({"journal_id": self.journal.id, "name": "Swedbank file"})
        return self.env["account.bank.statement.line"].create({
            "journal_id": self.journal.id, "statement_id": stmt.id, "date": day, "amount": amount,
            "payment_ref": text, "unique_import_id": f"1234567890-{self.journal.id}-SWED-1234567890-{day}-{amount:.2f}-{n}",
        })

    def _look_alike_todos(self):
        return self.provider.activity_ids.filtered(lambda a: "duplicates" in (a.summary or ""))

    def test_look_alikes_of_file_lines_are_imported_and_reported(self):
        """A line with the day and amount of a line from the Swedbank file could be the same payment or
        another one: it is imported (nothing is lost) and the user is told to check - a note and one
        to-do, which is not taken for the consent renewal to-do."""
        self._file_line("2026-09-14", 1000.0)
        lines, _ = self._pull([SWISH, BANKGIRO])
        self.assertEqual(sorted(v["amount"] for v in lines), [1000.0, 2000.0], "nothing left out")
        self.assertIn("1 with the day and amount of a line from a Swedbank file", self.provider.eb_last_pull_summary)
        self.assertTrue(any("check that they are not the same payments" in m.body for m in self.provider.message_ids))
        self.assertEqual(len(self._look_alike_todos()), 1)
        self.assertFalse(self.provider._enable_banking_renewal_activities())
        self._pull([SWISH, BANKGIRO])
        self.assertEqual(len(self._look_alike_todos()), 1, "one open to-do at a time")

    def test_look_alikes_only_among_the_lines_the_pull_imports(self):
        """A line outside the period (the bank filters on its own date) or already imported is not
        imported by this pull, so it is not reported either."""
        self._file_line("2026-09-14", 1000.0)
        self._pull([SWISH, BANKGIRO], since=datetime(2026, 9, 15))
        self.assertNotIn("Swedbank file", self.provider.eb_last_pull_summary)
        lines, _ = self._pull([SWISH])
        probe = {"unique_import_id": lines[0]["unique_import_id"]}
        self.journal._statement_line_import_update_unique_import_id(probe, self.provider.account_number)
        stmt = self.env["account.bank.statement"].create({"journal_id": self.journal.id, "name": "Enable Banking"})
        self.env["account.bank.statement.line"].create({
            "journal_id": self.journal.id, "statement_id": stmt.id, "date": "2026-09-14", "amount": 1000.0,
            "payment_ref": "Swish", "unique_import_id": probe["unique_import_id"]})
        self._pull([SWISH])
        self.assertNotIn("Swedbank file", self.provider.eb_last_pull_summary)

    def test_look_alike_is_the_same_signed_amount_on_the_day(self):
        self._file_line("2026-09-14", -1000.0)
        self._file_line("2026-09-13", 1000.0)
        self._file_line("2026-09-15", 1000.5)
        self._pull([SWISH])
        self.assertNotIn("Swedbank file", self.provider.eb_last_pull_summary)
        self.assertFalse(self._look_alike_todos())

    def test_look_alikes_by_value_date_look_a_few_days_either_way(self):
        """The file has the booking day; a line dated by value day can be off by a weekend."""
        self.provider.eb_date_type = "value_date"
        self._file_line("2026-09-12", 1000.0)
        (line,), _ = self._pull([dict(SWISH, booking_date="2026-09-12", value_date="2026-09-14")])
        self.assertEqual(str(line["date"]), "2026-09-14")
        self.assertIn("1 with the day and amount of a line from a Swedbank file", self.provider.eb_last_pull_summary)

    def test_without_file_lines_nothing_is_reported(self):
        lines, _ = self._pull([SWISH, dict(SWISH, remittance_information=["other text"])])
        self.assertEqual(len(lines), 2)
        self.assertNotIn("Swedbank file", self.provider.eb_last_pull_summary or "")
        self.assertFalse(self._look_alike_todos())

    # --- Connecting an account -------------------------------------------------------------

    def test_account_in_another_currency_is_not_connected(self):
        journal_currency = (self.journal.currency_id or self.company.currency_id).name
        other = "EUR" if journal_currency != "EUR" else "SEK"
        session = {"session_id": "s", "access": {}, "accounts": [
            {"uid": "mine", "currency": other, "account_id": {"iban": "SE0000000000000000000001"}}]}
        with mock.patch(f"{PROVIDER}._eb_request", return_value=session):
            self.assertFalse(self.provider._enable_banking_finish_authorization("code"))
        self.assertFalse(self.provider.eb_account_uid)
        self.assertIn(f"is in {other}", self.provider.message_ids[0].body)
        session["accounts"][0]["currency"] = journal_currency
        with mock.patch(f"{PROVIDER}._eb_request", return_value=session):
            self.assertTrue(self.provider._enable_banking_finish_authorization("code"))
        self.assertEqual(self.provider.eb_account_uid, "mine")

    def test_pagination(self):
        pages = [{"transactions": [SWISH], "continuation_key": "k2"}, {"transactions": [BANKGIRO], "continuation_key": None}]
        calls = []

        def fake_request(provider, method, path, params=None, body=None):
            calls.append(params)
            return pages.pop(0)

        with mock.patch(f"{PROVIDER}._eb_request", new=fake_request):
            rows = self.provider._enable_banking_request_transactions(datetime(2026, 9, 1), datetime(2026, 9, 2))
        self.assertEqual(len(rows), 2)
        self.assertEqual(calls[1]["continuation_key"], "k2")
        self.assertEqual(calls[0]["date_to"], "2026-09-01", "date_to is inclusive, date_until exclusive")


    def test_open_statement_keeps_computed_end_when_bank_balance_lags(self):
        """Swedbank: a line booked today while the booked balance still excludes it must not
        leave the statement "incomplete" for good."""
        today = fields.Date.context_today(self.provider)
        stmt = self.env["account.bank.statement"].create({
            "name": "EBB/OPEN", "journal_id": self.journal.id, "balance_start": 1000.0,
            "line_ids": [(0, 0, {"date": today, "payment_ref": "utlägg", "amount": -807.0})],
        })
        stmt.write({"balance_end_real": 1000.0})  # what the bank said: without the -807
        self.assertFalse(stmt.is_complete)
        base = "odoo.addons.account_statement_import_online.models.online_bank_statement_provider.OnlineBankStatementProvider"
        before = len(self.provider.message_ids)
        with mock.patch(f"{base}._statement_create_or_write", return_value=stmt):
            out = self.provider._statement_create_or_write({"name": "EBB/OPEN", "balance_end_real": 1000.0})
        self.assertEqual(out, stmt)
        self.assertAlmostEqual(stmt.balance_end_real, 193.0, places=2)
        self.assertTrue(stmt.is_complete)
        self.assertEqual(len(self.provider.message_ids), before, "the lag itself is not news; the post-pull check decides")

    def test_past_statement_keeps_the_banks_balance(self):
        """A mismatch on a closed day is real and must stay visible."""
        stmt = self.env["account.bank.statement"].create({
            "name": "EBB/PAST", "journal_id": self.journal.id, "balance_start": 1000.0,
            "line_ids": [(0, 0, {"date": "2026-09-01", "payment_ref": "x", "amount": -10.0})],
        })
        stmt.write({"balance_end_real": 1000.0})
        base = "odoo.addons.account_statement_import_online.models.online_bank_statement_provider.OnlineBankStatementProvider"
        with mock.patch(f"{base}._statement_create_or_write", return_value=stmt):
            self.provider._statement_create_or_write({"name": "EBB/PAST", "balance_end_real": 1000.0})
        self.assertAlmostEqual(stmt.balance_end_real, 1000.0, places=2)
        self.assertFalse(stmt.is_complete)

    # --- Balance check after the pull (1.4.0) -------------------------------------------------

    def test_pick_balances(self):
        pick = self.provider._enable_banking_pick_balances
        self.assertEqual(pick([{"balance_type": "ITBD", "balance_amount": {"amount": "1040.00"}},
                               {"balance_type": "ITAV", "balance_amount": {"amount": "1100.00"}}]), (1040.0, 1100.0))
        self.assertEqual(pick([{"name": "Closing booked", "balance_type": "CLBD", "balance_amount": {"amount": "1"}}]), (1.0, None))
        self.assertEqual(pick([]), (None, None))

    def test_explained_by_lag(self):
        lag = self.provider._enable_banking_explained_by_lag
        self.assertTrue(lag(1080.0, 1100.0, [20.0] * 5), "one of five payments not yet in the booked balance")
        self.assertTrue(lag(1000.0, 1100.0, [20.0] * 5), "none of today's payments in the booked balance")
        self.assertTrue(lag(1000.0, 193.0, [-807.0]), "outgoing transfer not yet in the booked balance")
        self.assertFalse(lag(500.0, -500.0, [-500.0]), "wrong opening balance is a real difference")
        self.assertFalse(lag(1085.0, 1100.0, [20.0] * 5), "15 is not a combination of today's lines")

    def _statement_today(self, start, amounts, day=None, name=None):
        day = day or fields.Date.context_today(self.provider)
        return self.env["account.bank.statement"].create({
            "name": name or f"EBB/{day}", "journal_id": self.journal.id, "balance_start": start,
            "line_ids": [(0, 0, {"date": day, "payment_ref": f"swish {i}", "amount": a, "journal_id": self.journal.id})
                         for i, a in enumerate(amounts)],
        })

    def _bank(self, booked, available=None):
        self.provider.write({"eb_bank_booked_balance": booked, "eb_bank_available_balance": available or 0.0,
                             "eb_bank_has_available": available is not None, "eb_bank_balance_at": fields.Datetime.now()})

    def _check(self, booked, available=None):
        self._bank(booked, available)
        self.provider._enable_banking_check_balance()
        return self.provider.eb_balance_check

    def test_check_balance_outcomes(self):
        stmt = self._statement_today(1000.0, [20.0] * 5)  # Odoo 1100
        before = len(self.provider.message_ids)
        self.assertIn("available balance", self._check(1040.0, 1100.0))
        self.assertIn("lacks some of today's lines", self._check(1040.0))
        self.assertIn("lacks some of today's lines", self._check(1040.0, 1300.0), "pending incoming payments")
        self.assertEqual(len(self.provider.message_ids), before, "explained differences are not posted")
        self.assertIn("DIFFERENCE", self._check(1085.0))
        self.assertEqual(len(self.provider.message_ids), before + 1)
        self.assertIn("-15.00", self.provider.message_ids[0].body)
        self._check(1085.0)
        self.assertEqual(len(self.provider.message_ids), before + 1, "the same difference is posted once")
        stmt.write({"line_ids": [(0, 0, {"date": stmt.line_ids[0].date, "payment_ref": "swish 6", "amount": 20.0,
                                         "journal_id": self.journal.id})]})  # Odoo 1120
        self.assertIn("DIFFERENCE", self._check(1105.0))
        self.assertEqual(len(self.provider.message_ids), before + 1, "both balances moved, same difference: not repeated")
        self.assertIn("agrees with the bank", self._check(1120.0))
        self.assertFalse(self.provider.eb_balance_warning_key)

    def test_lag_does_not_hide_a_duplicate_import(self):
        """A duplicate 20 makes Odoo exceed the bank's available balance even if today's lines could explain it."""
        self._statement_today(1000.0, [20.0] * 5)  # Odoo 1100, but the bank only has 1080
        before = len(self.provider.message_ids)
        self.assertIn("DIFFERENCE", self._check(1040.0, 1080.0))
        self.assertEqual(len(self.provider.message_ids), before + 1)

    def test_check_balance_on_a_quiet_day(self):
        """No lines today: nothing can explain a difference, so it is reported."""
        yesterday = fields.Date.context_today(self.provider) - timedelta(days=1)
        self._statement_today(0.0, [20.0], day=yesterday)
        before = len(self.provider.message_ids)
        self.assertIn("DIFFERENCE", self._check(0.0))
        self.assertEqual(len(self.provider.message_ids), before + 1)

    def test_check_ignores_undated_statements_and_statementless_providers(self):
        self._statement_today(1000.0, [20.0])  # Odoo 1020
        self.env["account.bank.statement"].create({"name": "EBB/EMPTY", "journal_id": self.journal.id, "balance_start": 5.0})
        self.assertIn("agrees with the bank", self._check(1020.0), "an empty statement (no date) is not the last one")
        self.provider.create_statement = False
        self.provider.eb_balance_check = False
        self._check(0.0)
        self.assertFalse(self.provider.eb_balance_check, "without statements there is nothing to compare")

    def test_pull_runs_the_check(self):
        self._statement_today(0.0, [20.0])
        now = fields.Datetime.now()
        balances = [{"balance_type": "ITBD", "balance_amount": {"amount": "0.00"}},
                    {"balance_type": "ITAV", "balance_amount": {"amount": "20.00"}}]

        def fake_request(provider, method, path, params=None, body=None):
            if path.endswith("/transactions"):
                return {"transactions": [], "continuation_key": None}
            if path.endswith("/balances"):
                return {"balances": balances}
            raise AssertionError(path)

        with mock.patch(f"{PROVIDER}._eb_request", new=fake_request):
            self.provider._pull(now - timedelta(hours=1), now + timedelta(hours=1))
        self.assertIn("available balance", self.provider.eb_balance_check)

    def test_record_pull_counts_only_new_lines_in_the_period(self):
        since, until = datetime(2026, 9, 1), datetime(2026, 10, 1)
        data = self._pull([SWISH, DEBIT], since=since, until=until)
        self.provider._create_or_update_statement(data, since, until)
        before = len(self.provider.message_ids)
        self._pull([SWISH, DEBIT], since=since, until=until)
        self.assertIn("2 booked transaction(s) from the bank, 0 new", self.provider.eb_last_pull_summary)
        self.assertEqual(len(self.provider.message_ids), before, "already imported lines are not news")
        self._pull([BANKGIRO], since=datetime(2026, 9, 20), until=datetime(2026, 9, 21))
        self.assertIn("1 booked transaction(s) from the bank, 0 new", self.provider.eb_last_pull_summary,
                      "a line dated outside the period is not counted for it")

    def test_swish_personal_account_matches_payer(self):
        lines, _ = self._pull([SWISH_PERSONAL])
        self.assertEqual(lines[0]["partner_id"], self.payer.id, "number first in the remittance, personal account format")
        self.env["res.partner"].create({"name": "Same Number", "phone": "+46700000000"})
        lines, _ = self._pull([SWISH_PERSONAL])
        self.assertFalse(lines[0].get("partner_id"), "two partners with the number: no guess")

