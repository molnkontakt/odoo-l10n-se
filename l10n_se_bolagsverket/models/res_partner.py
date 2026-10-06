import logging
import time
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..lib import bolagsverket as bv

_logger = logging.getLogger(__name__)

STATUS_SELECTION = [
    ("active", "Active"),
    ("inactive", "Not trading"),
    ("procedure", "Bankruptcy, liquidation or reorganisation"),
    ("deregistered", "Deregistered"),
    ("not_found", "Not found"),
    ("unknown", "Unknown"),
]
RECHECK_DAYS = 7
RETRY_HOURS = 24
INCOMPLETE_HOURS = 3
INCOMPLETE_STOP = 3   # this many incomplete answers in a row: the data source is down, stop the run
NEW_BATCH = 25
DUE_BATCH = 50
RUN_BUDGET_SECONDS = 120
PREFIX = "l10n_se_bolagsverket."
BV_FIELDS_RESET = {
    "l10n_se_bv_status": False, "l10n_se_bv_status_note": False, "l10n_se_bv_checked": False,
    "l10n_se_bv_next_check": False, "l10n_se_bv_name": False, "l10n_se_bv_name_mismatch": False,
    "l10n_se_bv_org_form": False, "l10n_se_bv_registered": False, "l10n_se_bv_sni": False,
    "l10n_se_bv_description": False, "l10n_se_bv_advertising_block": False, "l10n_se_bv_error": False,
    "l10n_se_bv_name_protection_no": False,
}


class ResPartner(models.Model):
    _inherit = "res.partner"

    l10n_se_bv_identity = fields.Char(
        string="Identity number (Bolagsverket)", compute="_compute_l10n_se_bv_identity", store=True, index=True,
        help="10-digit Swedish organisation number used for Bolagsverket, taken from Company ID or else from a "
             "Swedish VAT number.")
    l10n_se_bv_identity_kind = fields.Selection([("org", "Organisation number"), ("person", "Personal identity number")],
                                                compute="_compute_l10n_se_bv_identity", store=True)
    l10n_se_bv_status = fields.Selection(STATUS_SELECTION, string="Bolagsverket status", readonly=True, copy=False)
    l10n_se_bv_status_note = fields.Char(string="Bolagsverket status details", readonly=True, copy=False)
    l10n_se_bv_checked = fields.Datetime(string="Checked at Bolagsverket", readonly=True, copy=False)
    l10n_se_bv_next_check = fields.Datetime(readonly=True, copy=False, index=True)
    l10n_se_bv_name = fields.Char(string="Registered name", readonly=True, copy=False)
    l10n_se_bv_name_protection_no = fields.Integer(readonly=True, copy=False,
                                                   help="Which of a sole trader's businesses this contact is.")
    l10n_se_bv_name_mismatch = fields.Boolean(
        string="Name differs from Bolagsverket", readonly=True, copy=False,
        help="The name in Odoo is not one of the names registered for this organisation number. On a supplier this "
             "can mean the wrong number was entered – or an invoice from someone pretending to be the company.")
    l10n_se_bv_org_form = fields.Char(string="Company form", readonly=True, copy=False)
    l10n_se_bv_registered = fields.Date(string="Registered", readonly=True, copy=False)
    l10n_se_bv_sni = fields.Char(string="Industry codes (SNI)", readonly=True, copy=False)
    l10n_se_bv_description = fields.Text(string="Registered business", readonly=True, copy=False)
    l10n_se_bv_advertising_block = fields.Boolean(string="Advertising block", readonly=True, copy=False,
                                                  help="The organisation has asked not to receive direct marketing.")
    l10n_se_bv_error = fields.Char(string="Bolagsverket error", readonly=True, copy=False)
    l10n_se_bv_bad = fields.Boolean(compute="_compute_l10n_se_bv_bad")

    @api.depends("company_registry", "vat", "country_id")
    def _compute_l10n_se_bv_identity(self):
        for partner in self:
            swedish = not partner.country_id or partner.country_id.code == "SE"
            vat = partner.vat if (partner.vat or "").upper().startswith("SE") else None
            identity = bv.normalize_identity(partner.company_registry if swedish else None, vat)
            partner.l10n_se_bv_identity = identity
            partner.l10n_se_bv_identity_kind = bv.identity_kind(identity) if identity else False

    @api.depends("l10n_se_bv_status")
    def _compute_l10n_se_bv_bad(self):
        for partner in self:
            partner.l10n_se_bv_bad = partner.l10n_se_bv_status in bv.BAD_STATUSES

    # ------------------------------------------------------------------
    # New or changed numbers are checked soon
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        if partners.filtered("l10n_se_bv_identity") and self._l10n_se_bv_enabled():
            self._l10n_se_bv_trigger()
        return partners

    def write(self, vals):
        watch = {"company_registry", "vat", "country_id"} & set(vals)
        before = {p.id: p.l10n_se_bv_identity for p in self} if watch else None
        res = super().write(vals)
        if before is not None:
            changed = self.filtered(lambda p: p.l10n_se_bv_identity != before.get(p.id))
            if changed:
                # everything learnt about the old number is void
                super(ResPartner, changed.sudo()).write(dict(BV_FIELDS_RESET))
                if changed.filtered("l10n_se_bv_identity") and self._l10n_se_bv_enabled():
                    self._l10n_se_bv_trigger()
        return res

    @api.model
    def _l10n_se_bv_trigger(self):
        cron = self.env.ref("l10n_se_bolagsverket.ir_cron_bolagsverket_check", raise_if_not_found=False)
        cron = cron and cron.sudo()   # only system administrators may read ir.cron
        if cron and cron.active:
            cron._trigger(fields.Datetime.now() + timedelta(minutes=2))

    # ------------------------------------------------------------------
    # Client
    # ------------------------------------------------------------------

    @api.model
    def _l10n_se_bv_enabled(self):
        ICP = self.env["ir.config_parameter"].sudo()
        return bool(ICP.get_param(PREFIX + "enabled") and ICP.get_param(PREFIX + "client_id")
                    and ICP.get_param(PREFIX + "client_secret"))

    @api.model
    def _l10n_se_bv_client(self):
        ICP = self.env["ir.config_parameter"].sudo()
        cid = ICP.get_param(PREFIX + "client_id")
        secret = ICP.get_param(PREFIX + "client_secret")
        if not (cid and secret):
            raise UserError(self.env._("Bolagsverket is not configured: enter the client id and secret in the "
                                       "Accounting settings."))
        return bv.Client(cid, secret, ICP.get_param(PREFIX + "environment") or "prod")

    # ------------------------------------------------------------------
    # Language and recipients of notifications
    # ------------------------------------------------------------------

    def _l10n_se_bv_companies(self):
        """Companies whose follow-up user hears about this partner: its own company, or for a shared partner every
        company that has chosen a follow-up user."""
        self.ensure_one()
        if self.company_id:
            return self.company_id
        return self.env["res.company"].sudo().search([("l10n_se_bv_responsible_id", "!=", False)])

    def _l10n_se_bv_lang(self, company):
        user = company.l10n_se_bv_responsible_id
        return user.lang or company.partner_id.lang or self.env.lang or "en_US"

    def _l10n_se_bv_number_text(self):
        """The number for messages – never a sole trader's personal identity number."""
        self.ensure_one()
        if self.l10n_se_bv_identity_kind == bv.KIND_ORG:
            return bv.format_identity(self.l10n_se_bv_identity)
        return self.env._("the identity number")

    # ------------------------------------------------------------------
    # Applying a lookup
    # ------------------------------------------------------------------

    def _l10n_se_bv_lookup(self, client):
        """(picked organisation or None, all organisations) for this partner. Raises BolagsverketError."""
        self.ensure_one()
        parsed = client.organisations(self.l10n_se_bv_identity)
        picked = bv.pick_organisation(parsed, self.name, self.l10n_se_bv_name_protection_no or None)
        return picked, parsed

    def _l10n_se_bv_apply_status(self, picked, parsed):
        """Store what Bolagsverket says; notify when the organisation turns bad or the name stops matching. Never
        changes the partner's own contact data."""
        self.ensure_one()
        status, note = bv.status_of(picked)
        mismatch = bool(picked and picked["name"] and not bv.names_match(self.name, bv.all_names(parsed)))
        now = fields.Datetime.now()
        vals = {
            "l10n_se_bv_status": status,
            "l10n_se_bv_status_note": note,
            "l10n_se_bv_checked": now,
            "l10n_se_bv_next_check": now + timedelta(days=RECHECK_DAYS),
            "l10n_se_bv_name": picked and picked["name"],
            "l10n_se_bv_name_mismatch": mismatch,
            "l10n_se_bv_error": False,
        }
        if picked:
            vals.update({
                "l10n_se_bv_org_form": picked["org_form_text"] or picked["legal_form_text"],
                "l10n_se_bv_registered": picked["registration_date"],
                "l10n_se_bv_sni": "; ".join(f"{c} {t}" for c, t in picked["sni"]) or False,
                "l10n_se_bv_description": picked["description"],
                "l10n_se_bv_advertising_block": picked["advertising_block"],
            })
        was_bad, old_note, was_mismatch = (self.l10n_se_bv_status in bv.BAD_STATUSES, self.l10n_se_bv_status_note,
                                           self.l10n_se_bv_name_mismatch)
        self.sudo().write(vals)
        if status in bv.BAD_STATUSES and (not was_bad or note != old_note):
            self._l10n_se_bv_notify("status")
        if mismatch and not was_mismatch:
            self._l10n_se_bv_notify("mismatch")

    def _l10n_se_bv_message(self, kind, env):
        if kind == "status":
            label = dict(self._fields["l10n_se_bv_status"]._description_selection(env))[self.l10n_se_bv_status]
            return env._("Bolagsverket: %(name)s – %(status)s%(note)s.", name=self.name, status=label,
                         note=f" ({self.l10n_se_bv_status_note})" if self.l10n_se_bv_status_note else "")
        return env._("Bolagsverket: %(number)s is registered to %(registered)s, not %(name)s. Check the number – on "
                     "a supplier invoice this can be a sign of fraud.",
                     number=self.with_env(env)._l10n_se_bv_number_text(), registered=self.l10n_se_bv_name,
                     name=self.name)

    def _l10n_se_bv_notify(self, kind):
        """A note on the partner (followers see it) and one open to-do per follow-up user, in that user's
        language."""
        self.ensure_one()
        partner = self.sudo()
        companies = self._l10n_se_bv_companies()
        lang = self._l10n_se_bv_lang(companies[:1]) if companies else (self.env.lang or "en_US")
        note_env = partner.with_context(lang=lang).env
        partner.message_post(body=partner._l10n_se_bv_message(kind, note_env), message_type="comment",
                             subtype_xmlid="mail.mt_note")
        todo = self.env.ref("mail.mail_activity_data_todo")
        for user in companies.l10n_se_bv_responsible_id:
            user_env = partner.with_context(lang=user.lang or lang).env
            summary = (user_env._("Bolagsverket: check status") if kind == "status"
                       else user_env._("Bolagsverket: check the organisation number"))
            open_todo = self.env["mail.activity"].sudo().search_count([
                ("res_model", "=", "res.partner"), ("res_id", "=", self.id), ("user_id", "=", user.id),
                ("activity_type_id", "=", todo.id), ("summary", "=", summary)])
            if not open_todo:
                partner.activity_schedule("mail.mail_activity_data_todo", user_id=user.id, summary=summary,
                                          note=partner._l10n_se_bv_message(kind, user_env))

    def _l10n_se_bv_contact_vals(self, picked):
        """Contact data from Bolagsverket: registered name and postal address."""
        vals = {"is_company": True, "l10n_se_bv_name_protection_no": picked["name_protection_no"] or 0}
        if picked["name"]:
            vals["name"] = picked["name"]
        address = picked["address"]
        if address.get("street") or address.get("zip"):
            vals.update({"street": address.get("street") or False, "street2": address.get("co") or False,
                         "zip": address.get("zip") or False, "city": address.get("city") or False})
            land = (address.get("country") or "").strip()
            if not land or land.upper() in ("SVERIGE", "SWEDEN", "SE"):
                vals["country_id"] = self.env.ref("base.se").id
            else:
                Country = self.env["res.country"]
                country = Country.with_context(lang="sv_SE").search([("name", "=ilike", land)], limit=1) \
                    or Country.search([("name", "=ilike", land)], limit=1)
                if country:
                    vals["country_id"] = country.id
        if not self.company_registry:
            vals["company_registry"] = self.l10n_se_bv_identity   # same 10-digit form l10n_se derives from the VAT
        return vals

    def _l10n_se_bv_check_one(self, client, update_contact=False):
        """Look the partner up and apply the result. Returns the picked organisation (None when not found).
        Raises BolagsverketError; nothing is written for an error."""
        self.ensure_one()
        picked, parsed = self._l10n_se_bv_lookup(client)
        if update_contact and picked and not picked["not_found"]:
            vals = self._l10n_se_bv_contact_vals(picked)
            changes = []
            for field, value in vals.items():
                if field in ("is_company", "l10n_se_bv_name_protection_no") or not value:
                    continue
                if field == "company_registry" and self.l10n_se_bv_identity_kind != bv.KIND_ORG:
                    continue   # never a personal identity number in chatter
                old = self[field].id if field == "country_id" else self[field]
                if old != value:
                    label = self._fields[field].get_description(self.env)["string"]
                    shown = self.env["res.country"].browse(value).name if field == "country_id" else value
                    changes.append(f"{label}: {shown}")
            self.write(vals)
            if changes:
                self.message_post(body=self.env._("Updated from Bolagsverket: %(changes)s", changes="; ".join(changes)),
                                  message_type="comment", subtype_xmlid="mail.mt_note")
        self._l10n_se_bv_apply_status(picked, parsed)
        return picked

    def _l10n_se_bv_run_buttons(self, update_contact):
        self.check_access("write")
        client = self._l10n_se_bv_client()
        not_found = self.env["res.partner"]
        for partner in self:
            if not partner.l10n_se_bv_identity:
                raise UserError(self.env._("%(name)s has no valid Swedish organisation number (Company ID).",
                                           name=partner.display_name))
            try:
                picked = partner._l10n_se_bv_check_one(client, update_contact=update_contact)
            except bv.BolagsverketError as exc:
                raise UserError(self.env._("Bolagsverket could not answer right now: %(error)s", error=exc)) from None
            if not picked or picked["not_found"]:
                not_found |= partner
        if not_found:
            return {"type": "ir.actions.client", "tag": "display_notification", "params": {
                "type": "warning", "sticky": True,
                "message": self.env._("Bolagsverket has no organisation registered for %(names)s.",
                                      names=", ".join(not_found.mapped("display_name"))),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"}}}
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def action_l10n_se_bv_fetch(self):
        """Button: fill name and address from Bolagsverket and refresh the status."""
        return self._l10n_se_bv_run_buttons(update_contact=True)

    def action_l10n_se_bv_check(self):
        """Button: refresh the status only."""
        return self._l10n_se_bv_run_buttons(update_contact=False)

    # ------------------------------------------------------------------
    # Scheduled check
    # ------------------------------------------------------------------

    @api.model
    def _l10n_se_bv_watch_domain(self):
        """Companies (incl. sole traders marked as companies) that are customers or suppliers – never private
        persons."""
        return [("l10n_se_bv_identity", "!=", False), ("parent_id", "=", False),
                "|", ("is_company", "=", True), ("l10n_se_bv_identity_kind", "=", bv.KIND_ORG),
                "|", ("customer_rank", ">", 0), ("supplier_rank", ">", 0)]

    @api.model
    def _cron_l10n_se_bv_check(self):
        """New numbers first, then those due (weekly; a day after an error). Stops on errors that aren't about one
        number. Keeps the API account alive (Bolagsverket closes accounts unused for six months) with a daily ping
        when idle. Never raises: Odoo deactivates crons that fail repeatedly."""
        if not self._l10n_se_bv_enabled():
            return
        try:
            self._l10n_se_bv_check_due(self._l10n_se_bv_client())
        except Exception as exc:  # noqa: BLE001 – keep the job alive, report the problem
            _logger.exception("Bolagsverket: check failed")
            self._l10n_se_bv_set_error(str(exc)[:200])

    @api.model
    def _l10n_se_bv_check_due(self, client):
        started = time.monotonic()
        now = fields.Datetime.now()
        Partner = self.sudo()
        domain = self._l10n_se_bv_watch_domain()
        partners = Partner.search(domain + [("l10n_se_bv_next_check", "=", False)], limit=NEW_BATCH, order="id")
        partners |= Partner.search(domain + [("l10n_se_bv_next_check", "<=", now)], limit=DUE_BATCH,
                                   order="l10n_se_bv_next_check")
        done = incomplete_in_a_row = 0
        for partner in partners:
            if time.monotonic() - started > RUN_BUDGET_SECONDS:
                break
            try:
                with self.env.cr.savepoint():
                    partner._l10n_se_bv_check_one(client)
            except bv.BolagsverketError as exc:
                if exc.incomplete:
                    # a data source was down for this number: keep everything, try it again in a few hours
                    partner.write({"l10n_se_bv_error": str(exc)[:200],
                                   "l10n_se_bv_next_check": now + timedelta(hours=INCOMPLETE_HOURS)})
                    incomplete_in_a_row += 1
                    if incomplete_in_a_row >= INCOMPLETE_STOP:
                        self._l10n_se_bv_commit()
                        self._l10n_se_bv_set_error(str(exc)[:200])
                        return
                    self._l10n_se_bv_commit()
                    done += 1
                    continue
                if exc.retry or exc.status in (401, 403):
                    _logger.warning("Bolagsverket: check stopped: %s", exc)
                    self._l10n_se_bv_set_error(str(exc)[:200])
                    return
                partner.write({"l10n_se_bv_error": str(exc)[:200],
                               "l10n_se_bv_next_check": now + timedelta(hours=RETRY_HOURS)})
            except Exception as exc:  # noqa: BLE001 – one odd record must not stop the queue
                _logger.exception("Bolagsverket: partner %s could not be checked", partner.id)
                partner.write({"l10n_se_bv_error": str(exc)[:200],
                               "l10n_se_bv_next_check": now + timedelta(hours=RETRY_HOURS)})
            incomplete_in_a_row = 0
            self._l10n_se_bv_commit()
            done += 1
        if not done:
            ICP = self.env["ir.config_parameter"].sudo()
            last = ICP.get_param(PREFIX + "last_ping")
            if not last or fields.Datetime.from_string(last) < now - timedelta(days=1):
                ICP.set_param(PREFIX + "last_ping", fields.Datetime.to_string(now))   # once a day, even on failure
                try:
                    client.is_alive()
                except bv.BolagsverketError as exc:
                    self._l10n_se_bv_set_error(str(exc)[:200])
                    return
        self._l10n_se_bv_set_error(False)
        if done:
            _logger.info("Bolagsverket: checked %s partners", done)

    @api.model
    def _l10n_se_bv_set_error(self, error):
        """Only written when it changes: every ir.config_parameter write clears caches in all workers."""
        ICP = self.env["ir.config_parameter"].sudo()
        if (ICP.get_param(PREFIX + "last_error") or False) != (error or False):
            ICP.set_param(PREFIX + "last_error", error or False)

    @api.model
    def _l10n_se_bv_commit(self):
        # own method so tests can replace it (commit is forbidden in tests)
        self.env.cr.commit()
