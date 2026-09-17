# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from datetime import datetime, timedelta
from unittest import mock

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

PROVIDER = "odoo.addons.account_statement_import_online_enable_banking.models.online_bank_statement_provider.OnlineBankStatementProvider"

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
        self.provider.eb_session_valid_until = fields.Datetime.now() - timedelta(days=1)
        lines, values = self._pull([SWISH])
        self.assertEqual((lines, values), ([], {}))
        self.assertIn("expired", self.provider.message_ids[0].body)

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
        self.assertTrue(same("SE38 8000 0830 5500 4537 3453", "8305-5 004 537 3453"))
        self.assertTrue(same("SE3880000830550045373453", "SE3880000830550045373453"))
        self.assertFalse(same("SE3880000830550045373453", "8305-5 004 509 2582"))
        self.assertFalse(same("SE3880000830550045373453", "3453"), "too short to be an account number")
        self.bank_account.acc_number = "8305-5 004 537 3453"
        # IBAN in the test data is all zeros; a domestic number that is its suffix must bind.
        session = {"session_id": "s", "access": {}, "accounts": [{"uid": "mine", "account_id": {"iban": "SE00 0000 0000 0000 4537 3453"}}]}
        self.bank_account.acc_number = "0000 4537 3453"
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
