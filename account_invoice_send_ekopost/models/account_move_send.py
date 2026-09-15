"""Sändningssättet "ekopost" i Odoos fakturautskick (account.move.send).

Ekopost (https://api.ekopost.se): kampanj → kuvert (postal-adress) → innehåll (base64-PDF) → stäng kuvert →
stäng kampanj. Basic auth (användare/lösenord) eller api-key. Metoderna _ekopost_* är avsedda att
återanvändas av andra moduler (t.ex. påminnelser) och att överridas (adressblocket)."""
import base64
import logging
from datetime import date

import requests
from markupsafe import Markup

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
HTTP_TIMEOUT = 60
TRUE_VALUES = ("1", "true", "True", "yes")


class AccountMoveSend(models.AbstractModel):
    _inherit = "account.move.send"

    # ------------------------------------------------------------------ inställningar

    @api.model
    def _ekopost_param(self, key, default=""):
        return (self.env["ir.config_parameter"].sudo().get_param("ekopost." + key) or default).strip()

    @api.model
    def _ekopost_headers(self):
        api_key, user, password = self._ekopost_param("api_key"), self._ekopost_param("user"), self._ekopost_param("password")
        if api_key:
            auth = "api-key " + api_key
        elif user and password:
            auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        else:
            return None
        return {"Authorization": auth, "Content-Type": "application/json; charset=UTF-8", "Accept": "application/json",
                "User-Agent": "odoo-account_invoice_send_ekopost/1.0"}

    @api.model
    def _ekopost_configured(self):
        return bool(self._ekopost_headers())

    # ------------------------------------------------------------------ tillgänglighet

    @api.model
    def _is_applicable_to_company(self, method, company):
        if method == "ekopost":
            return self._ekopost_configured()
        return super()._is_applicable_to_company(method, company)

    @api.model
    def _is_applicable_to_move(self, method, move, **move_data):
        if method == "ekopost":
            return self._ekopost_address_ok(move.partner_id)
        return super()._is_applicable_to_move(method, move, **move_data)

    # ------------------------------------------------------------------ API

    @api.model
    def _ekopost(self, method, path, body=None):
        url = self._ekopost_param("url", "https://api.ekopost.se").rstrip("/") + path
        try:
            r = requests.request(method, url, json=body, headers=self._ekopost_headers(), timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            raise UserError(_("Ekopost svarar inte: %s", e)) from e
        if r.status_code >= 400:
            raise UserError(_("Ekopost %(method)s %(path)s → %(code)s: %(body)s",
                              method=method, path=path, code=r.status_code, body=r.text[:400]))
        return r.json() if r.content else {}

    @api.model
    def _ekopost_address_ok(self, partner):
        return bool(partner.zip and partner.city and (partner.street or partner.name))

    @api.model
    def _ekopost_postal_address(self, partner):
        """Ekoposts postal-adress från kundens adressfält. Överrid för egna regler (t.ex. flera
        namnrader). use_coverpage=False: adressen står redan i PDF:ens fönster."""
        names = [partner.name]
        if partner.parent_id and partner.parent_id.name != partner.name:
            names.append(partner.parent_id.name)
        street = [s for s in (partner.street, partner.street2) if s]
        return {"$type": "postal", "name": names, "street": street, "postal_code": (partner.zip or "").replace(" ", ""),
                "city": partner.city or "", "country": partner.country_id.code or "SE", "use_coverpage": False}

    @api.model
    def _ekopost_post_pdfs(self, items, campaign_name=None):
        """Postar en lista av (partner, namn, pdf_bytes) i EN kampanj. Returnerar (kampanj_id, [(kuvert_id, adress)…],
        stängd?). Misslyckas något avbryts kampanjen (inget skrivs ut)."""
        color = self._ekopost_param("color", "1") in TRUE_VALUES
        postage = self._ekopost_param("postage", "economy")
        close = self._ekopost_param("close", "1") in TRUE_VALUES
        camp = self._ekopost("POST", "/campaigns", {"name": campaign_name or f"Odoo {date.today().isoformat()}"})
        cid = camp["id"]
        envelopes = []
        try:
            for partner, name, pdf in items:
                addr = self._ekopost_postal_address(partner)
                env = self._ekopost("POST", f"/campaigns/{cid}/envelopes", {"name": name, "address": addr, "postage": postage,
                                                                              "plex": "simplex", "color": color})
                self._ekopost("POST", f"/campaigns/{cid}/envelopes/{env['id']}/content", {
                    "data": base64.b64encode(pdf).decode(), "mime": "application/pdf", "length": len(pdf), "type": "document"})
                self._ekopost("POST", f"/campaigns/{cid}/envelopes/{env['id']}/close")
                envelopes.append((env["id"], addr))
        except UserError:
            self._ekopost("POST", f"/campaigns/{cid}/cancel")
            raise
        if close:
            self._ekopost("POST", f"/campaigns/{cid}/close")
        return cid, envelopes, close

    @api.model
    def _ekopost_note(self, closed, eid, cid, addr, extra=""):
        return Markup("<p><b>%s</b>: kuvert %s, kampanj %s (%s) → %s, %s, %s %s%s</p>") % (
            _("Postad via Ekopost") if closed else _("Förberedd i Ekopost (kampanjen är inte stängd)"),
            eid, cid, date.today().isoformat(), " / ".join(addr["name"]), " / ".join(addr["street"]),
            addr["postal_code"], addr["city"], extra)

    # ------------------------------------------------------------------ utskick

    @api.model
    def _hook_if_success(self, moves_data, from_cron=False):
        # Brevet före e-posten: går det fel ska felet stoppa dialogen innan mail gått ut
        # (i batch/cron loggas felet i stället).
        moves = [m for m, d in moves_data.items() if "ekopost" in d["sending_methods"]]
        if moves:
            try:
                self._ekopost_send_invoices(moves)
            except UserError:
                if not from_cron:
                    raise
                _logger.exception("Ekopost: utskick misslyckades")
        return super()._hook_if_success(moves_data, from_cron=from_cron)

    @api.model
    def _ekopost_send_invoices(self, moves):
        items = []
        for move in moves:
            pdf = self._get_invoice_extra_attachments(move)[:1]
            if not pdf:
                raise UserError(_("%s: ingen PDF att posta", move.name))
            items.append((move.partner_id, move.name, pdf.raw))
        cid, envelopes, closed = self._ekopost_post_pdfs(items, f"Odoo fakturor {date.today().isoformat()}")
        for move, (eid, addr) in zip(moves, envelopes, strict=True):
            move.message_post(body=self._ekopost_note(closed, eid, cid, addr), message_type="comment", subtype_xmlid="mail.mt_note")
