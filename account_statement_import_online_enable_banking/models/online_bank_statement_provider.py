# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Enable Banking (https://enablebanking.com) as an online statement provider.

Enable Banking is a PSD2 account-information aggregator: one API in front of
most Nordic and European banks. Authentication is a JWT signed with the RSA key
generated when the application was registered in their control panel; the
end user (PSU) then grants access to one or more accounts through the bank's
own consent screen (BankID in Sweden), which yields a session valid for up to
180 days.

Flow in Odoo:
  1. On the provider: application id, private key, bank name (ASPSP), country
     and PSU type (business/personal).
  2. *Authorise with the bank* opens the consent screen; the bank redirects to
     `/enable_banking/callback` and the session is bound to the provider. The
     account is picked by IBAN when the journal has a bank account, otherwise
     the first account of the session.
  3. The OCA scheduler pulls transactions per period like any other provider.
"""
import base64
import hashlib
import json
import logging
import re
import secrets
import time
from datetime import UTC, datetime, timedelta

import requests
from werkzeug.urls import url_join

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

EB_API_BASE = "https://api.enablebanking.com"
EB_DATE_FORMAT = "%Y-%m-%d"
# Bank-side maximum for AIS consents under PSD2 (Enable Banking reports it per
# ASPSP as `maximum_consent_validity`); used when the ASPSP lookup fails.
EB_DEFAULT_CONSENT_DAYS = 90
SWISH_RE = re.compile(r"Swish\s+(\+?\d[\d \-]{6,})", re.I)


class OnlineBankStatementProvider(models.Model):
    _name = "online.bank.statement.provider"
    _inherit = ["online.bank.statement.provider", "mail.activity.mixin"]

    # `username` (base field) holds the application id and
    # `certificate_private_key` (base field) the PEM private key.
    eb_aspsp_name = fields.Char(string="Bank (ASPSP name)", help="Exactly as listed by Enable Banking, e.g. 'Swedbank'.")
    eb_aspsp_country = fields.Char(string="Bank country", size=2, default="SE")
    eb_psu_type = fields.Selection(
        [("business", "Business"), ("personal", "Personal")], string="Account holder type", default="business"
    )
    eb_date_type = fields.Selection(
        [("booking_date", "Booking date"), ("value_date", "Value date")], string="Statement line date", default="booking_date"
    )
    eb_renewal_warn_days = fields.Integer(
        string="Warn before consent expires (days)", default=14,
        help="A to-do is scheduled for the renewal user this many days before the bank consent expires.",
    )
    eb_renewal_user_id = fields.Many2one(
        "res.users", string="Renewal user", default=lambda self: self.env.user,
        help="Gets the renewal to-do and the chatter notification. Must be able to authorise with the bank.",
    )
    eb_last_pull = fields.Datetime(string="Last pull", readonly=True, copy=False)
    eb_last_pull_summary = fields.Char(string="Last pull result", readonly=True, copy=False)
    eb_auth_state = fields.Char(readonly=True, copy=False)
    eb_session_id = fields.Char(readonly=True, copy=False)
    eb_session_valid_until = fields.Datetime(readonly=True, copy=False)
    eb_account_uid = fields.Char(readonly=True, copy=False)
    eb_account_iban = fields.Char(readonly=True, copy=False)

    @api.model
    def _get_available_services(self):
        return super()._get_available_services() + [("enable_banking", "Enable Banking")]

    # ------------------------------------------------------------------ API
    def _eb_jwt(self):
        """RS256 JWT with kid = application id, as Enable Banking specifies."""
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        self.ensure_one()
        if not self.username or not self.certificate_private_key:
            raise UserError(self.env._("Enable Banking: application id and private key are required."))
        key = serialization.load_pem_private_key(self.certificate_private_key.encode(), password=None)
        now = int(time.time())

        def b64(raw):
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        header = b64(json.dumps({"typ": "JWT", "alg": "RS256", "kid": self.username}).encode())
        payload = b64(
            json.dumps({"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + 3600}).encode()
        )
        signature = key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{payload}.{b64(signature)}"

    def _eb_request(self, method, path, params=None, body=None):
        """One API call. Isolated so tests can mock the transport."""
        self.ensure_one()
        url = url_join((self.api_base or EB_API_BASE).rstrip("/") + "/", path.lstrip("/"))
        headers = {
            "Authorization": "Bearer " + self._eb_jwt(),
            "Content-Type": "application/json",
            "User-Agent": "odoo-account_statement_import_online_enable_banking/19.0",
        }
        response = requests.request(method, url, params=params, json=body, headers=headers, timeout=60)
        if response.status_code >= 400:
            detail = response.text[:500]
            _logger.warning("Enable Banking %s %s -> %s %s", method, path, response.status_code, detail)
            raise UserError(self.env._("Enable Banking answered %(status)s: %(detail)s", status=response.status_code, detail=detail))
        return response.json()

    # ------------------------------------------------------------ authorise
    def action_enable_banking_check_aspsp(self):
        """Confirm that the bank name exists and post its PSU types in the chatter."""
        self.ensure_one()
        aspsps = self._eb_request("GET", "/aspsps", params={"country": self.eb_aspsp_country or "SE"})["aspsps"]
        match = [a for a in aspsps if a["name"].lower() == (self.eb_aspsp_name or "").lower()]
        if not match:
            names = ", ".join(sorted(a["name"] for a in aspsps))
            raise UserError(self.env._("No bank named %(name)s in %(country)s. Available: %(names)s", name=self.eb_aspsp_name, country=self.eb_aspsp_country, names=names))
        a = match[0]
        self.eb_aspsp_name = a["name"]
        self.message_post(
            body=self.env._(
                "Bank %(name)s found: account holder types %(types)s, max consent %(days)s days.",
                name=a["name"], types=", ".join(a.get("psu_types") or []),
                days=(a.get("maximum_consent_validity") or 0) // 86400,
            )
        )
        return True

    def _enable_banking_consent_days(self):
        try:
            aspsps = self._eb_request("GET", "/aspsps", params={"country": self.eb_aspsp_country or "SE"})["aspsps"]
            for a in aspsps:
                if a["name"].lower() == (self.eb_aspsp_name or "").lower() and a.get("maximum_consent_validity"):
                    return max(1, min(180, a["maximum_consent_validity"] // 86400))
        except Exception:  # noqa: BLE001 - the default is fine when the lookup fails
            _logger.info("Enable Banking: ASPSP lookup failed, using %s days", EB_DEFAULT_CONSENT_DAYS)
        return EB_DEFAULT_CONSENT_DAYS

    def action_enable_banking_authorize(self):
        """Start the consent flow: returns an act_url to the bank's screen."""
        self.ensure_one()
        if not self.eb_aspsp_name:
            raise UserError(self.env._("Enable Banking: set the bank name first."))
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        redirect_url = url_join(base_url, "/enable_banking/callback")
        state = secrets.token_urlsafe(24)
        valid_until = datetime.now(UTC) + timedelta(days=self._enable_banking_consent_days())
        response = self._eb_request(
            "POST", "/auth",
            body={
                "access": {"valid_until": valid_until.strftime("%Y-%m-%dT%H:%M:%S+00:00")},
                "aspsp": {"name": self.eb_aspsp_name, "country": self.eb_aspsp_country or "SE"},
                "state": state,
                "redirect_url": redirect_url,
                "psu_type": self.eb_psu_type or "business",
            },
        )
        self.write({"eb_auth_state": state})
        return {"type": "ir.actions.act_url", "url": response["url"], "target": "self"}

    @staticmethod
    def _enable_banking_same_account(iban, own_number):
        """Does the bank's IBAN denote the journal's bank account?

        Journals often carry the domestic number (Swedish `8305-5 004 537 3453`)
        rather than the IBAN; the IBAN's account part is that number zero-padded,
        so a digit-suffix match is the reliable comparison."""
        iban = re.sub(r"\s", "", iban or "").upper()
        own = re.sub(r"\s", "", own_number or "").upper()
        if not iban or not own:
            return False
        if iban == own:
            return True
        own_digits = re.sub(r"\D", "", own)
        return len(own_digits) >= 8 and re.sub(r"\D", "", iban).endswith(own_digits)

    def _enable_banking_finish_authorization(self, code):
        """Exchange the callback code for a session and bind the right account."""
        self.ensure_one()
        session = self._eb_request("POST", "/sessions", body={"code": code})
        accounts = session.get("accounts") or []
        own_iban = self.journal_id.bank_account_id.sanitized_acc_number or ""
        chosen = None
        if own_iban:
            for acc in accounts:
                if self._enable_banking_same_account((acc.get("account_id") or {}).get("iban"), own_iban):
                    chosen = acc
                    break
        elif len(accounts) == 1:
            chosen = accounts[0]
        vals = {
            "eb_auth_state": False,
            "eb_session_id": session.get("session_id"),
            "eb_session_valid_until": self._eb_parse_datetime((session.get("access") or {}).get("valid_until")),
        }
        if chosen:
            vals.update({"eb_account_uid": chosen["uid"], "eb_account_iban": (chosen.get("account_id") or {}).get("iban")})
            self.write(vals)
            self.message_post(body=self.env._("Enable Banking: connected to account %s.", vals["eb_account_iban"] or chosen["uid"]))
            self._enable_banking_renewal_activities().action_feedback(feedback=self.env._("Consent renewed."))
        else:
            vals.update({"eb_account_uid": False, "eb_account_iban": False})
            self.write(vals)
            ibans = ", ".join((a.get("account_id") or {}).get("iban") or a.get("uid") for a in accounts) or "-"
            self.message_post(
                body=self.env._(
                    "Enable Banking: the session has %(n)s account(s) (%(ibans)s) but none matches the journal's bank account%(own)s. Set the IBAN on the journal and authorise again.",
                    n=len(accounts), ibans=ibans, own=f" ({own_iban})" if own_iban else "",
                )
            )
        return chosen is not None

    # -------------------------------------------------------------- renewal
    def _enable_banking_renewal_activities(self):
        return self.activity_ids.filtered(lambda a: a.summary and a.summary.startswith("Enable Banking"))

    @api.model
    def _enable_banking_check_consents(self):
        """Daily cron: schedule a to-do for the renewal user when a consent is about to expire.

        The scheduled pull only complains once the consent *has* expired, and a
        180-day consent is easy to forget; this gives the human a head start."""
        now = fields.Datetime.now()
        providers = self.search([("service", "=", "enable_banking"), ("eb_session_valid_until", "!=", False)])
        for provider in providers:
            days_left = (provider.eb_session_valid_until - now).days
            if days_left > (provider.eb_renewal_warn_days or 0):
                continue
            if provider._enable_banking_renewal_activities():
                continue
            user = provider.eb_renewal_user_id or provider.create_uid
            if days_left >= 0:
                summary = self.env._("Enable Banking: renew the bank consent (expires in %s days)", days_left)
            else:
                summary = self.env._("Enable Banking: bank consent expired, authorise again")
            note = self.env._(
                "The consent for %(journal)s expires %(when)s. Open the provider and click "
                "<i>Authorise with the bank</i> (BankID as the account's signatory).",
                journal=provider.journal_id.display_name, when=fields.Datetime.to_string(provider.eb_session_valid_until),
            )
            provider.activity_schedule(
                "mail.mail_activity_data_todo", summary=summary, note=note, user_id=user.id,
                date_deadline=max(provider.eb_session_valid_until.date(), now.date()),
            )
            provider.message_post(body=summary + ". " + note, partner_ids=user.partner_id.ids)
        return True

    def action_enable_banking_reset(self):
        self.write({"eb_auth_state": False, "eb_session_id": False, "eb_session_valid_until": False, "eb_account_uid": False, "eb_account_iban": False})
        return True

    @staticmethod
    def _eb_parse_datetime(value):
        if not value:
            return False
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt

    # ----------------------------------------------------------------- pull
    def _obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        if self.service != "enable_banking":
            return super()._obtain_statement_data(date_since, date_until)
        return self._enable_banking_obtain_statement_data(date_since, date_until)

    def _enable_banking_request_transactions(self, date_since, date_until):
        """All booked transactions in [date_since, date_until), following pagination."""
        params = {"date_from": date_since.strftime(EB_DATE_FORMAT)}
        # The API's date_to is inclusive and rejects the future; the base module's
        # date_until is exclusive.
        last = min(date_until - timedelta(days=1), fields.Datetime.now())
        if last >= date_since:
            params["date_to"] = last.strftime(EB_DATE_FORMAT)
        transactions = []
        while True:
            data = self._eb_request("GET", f"/accounts/{self.eb_account_uid}/transactions", params=params)
            transactions += data.get("transactions") or []
            if not data.get("continuation_key"):
                return transactions
            params = dict(params, continuation_key=data["continuation_key"])

    def _enable_banking_request_balances(self):
        data = self._eb_request("GET", f"/accounts/{self.eb_account_uid}/balances")
        return data.get("balances") or []

    def _enable_banking_obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        if not self.eb_account_uid or not self.eb_session_id:
            self.sudo().message_post(body=self.env._("Enable Banking: no account connected. Authorise with the bank first."))
            return [], {}
        if self.eb_session_valid_until and self.eb_session_valid_until <= fields.Datetime.now():
            self.sudo().message_post(body=self.env._("Enable Banking: the bank consent has expired. Authorise with the bank again."))
            return [], {}
        try:
            transactions = self._enable_banking_request_transactions(date_since, date_until)
        except Exception as err:
            self.sudo().write({"eb_last_pull": fields.Datetime.now(), "eb_last_pull_summary": self.env._("FAILED: %s", err)[:250]})
            self.sudo().message_post(body=self.env._("Enable Banking: pull for %(since)s – %(until)s failed: %(err)s",
                                                     since=date_since.date(), until=(date_until - timedelta(days=1)).date(), err=err))
            raise
        own_iban = self.journal_id.bank_account_id.sanitized_acc_number or ""
        lines = []
        seen = {}
        for sequence, tr in enumerate(transactions, start=1):
            if (tr.get("status") or "BOOK") != "BOOK":
                continue
            date_string = tr.get(self.eb_date_type or "booking_date") or tr.get("booking_date") or tr.get("value_date")
            if not date_string:
                continue
            vals = self._enable_banking_line_vals(tr, own_iban)
            vals.update({"sequence": sequence, "date": fields.Date.from_string(date_string)})
            # Swedbank sends neither entry_reference nor transaction_id, so the
            # id is a hash of the stable fields plus an occurrence counter for
            # identical rows on the same day (three Swish payments of 1000 with
            # the same message are three lines, not one).
            if not vals["unique_import_id"]:
                key = self._enable_banking_line_hash(tr, date_string)
                seen[key] = seen.get(key, 0) + 1
                vals["unique_import_id"] = f"{key}-{seen[key]}"
            lines.append(vals)
        statement_values = {}
        note = ""
        # Balances are "now"; they only describe the period end when the period covers today.
        # PSD2 allows 4 unattended calls per day and account per service, so a failed
        # balance call (typically 429 after manual pulls) must not lose the transactions.
        if date_until > fields.Datetime.now() >= date_since:
            try:
                for bal in self._enable_banking_request_balances():
                    if bal.get("balance_type") in ("CLBD", "ITBD") or "booked" in (bal.get("name") or "").lower():
                        statement_values["balance_end_real"] = float((bal.get("balance_amount") or {}).get("amount") or 0)
                        break
            except Exception as err:  # noqa: BLE001 - transactions are already in hand
                _logger.warning("Enable Banking: balance call failed, statement imported without closing balance: %s", err)
                note = self.env._("; balance unavailable (%s)", str(err)[:80])
        self._enable_banking_match_swish_partners(lines)
        self._enable_banking_record_pull(date_since, date_until, transactions, lines, note)
        return lines, statement_values

    def _enable_banking_record_pull(self, date_since, date_until, transactions, lines, note=""):
        """Leave a trace of every pull on the provider; chatter only when something came in.

        `lines` is what the bank returned as booked for the period; the OCA base
        then skips the ones already imported (unique_import_id)."""
        period = f"{date_since.date()} – {(date_until - timedelta(days=1)).date()}"
        summary = self.env._("%(when)s — %(period)s: %(n)s booked transaction(s) from the bank",
                             when=fields.Datetime.context_timestamp(self, fields.Datetime.now()).strftime("%Y-%m-%d %H:%M"),
                             period=period, n=len(lines))
        self.sudo().write({"eb_last_pull": fields.Datetime.now(), "eb_last_pull_summary": (summary + note)[:250]})
        if lines:
            self.sudo().message_post(body=self.env._("Enable Banking: %(period)s — %(n)s booked transaction(s) received (%(amount)s %(cur)s net).",
                                                     period=period, n=len(lines), amount=round(sum(v["amount"] for v in lines), 2),
                                                     cur=self.journal_id.currency_id.name or self.journal_id.company_id.currency_id.name))

    def _enable_banking_line_vals(self, tr, own_iban):
        amount = float((tr.get("transaction_amount") or {}).get("amount") or 0)
        if (tr.get("credit_debit_indicator") or "CRDT") == "DBIT":
            amount = -amount
        other, other_account = (tr.get("debtor"), tr.get("debtor_account")) if amount >= 0 else (tr.get("creditor"), tr.get("creditor_account"))
        other = other or {}
        other_account = other_account or {}
        partner_name = (other.get("name") or "").strip() or False
        account_number = (other_account.get("iban") or "").replace(" ", "") or False
        if account_number and self._enable_banking_same_account(account_number, own_iban):
            account_number = False
        description = ((tr.get("bank_transaction_code") or {}).get("description") or "").strip()
        remittance = " ".join(part.strip() for part in (tr.get("remittance_information") or []) if part and part.strip())
        phone = ""
        other_id = (other_account.get("other") or {}).get("identification") or ""
        if description.lower() == "swish" and other_id.startswith("+"):
            phone = other_id
        payment_ref = remittance or partner_name or description or "/"
        if phone and phone not in payment_ref:
            # Some banks put the raw "swish +46…" text in the remittance already.
            payment_ref = f"{payment_ref} Swish {phone}"
        elif description and description.lower() not in payment_ref.lower():
            payment_ref = f"{description} {payment_ref}".strip()
        # A payer that is only a reference number is not a partner name.
        if partner_name and partner_name.replace(" ", "").isdigit():
            partner_name = False
        narration_bits = [
            f"{k}: {v}" for k, v in (
                ("Type", description), ("Booking date", tr.get("booking_date")), ("Value date", tr.get("value_date")),
                ("Reference", tr.get("entry_reference") or tr.get("transaction_id")), ("Remittance", remittance),
                ("Counterparty", (other.get("name") or "").strip()), ("Counterparty account", other_account.get("iban") or other_id),
            ) if v
        ]
        return {
            "payment_ref": payment_ref,
            "ref": description or "/",
            "amount": amount,
            "partner_name": partner_name,
            "account_number": account_number,
            "transaction_type": description,
            "narration": "\n".join(narration_bits),
            "unique_import_id": tr.get("entry_reference") or tr.get("transaction_id") or False,
        }

    @staticmethod
    def _enable_banking_line_hash(tr, date_string):
        amount = tr.get("transaction_amount") or {}
        parts = [
            date_string, tr.get("credit_debit_indicator") or "", str(amount.get("amount") or ""), amount.get("currency") or "",
            "|".join(tr.get("remittance_information") or []),
            (tr.get("debtor") or {}).get("name") or "", (tr.get("creditor") or {}).get("name") or "",
            ((tr.get("debtor_account") or {}).get("other") or {}).get("identification") or "",
            (tr.get("bank_transaction_code") or {}).get("description") or "",
        ]
        return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()[:20]

    def _enable_banking_match_swish_partners(self, lines):
        """Swish: the payer's mobile number is the only handle; look it up on
        res.partner.phone_sanitized (E.164). Same rule as the Swedbank CSV import."""
        Partner = self.env["res.partner"]
        for vals in lines:
            if vals.get("partner_id"):
                continue
            mo = SWISH_RE.search(vals.get("payment_ref") or "")
            if not mo:
                continue
            digits = re.sub(r"\D", "", mo.group(1))
            e164 = "+46" + digits[1:] if digits.startswith("0") else "+" + digits
            owners = Partner.search([("phone_sanitized", "=", e164)]).mapped("commercial_partner_id")
            if len(owners) == 1:
                vals["partner_id"] = owners.id
