# All account numbers are invented; they only satisfy the check digits.
from odoo.addons.l10n_se_bank_account.lib import se_bank
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

SEB_IBAN = "SE3150000000052031234560"  # SEB 5203-123 45 60
SWEDBANK_IBAN = "SE7280000832791234567897"  # Swedbank 8327-9, 123 456 789-7


@tagged("post_install", "-at_install", "l10n_se")
class TestResPartnerBank(TransactionCase):
    def _bank(self, acc_number, **vals):
        # one partner per account: "5551-2347" and "55512347" sanitize to the same number
        partner = self.env["res.partner"].create({"name": "Test Supplier AB", "is_company": True})
        return self.env["res.partner.bank"].create(
            {"acc_number": acc_number, "partner_id": partner.id, **vals}
        )

    def test_account_type_guess(self):
        cases = {
            "BG 555-1239": "bankgiro",
            "5551-2347": "bankgiro",
            "PG 12 34 56-6": "plusgiro",
            "PG 555 12 34-7SEK": "plusgiro",
            "8327-9 123 456 789-7": "bban",
            "6789 123 456 789": "bban",
            SWEDBANK_IBAN: "iban",
            "SE36 5000 0000 0520 3123 4567": "iban",
            "55512347": "other",
            "BG 5551-234567890123": "bankgiro",
        }
        for acc, expected in cases.items():
            with self.subTest(acc=acc):
                self.assertEqual(self._bank(acc).l10n_se_account_type, expected)

    def test_manual_type_is_kept_until_the_number_changes(self):
        bank = self._bank("55512347")
        self.assertEqual(bank.l10n_se_account_type, "other")
        self.assertFalse(bank.l10n_se_payment_number)
        self.assertFalse(bank.l10n_se_account_problem)
        bank.l10n_se_account_type = "plusgiro"
        self.assertEqual(bank.l10n_se_account_type, "plusgiro")
        self.assertEqual(bank._l10n_se_payment_account(), "55512347")
        bank.acc_number = "BG 555-1239"
        self.assertEqual(bank.l10n_se_account_type, "bankgiro")

    def test_clearing_number_field(self):
        bank = self._bank("123 45 60")
        self.assertEqual(bank.l10n_se_account_type, "other")
        bank.clearing_number = "5203"
        self.assertEqual(bank.l10n_se_account_type, "bban")
        self.assertEqual(bank.l10n_se_payment_number, "52031234560")
        self.assertEqual(bank.l10n_se_clearing_bank, "SEB")
        self.assertEqual(bank._l10n_se_bban().clearing, "5203")

    def test_payment_number_per_bank(self):
        cases = {
            "BG 555-1239": ("5551239", False),
            "PG 12 34 56-6": ("1234566", False),
            "6789 123 456 789": ("123456789", "Handelsbanken"),
            "8327-9, 12 345 678-2": ("832790123456782", "Swedbank"),
            "9180-1234567897": ("1234567897", "Danske Bank"),
            "9570 123 456 789 7": ("95701234567897", "Sparbanken Syd"),
            "5203-123 45 60": ("52031234560", "SEB"),
            "SE72 8000 0832 7912 3456 7897": (SWEDBANK_IBAN, False),
        }
        for acc, (number, bank_name) in cases.items():
            with self.subTest(acc=acc):
                bank = self._bank(acc)
                self.assertEqual(bank.l10n_se_payment_number, number)
                self.assertEqual(bank._l10n_se_payment_account(), number)
                self.assertEqual(bank.l10n_se_clearing_bank, bank_name)
                self.assertFalse(bank.l10n_se_account_problem)
                self.assertFalse(bank._l10n_se_account_problem_message())

    def test_problems(self):
        cases = {
            "BG 555-1238": "invalid check digit",
            "BG 5551-234567890123": "has 16 digits",
            "PG 12 34 56-7": "invalid check digit",
            "6789 12345678": "expects 9 digits",
            "8327-5 123 456 789-7": "invalid check digit (expected 83279)",
            "5203-123 45 67": "invalid check digit for clearing number 5203 (SEB)",
            "9180-1234567890": "invalid check digit for clearing number 9180 (Danske Bank)",
            "812345678": "cannot tell where the Swedbank clearing number ends",
            "BG 000-0000": "only zeros",
        }
        for acc, text in cases.items():
            with self.subTest(acc=acc):
                bank = self._bank(acc)
                self.assertIn(text, bank.l10n_se_account_problem)
                self.assertFalse(bank.l10n_se_payment_number)
                self.assertEqual(bank._l10n_se_account_problem_message(), bank.l10n_se_account_problem)
                with self.assertRaisesRegex(UserError, text.replace("(", r"\(").replace(")", r"\)")):
                    bank._l10n_se_payment_account()
        other = self._bank("55512347")
        with self.assertRaisesRegex(UserError, "no known Swedish account type"):
            other._l10n_se_payment_account()
        with self.assertRaises(UserError):
            self._bank("BG 555-1239")._l10n_se_bban()

    def test_check_digit_warnings(self):
        cases = {
            "5203-123 45 60": False,
            "8327-9 123 456 789-7": False,
            "BG 555-1239": False,
            "8327-9 123 456 789-0": "does not match. Swedbank has a few accounts like this",
            "9960 123456-7": "does not match. Nordea (PlusGirot)",
            "9990 1234567": "Clearing number 9990 is not in the Swedish banks' list",
        }
        for acc, text in cases.items():
            with self.subTest(acc=acc):
                bank = self._bank(acc)
                self.assertFalse(bank.l10n_se_account_problem)
                self.assertTrue(bank.l10n_se_payment_number)
                if text:
                    self.assertIn(text, bank.l10n_se_account_warning)
                    self.assertEqual(bank._l10n_se_account_warning_message(), bank.l10n_se_account_warning)
                else:
                    self.assertFalse(bank.l10n_se_account_warning)
        # an unusable number has a problem, not a warning
        self.assertFalse(self._bank("5203-123 45 67").l10n_se_account_warning)

    def test_bic(self):
        self.assertEqual(self._bank(SEB_IBAN)._l10n_se_bic(), "ESSESESS")
        self.assertEqual(self._bank(SWEDBANK_IBAN)._l10n_se_bic(), "SWEDSESS")
        self.assertFalse(self._bank("BG 555-1239")._l10n_se_bic())
        bank = self.env["res.bank"].create({"name": "Test bank", "bic": "TESTSESS"})
        self.assertEqual(self._bank("5203-123 45 60", bank_id=bank.id)._l10n_se_bic(), "TESTSESS")

    def test_domestic_bban(self):
        self.assertEqual(self._bank(SEB_IBAN)._l10n_se_domestic_bban(), "52031234560")
        self.assertEqual(self._bank("5203-123 45 60")._l10n_se_domestic_bban(), "52031234560")
        handelsbanken = "SE7160000000000123456789"  # account-only bank: no clearing number in the IBAN
        self.assertTrue(se_bank.iban_valid(handelsbanken))
        with self.assertRaisesRegex(UserError, "cannot be derived"):
            self._bank(handelsbanken)._l10n_se_domestic_bban()
        with self.assertRaises(UserError):
            self._bank("BG 555-1239")._l10n_se_domestic_bban()

    def test_account_digits(self):
        self.assertEqual(self._bank("PG 555 12 34-7SEK")._l10n_se_account_digits(), "55512347")

    def test_every_error_code_has_a_translated_template(self):
        templates = self.env["res.partner.bank"]._l10n_se_error_templates()
        self.assertEqual(set(templates), set(se_bank.MESSAGES))
        for code, english in se_bank.MESSAGES.items():
            self.assertEqual(templates[code], english, code)

    def test_swedish_translation(self):
        self.env["res.lang"]._activate_lang("sv_SE")
        self.env["ir.module.module"]._load_module_terms(["l10n_se_bank_account"], ["sv_SE"], overwrite=True)
        bank = self._bank("BG 555-1238").with_context(lang="sv_SE")
        self.assertIn("ogiltig kontrollsiffra", bank.l10n_se_account_problem)
        field = bank.env["ir.model.fields"]._get("res.partner.bank", "l10n_se_account_type")
        self.assertEqual(field.with_context(lang="sv_SE").field_description, "Svensk kontotyp")
        labels = dict(
            bank.fields_get(["l10n_se_account_type"])["l10n_se_account_type"]["selection"]
        )
        self.assertEqual(labels["bban"], "Bankkonto (clearing + konto)")
        templates = bank.env["res.partner.bank"]._l10n_se_error_templates()
        for code, template in templates.items():
            with self.subTest(code=code):
                self.assertNotEqual(template, se_bank.MESSAGES[code], "untranslated")
                # the Swedish text uses the same placeholders
                params = {"number": "X", "length": 1, "clearing": "1", "bank": "", "expected": "1"}
                self.assertTrue(template % params)
        warning = self._bank("8327-9 123 456 789-0").with_context(lang="sv_SE")
        self.assertIn("Kontrollsiffran", warning.l10n_se_account_warning)
