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
# A pull of a normal period is a page or two; more means a loop or a runaway window.
EB_MAX_PAGES = 100
EB_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,59}$")
# A failed scheduled pull is retried from the failed period only for failures that go away by
# themselves or with a new consent, and at most this long; a failure that repeats for the same
# data (a malformed answer, a page loop) must not block every later day.
EB_RETRY_CODES = ("NETWORK", "UNREADABLE", "NO_CONSENT")
EB_RETRY_MAX_DAYS = 14
SWISH_RE = re.compile(r"Swish\s+(\+?\d[\d \-]{6,})", re.I)
# Personal accounts (Swedbank via Enable Banking): no transaction type and no counterparty id; the payer
# number only appears first in the remittance, e.g. "+46701234567    1833000000000000 swish mottagen".
SWISH_PERSONAL_RE = re.compile(r"^\s*(\+?\d{9,15})\s+\d+\s+swish\b", re.I)


def _eb_error(message, status=None, code=None):
    """A failed Enable Banking call: a fixed, translated text for the user and the chatter; the
    bank's raw answer (which can hold account numbers) only goes to the server log. A plain
    UserError, because the web client shows a warning (not a crash dialog) only for that exact
    class; the HTTP status and error code ride along as attributes."""
    err = UserError(message)
    err.eb_status, err.eb_code = status, code
    return err


def _eb_short(err):
    """'429 ASPSP_RATE_LIMIT_EXCEEDED' for an Enable Banking error, '' otherwise."""
    return " ".join(str(x) for x in (getattr(err, "eb_status", None), getattr(err, "eb_code", None)) if x)


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
    # Last balances the bank reported (fetched with the period that covers today) and the result
    # of comparing them with Odoo. See _enable_banking_check_balance.
    eb_bank_booked_balance = fields.Float(string="Bank: booked balance", readonly=True, copy=False)
    eb_bank_available_balance = fields.Float(string="Bank: available balance", readonly=True, copy=False)
    eb_bank_has_available = fields.Boolean(readonly=True, copy=False)
    eb_bank_balance_at = fields.Datetime(string="Bank balance fetched", readonly=True, copy=False)
    eb_balance_check = fields.Char(string="Balance check", readonly=True, copy=False)
    eb_balance_warning_key = fields.Char(readonly=True, copy=False)
    # Where the next scheduled pull starts after a failed one: the failed period (retried) or the
    # period after it (skipped). The OCA base alone moves last_successful_run to the end of the
    # window even after a failure, skipping every period from the failed one on.
    eb_resume_from = fields.Datetime(readonly=True, copy=False)

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
        me = self if self.env.lang else self.with_context(lang=self._enable_banking_lang())  # cron: no language
        try:
            response = requests.request(method, url, params=params, json=body, headers=headers, timeout=60)
        except requests.RequestException as err:
            _logger.warning("Enable Banking %s %s: %s", method, path, err)
            raise _eb_error(
                me.env._("Enable Banking could not be reached (network error). Try again later."), code="NETWORK"
            ) from None
        if response.status_code >= 400:
            _logger.warning("Enable Banking %s %s -> %s %s", method, path, response.status_code, response.text[:500])
            code = self._enable_banking_error_code(response)
            raise _eb_error(
                self._enable_banking_error_text(me, response.status_code, code),
                status=response.status_code, code=code,
            )
        try:
            return response.json()
        except ValueError:
            _logger.warning("Enable Banking %s %s: unreadable answer %s", method, path, response.text[:200])
            raise _eb_error(
                me.env._("Enable Banking sent an answer that could not be read. Try again later."),
                status=response.status_code, code="UNREADABLE",
            ) from None

    @staticmethod
    def _enable_banking_error_code(response):
        """The error code of an Enable Banking error answer ("ASPSP_ERROR"), when it has one."""
        try:
            data = response.json()
        except ValueError:
            return None
        code = data.get("error") if isinstance(data, dict) else None
        return code if isinstance(code, str) and EB_ERROR_CODE_RE.match(code) else None

    @staticmethod
    def _enable_banking_error_text(me, status, code):
        ref = f"{status} {code}" if code else str(status)
        _ = me.env._
        if status == 429:
            return _(
                "Enable Banking: the bank's daily limit for account information is reached (%(ref)s). "
                "Try again later; the connection does not need to be renewed.",
                ref=ref,
            )
        if status in (401, 403):
            return _(
                "Enable Banking refused the request (%(ref)s). If it keeps happening, authorise with the bank "
                "again.",
                ref=ref,
            )
        if status >= 500:
            return _("Enable Banking or the bank is temporarily unavailable (%(ref)s). Try again later.", ref=ref)
        return _("Enable Banking rejected the request (%(ref)s).", ref=ref)

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

        Journals often carry the domestic number (Swedish `8000-0 123 456 7890`)
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
        journal_currency = (self.journal_id.currency_id or self.journal_id.company_id.currency_id).name
        if chosen and chosen.get("currency") and journal_currency and chosen["currency"] != journal_currency:
            self.write(dict(vals, eb_account_uid=False, eb_account_iban=False))
            self.message_post(
                body=self.env._(
                    "Enable Banking: the account %(account)s is in %(currency)s, the journal in %(journal)s. "
                    "It was not connected.",
                    account=(chosen.get("account_id") or {}).get("iban") or chosen.get("uid"),
                    currency=chosen["currency"], journal=journal_currency,
                )
            )
            return False
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
            me = provider.with_context(lang=provider._enable_banking_lang())
            if days_left >= 0:
                summary = me.env._("Enable Banking: renew the bank consent (expires in %s days)", days_left)
            else:
                summary = me.env._("Enable Banking: bank consent expired, authorise again")
            note = me.env._(
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
        me = self if self.env.lang else self.with_context(lang=self._enable_banking_lang())
        transactions, keys = [], set()
        for _page in range(EB_MAX_PAGES):
            data = self._eb_request("GET", f"/accounts/{self.eb_account_uid}/transactions", params=params)
            transactions += data.get("transactions") or []
            key = data.get("continuation_key")
            if not key:
                return transactions
            if key in keys:
                raise _eb_error(me.env._("Enable Banking sent the same page twice; the pull was stopped."))
            keys.add(key)
            params = dict(params, continuation_key=key)
        raise _eb_error(
            me.env._(
                "Enable Banking sent more than %(pages)s pages of transactions; the pull was stopped. Pull a "
                "shorter period.",
                pages=EB_MAX_PAGES,
            )
        )

    def _enable_banking_request_balances(self):
        data = self._eb_request("GET", f"/accounts/{self.eb_account_uid}/balances")
        return data.get("balances") or []

    def _enable_banking_obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        # Scheduled pulls run with an empty context (no language); texts follow the company.
        me = self.sudo().with_context(lang=self._enable_banking_lang(), tz=self._enable_banking_tz())
        # A failure, not an empty pull: one note per run, and the periods are pulled once the bank is
        # authorised again (an empty result would count as done).
        tr = self if self.env.lang else me  # a manual pull shows it to the user in their language
        if not self.eb_account_uid or not self.eb_session_id:
            raise _eb_error(tr.env._("Enable Banking: no account connected. Authorise with the bank first."), code="NO_CONSENT")
        if self.eb_session_valid_until and self.eb_session_valid_until <= fields.Datetime.now():
            raise _eb_error(tr.env._("Enable Banking: the bank consent has expired. Authorise with the bank again."), code="NO_CONSENT")
        try:
            transactions = self._enable_banking_request_transactions(date_since, date_until)
        except Exception as err:
            if not self.env.context.get("scheduled"):  # a scheduled failure is noted by _log_provider_exception
                self._enable_banking_post_failure(err, date_since, date_until)
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
                booked, available = self._enable_banking_pick_balances(self._enable_banking_request_balances())
                if booked is not None:
                    statement_values["balance_end_real"] = booked
                    self.sudo().write({
                        "eb_bank_booked_balance": booked, "eb_bank_available_balance": available or 0.0,
                        "eb_bank_has_available": available is not None, "eb_bank_balance_at": fields.Datetime.now(),
                    })
            except Exception as err:  # noqa: BLE001 - transactions are already in hand
                _logger.warning("Enable Banking: balance call failed, statement imported without closing balance: %s", err)
                note = me.env._("; balance unavailable (%s)", _eb_short(err) or me.env._("error"))
        self._enable_banking_match_swish_partners(lines)
        self._enable_banking_record_pull(date_since, date_until, transactions, lines, note)
        return lines, statement_values

    def _enable_banking_post_failure(self, err, date_since, date_until, skipped=False):
        """One chatter note and the result field for a failed pull. A UserError carries a text meant
        for the user (the fixed Enable Banking texts, a missing key); anything else is only named,
        its details are in the server log."""
        me = self.sudo().with_context(lang=self._enable_banking_lang(), tz=self._enable_banking_tz())
        reason = str(err) if isinstance(err, UserError) else me.env._("unexpected error, see the server log")
        period = {"since": date_since.date(), "until": (date_until - timedelta(days=1)).date()}
        self.sudo().write({"eb_last_pull": fields.Datetime.now(), "eb_last_pull_summary": me.env._("FAILED: %s", reason)[:250]})
        body = me.env._("Enable Banking: pull for %(since)s – %(until)s failed: %(err)s", err=reason, **period)
        if skipped:
            body += " " + me.env._(
                "This period will not be pulled again automatically; pull it manually once the cause is fixed.")
        me.message_post(body=body)

    @staticmethod
    def _enable_banking_retryable(exception):
        """Failures that go away by themselves (daily limit, 5xx, network, an unreadable answer) or with a
        new consent (401/403, expired or missing consent)."""
        status = getattr(exception, "eb_status", None)
        return (
            getattr(exception, "eb_code", None) in EB_RETRY_CODES
            or status in (401, 403, 429)
            or (isinstance(status, int) and status >= 500)
        )

    def _log_provider_exception(self, exception, statement_date_since, statement_date_until):
        """The OCA base posts str(exception) - which could be a bank's raw text - and moves on to the
        next period for good. For Enable Banking: log with the traceback, note the failure once with a
        fixed text, and pull the failed period again at the next scheduled run when the failure is
        retryable and at most EB_RETRY_MAX_DAYS old; otherwise skip only that period and say so."""
        if self.service != "enable_banking":
            return super()._log_provider_exception(exception, statement_date_since, statement_date_until)
        self.ensure_one()
        _logger.warning("Enable Banking: provider %s failed to obtain statement data since %s until %s",
                        self.id, statement_date_since, statement_date_until, exc_info=True)
        # age by the period's end: a month-long period is recent when its last days are
        retry = self._enable_banking_retryable(exception) and (
            statement_date_until >= fields.Datetime.now() - timedelta(days=EB_RETRY_MAX_DAYS)
        )
        # retried: start again at the failed period; skipped: continue right after it, so the later
        # periods of the window (never tried, the OCA loop stops at the first failure) are not lost
        self.sudo().write({"eb_resume_from": statement_date_since if retry else statement_date_until})
        self._enable_banking_post_failure(exception, statement_date_since, statement_date_until, skipped=not retry)

    def _schedule_next_run(self):
        """After a failed scheduled pull, start the next one at eb_resume_from (the failed period, or
        the one after a skipped period); the OCA base would jump to the end of the window."""
        self.ensure_one()
        resume_from = self.eb_resume_from if self.service == "enable_banking" else False
        res = super()._schedule_next_run()
        if resume_from:
            self.last_successful_run = resume_from
        return res

    def _statement_create_or_write(self, statement_values):
        """The bank's booked balance can lag its own booked transactions within the day.

        Swedbank delivers a transaction as BOOK with today's booking date while "Current
        booked balance" (ITBD) still excludes it until the nightly run (an outgoing transfer
        delivered while the booked balance was still without it; a weekend Swish payment
        missing from the booked balance but present in the available one). Odoo would show the
        statement as incomplete for good, because tomorrow's pull is a new statement.
        For a statement that is still open (dated today) the bank's figure is therefore only
        kept when it agrees with start + lines; otherwise Odoo's computed end is used.
        Whether the difference is such a lag or a real discrepancy is decided after the pull
        by _enable_banking_check_balance, which also runs on days without new lines.
        Past statements keep the bank's figure — there a mismatch is real and must stay visible."""
        statement = super()._statement_create_or_write(statement_values)
        if self.service != "enable_banking" or not statement or "balance_end_real" not in statement_values:
            return statement
        stmt = statement.sudo()
        today = fields.Date.context_today(self)
        if stmt.date and stmt.date >= today and stmt.currency_id.compare_amounts(stmt.balance_end, stmt.balance_end_real) != 0:
            stmt.write({"balance_end_real": stmt.balance_end})
        return statement

    @staticmethod
    def _enable_banking_pick_balances(balances):
        """(booked, available) from the bank's balance list; None when the bank did not send one."""
        booked = available = None
        for bal in balances:
            kind = bal.get("balance_type") or ""
            name = (bal.get("name") or "").lower()
            amount = float((bal.get("balance_amount") or {}).get("amount") or 0)
            if booked is None and (kind in ("CLBD", "ITBD") or "booked" in name):
                booked = amount
            elif available is None and (kind in ("CLAV", "ITAV") or "available" in name):
                available = amount
        return booked, available

    @staticmethod
    def _enable_banking_explained_by_lag(bank_booked, odoo_balance, today_amounts, limit=100000):
        """True when the bank's booked balance equals Odoo's balance without some of today's lines.

        The booked balance lags booked transactions of the same day (see _statement_create_or_write),
        so bank == Odoo - (today's lines not yet in the bank's figure) is expected; any other
        difference is real. Works in öre to avoid float noise; gives up (False) on huge line sets."""
        cents = [round(a * 100) for a in today_amounts]
        missing = round((odoo_balance - bank_booked) * 100)
        sums = {0}
        for c in cents:
            sums |= {x + c for x in sums}
            if len(sums) > limit:
                return False
        return missing in sums

    def _pull(self, date_since, date_until):
        # a failure of this pull sets it again (_log_provider_exception)
        self.filtered(lambda p: p.service == "enable_banking" and p.eb_resume_from).sudo().write({"eb_resume_from": False})
        started = fields.Datetime.now()
        res = super()._pull(date_since, date_until)
        if not self.env.context.get("account_statement_online_import_debug"):
            for provider in self.filtered(lambda p: p.service == "enable_banking"):
                if provider.eb_bank_balance_at and provider.eb_bank_balance_at >= started:
                    try:
                        provider._enable_banking_check_balance()
                    except Exception:  # noqa: BLE001 - a failed check must not undo the import
                        _logger.exception("Enable Banking: balance check failed for provider %s", provider.id)
        return res

    def _enable_banking_lang(self):
        return self.journal_id.company_id.partner_id.lang or self.env.lang

    def _enable_banking_tz(self):
        # Scheduled pulls run as the system user without a timezone; show the renewal user's local time.
        return self.eb_renewal_user_id.tz or self.env.user.tz or "UTC"

    def _enable_banking_check_balance(self):
        """Compare the bank's balance with Odoo after every pull, also on days without new lines.

        Consistent when Odoo equals the booked or the available balance, or when the booked
        balance only lacks some of today's lines (lag) and the available balance, if the bank sends
        one, is not below Odoo — a duplicate import makes Odoo exceed what the bank has, pending
        incoming payments only make the available balance larger. Anything else is a real
        discrepancy: one chatter warning per difference (the key is the difference, so both balances
        moving together does not repeat it), and the result is kept in eb_balance_check."""
        self.ensure_one()
        if not self.create_statement:
            return  # lines without statements: the last statement does not show the journal balance
        me = self.sudo().with_context(lang=self._enable_banking_lang(), tz=self._enable_banking_tz())
        currency = self.journal_id.currency_id or self.journal_id.company_id.currency_id
        last = self.env["account.bank.statement"].sudo().search(
            [("journal_id", "=", self.journal_id.id), ("date", "!=", False)], order="date desc, id desc", limit=1)
        if not last:
            return
        today = fields.Date.context_today(self)
        odoo_balance = last.balance_end
        today_amounts = last.line_ids.filtered(lambda line: line.date == today).mapped("amount")
        booked = self.eb_bank_booked_balance
        available = self.eb_bank_available_balance if self.eb_bank_has_available else None
        when = fields.Datetime.context_timestamp(me, self.eb_bank_balance_at).strftime("%Y-%m-%d %H:%M")
        amounts = {"bank": booked, "odoo": odoo_balance, "diff": booked - odoo_balance,
                   "available": available if available is not None else 0.0, "when": when}
        key = self.eb_balance_warning_key
        if not currency.compare_amounts(odoo_balance, booked):
            result, real, key = me.env._("%(when)s: balance agrees with the bank (%(odoo).2f)", **amounts), False, False
        elif available is not None and not currency.compare_amounts(odoo_balance, available):
            result, real, key = me.env._(
                "%(when)s: balance agrees with the bank's available balance (%(available).2f); the booked "
                "balance (%(bank).2f) has not caught up yet", **amounts), False, False
        elif self._enable_banking_explained_by_lag(booked, odoo_balance, today_amounts) and (
                available is None or currency.compare_amounts(available, odoo_balance) >= 0):
            result, real = me.env._(
                "%(when)s: the bank's booked balance (%(bank).2f) lacks some of today's lines; Odoo %(odoo).2f",
                **amounts), False
        else:
            result, real = me.env._(
                "%(when)s: DIFFERENCE – bank %(bank).2f, Odoo %(odoo).2f (%(diff).2f)", **amounts), True
        if real:
            new_key = f"{self.journal_id.id}|{booked - odoo_balance:.2f}"
            if new_key != self.eb_balance_warning_key:
                me.message_post(body=me.env._(
                    "Enable Banking: the bank's booked balance (%(bank).2f) differs by %(diff).2f from Odoo's balance "
                    "(%(odoo).2f) on %(name)s, and the difference is not explained by today's transactions. Check that "
                    "every transaction has been imported and that the opening balance is right.",
                    name=last.name, **amounts))
            key = new_key
        self.sudo().write({"eb_balance_check": result[:250], "eb_balance_warning_key": key})

    def _enable_banking_record_pull(self, date_since, date_until, transactions, lines, note=""):
        """Leave a trace of every pull on the provider; chatter only when something new came in.

        `lines` is what the bank returned as booked for the period. The bank filters on its own
        date, so the same lines can come back for neighbouring periods (weekend Swish booked on
        Monday); only lines dated inside the period and not yet imported are new — exactly what
        the OCA base will import."""
        me = self.sudo().with_context(lang=self._enable_banking_lang(), tz=self._enable_banking_tz())
        first, last = date_since.date(), (date_until - timedelta(days=1)).date()
        period = f"{first} – {last}" if first != last else f"{first}"
        new = []
        Line = self.env["account.bank.statement.line"].sudo()
        for vals in lines:
            if not (first <= vals["date"] <= last):
                continue
            probe = {"unique_import_id": vals.get("unique_import_id")}
            self.journal_id._statement_line_import_update_unique_import_id(probe, self.account_number)
            if probe.get("unique_import_id") and Line.search_count(
                    [("unique_import_id", "=", probe["unique_import_id"])], limit=1):
                continue
            new.append(vals)
        summary = me.env._("%(when)s — %(period)s: %(n)s booked transaction(s) from the bank, %(new)s new",
                           when=fields.Datetime.context_timestamp(me, fields.Datetime.now()).strftime("%Y-%m-%d %H:%M"),
                           period=period, n=len(lines), new=len(new))
        self.sudo().write({"eb_last_pull": fields.Datetime.now(), "eb_last_pull_summary": (summary + note)[:250]})
        if new:
            me.message_post(body=me.env._(
                "Enable Banking: %(period)s — %(n)s new booked transaction(s) (%(amount).2f %(cur)s net).",
                period=period, n=len(new), amount=round(sum(v["amount"] for v in new), 2),
                cur=self.journal_id.currency_id.name or self.journal_id.company_id.currency_id.name))

    def _enable_banking_line_vals(self, tr, own_iban):
        amount = float((tr.get("transaction_amount") or {}).get("amount") or 0)
        # The indicator gives the sign; some banks also sign the amount, which must not flip it twice.
        indicator = tr.get("credit_debit_indicator")
        if indicator == "DBIT":
            amount = -abs(amount)
        elif indicator == "CRDT":
            amount = abs(amount)
        other, other_account = (tr.get("debtor"), tr.get("debtor_account")) if amount >= 0 else (tr.get("creditor"), tr.get("creditor_account"))
        other = other or {}
        other_account = other_account or {}
        partner_name = (other.get("name") or "").strip() or False
        account_number = (other_account.get("iban") or "").replace(" ", "") or False
        if account_number and self._enable_banking_same_account(account_number, own_iban):
            account_number = False
        description = self._enable_banking_transaction_type(tr)
        remittance = " ".join(self._enable_banking_remittance(tr))
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
    def _enable_banking_transaction_type(tr):
        """bank_transaction_code is an object ({description, code, sub_code}); some banks send a string."""
        code = tr.get("bank_transaction_code")
        if isinstance(code, dict):
            return (code.get("description") or "").strip()
        return code.strip() if isinstance(code, str) else ""

    @staticmethod
    def _enable_banking_remittance(tr):
        """remittance_information is a list of strings; a bank that sends one string must not be read letter by letter."""
        value = tr.get("remittance_information") or []
        if isinstance(value, str):
            value = [value]
        return [part.strip() for part in value if isinstance(part, str) and part.strip()]

    @staticmethod
    def _enable_banking_line_hash(tr, date_string):
        # Unchanged since the first import: the id of an imported line must stay the same.
        amount = tr.get("transaction_amount") or {}
        code = tr.get("bank_transaction_code")
        parts = [
            date_string, tr.get("credit_debit_indicator") or "", str(amount.get("amount") or ""), amount.get("currency") or "",
            "|".join(tr.get("remittance_information") or []),
            (tr.get("debtor") or {}).get("name") or "", (tr.get("creditor") or {}).get("name") or "",
            ((tr.get("debtor_account") or {}).get("other") or {}).get("identification") or "",
            ((code.get("description") or "") if isinstance(code, dict) else (code if isinstance(code, str) else "")),
        ]
        return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()[:20]

    def _enable_banking_match_swish_partners(self, lines):
        """Swish: the payer's mobile number is the only handle; look it up on
        res.partner.phone_sanitized (E.164). Same rule as the Swedbank CSV import.
        Business accounts carry "Swish +46…"; personal accounts start the remittance with
        the number. Two partners with the same number: no match (never guess a payer)."""
        Partner = self.env["res.partner"]
        for vals in lines:
            if vals.get("partner_id"):
                continue
            text = vals.get("payment_ref") or ""
            mo = SWISH_RE.search(text) or SWISH_PERSONAL_RE.search(text)
            if not mo:
                continue
            digits = re.sub(r"\D", "", mo.group(1))
            e164 = "+46" + digits[1:] if digits.startswith("0") else "+" + digits
            owners = Partner.search([("phone_sanitized", "=", e164)]).mapped("commercial_partner_id")
            if len(owners) == 1:
                vals["partner_id"] = owners.id
