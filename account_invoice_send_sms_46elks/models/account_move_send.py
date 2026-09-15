"""Sändningssättet "sms" i Odoos fakturautskick via 46elks (https://46elks.com, POST /a1/sms, Basic auth).
Texten byggs från systemparametern sms_46elks.invoice_text. Metoderna _sms_* återanvänds av
account_invoice_reminder_sms_46elks och kan överridas (t.ex. texten, numret)."""
import logging
from datetime import date

import requests
from markupsafe import Markup

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
HTTP_TIMEOUT = 60
TRUE_VALUES = ("1", "true", "True", "yes")
# tecken utanför ASCII som ändå ryms i GSM 03.38 (annars blir SMS:et UCS-2 och delas i 70-teckenbitar)
GSM_EXTRA = "åäöÅÄÖéÉüÜñÑàèìòùØøÆæß¿¡£¥€"


class AccountMoveSend(models.AbstractModel):
    _inherit = "account.move.send"

    @api.model
    def _sms_param(self, key, default=""):
        return (self.env["ir.config_parameter"].sudo().get_param("sms_46elks." + key) or default).strip()

    @api.model
    def _sms_configured(self):
        return bool(self._sms_param("username") and self._sms_param("password"))

    @api.model
    def _is_applicable_to_company(self, method, company):
        if method == "sms":
            return self._sms_configured()
        return super()._is_applicable_to_company(method, company)

    @api.model
    def _is_applicable_to_move(self, method, move, **move_data):
        if method == "sms":
            return bool(self._sms_number(move.partner_id))
        return super()._is_applicable_to_move(method, move, **move_data)

    # ------------------------------------------------------------------ hjälpare

    @api.model
    def _sms_number(self, partner):
        """Kundens nummer i E.164: partnern själv, annars första kontakten under den med telefon."""
        for cand in [partner] + list(partner.child_ids.filtered(lambda k: k.type == "contact").sorted("id")):
            if cand.phone_sanitized:
                return cand.phone_sanitized
        return None

    @api.model
    def _sms_portal_url(self, move):
        base = self._sms_param("public_base_url") or self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        return base.rstrip("/") + move.get_portal_url()

    @api.model
    def _sms_invoice_text(self, move):
        bank = move.partner_bank_id.acc_number or move.company_id.partner_id.bank_ids[:1].acc_number or "?"
        return self._sms_param("invoice_text").format(
            company=move.company_id.name, name=move.name, partner=move.partner_id.name,
            amount=f"{move.amount_residual:.0f}", due=move.invoice_date_due or date.today(), bank=bank,
            url=self._sms_portal_url(move))

    @api.model
    def _sms_post(self, to, msg):
        """Skickar ett SMS (eller dry-run). Returnerar (46elks-svar, kostnad i kr, dry-run?)."""
        bad = sorted({ch for ch in msg if ord(ch) > 127 and ch not in GSM_EXTRA})
        if bad:
            raise UserError(_("Tecken utanför GSM-7 i SMS-texten: %s", " ".join(bad)))
        dry = self._sms_param("dryrun", "0") in TRUE_VALUES
        data = {"from": self._sms_param("sender", "Odoo"), "to": to, "message": msg}
        if dry:
            data["dryrun"] = "yes"
        try:
            r = requests.post("https://api.46elks.com/a1/sms", data=data,
                              auth=(self._sms_param("username"), self._sms_param("password")), timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            raise UserError(_("46elks svarar inte: %s", e)) from e
        if r.status_code >= 400:
            raise UserError(_("46elks %(code)s: %(body)s", code=r.status_code, body=r.text[:300]))
        res = r.json()
        return res, (res.get("cost") or res.get("estimated_cost") or 0) / 10000, dry

    @api.model
    def _sms_note(self, title, to, res, cost, msg, dry):
        return Markup("<p><b>%s</b> till %s via 46elks (id %s, %s delar, %.2f kr).</p><p><i>%s</i></p>") % (
            _("SMS provkört (dry-run, inte skickat)") if dry else title, to, res.get("id", "-"), res.get("parts", "?"), cost, msg)

    # ------------------------------------------------------------------ utskick

    @api.model
    def _hook_if_success(self, moves_data, from_cron=False):
        moves = [m for m, d in moves_data.items() if "sms" in d["sending_methods"]]
        try:
            for move in moves:
                self._sms_send_invoice(move)
        except UserError:
            if not from_cron:
                raise
            _logger.exception("46elks: SMS-utskick misslyckades")
        return super()._hook_if_success(moves_data, from_cron=from_cron)

    @api.model
    def _sms_send_invoice(self, move):
        to = self._sms_number(move.partner_id)
        if not to:
            raise UserError(_("%s: inget telefonnummer på kunden", move.name))
        msg = self._sms_invoice_text(move)
        res, cost, dry = self._sms_post(to, msg)
        move.message_post(body=self._sms_note(_("Faktura skickad som SMS"), to, res, cost, msg, dry),
                          message_type="comment", subtype_xmlid="mail.mt_note")
