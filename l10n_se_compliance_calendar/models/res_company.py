import hashlib
import json
import logging
from datetime import date, datetime, time

from markupsafe import Markup, escape

from odoo import Command, api, fields, models
from odoo.addons.l10n_se_compliance_calendar import rules
from odoo.exceptions import AccessError, ValidationError

_logger = logging.getLogger(__name__)

#: Context for every write to calendar events and the contact: no invitation, no alarm mail,
#: no chatter tracking, no contact details appended to the description.
SILENT_CONTEXT = {
    "no_mail_to_attendees": True,
    "dont_notify": True,
    "skip_contact_description": True,
    "mail_create_nosubscribe": True,
    "mail_create_nolog": True,
    "mail_notrack": True,
    "tracking_disable": True,
    "active_test": False,
}

#: Years after the current one that the calendar covers.
HORIZON_YEARS = 2


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_se_cc_active = fields.Boolean(
        string="Use Compliance Calendar",
        help="Create the company's statutory dates in Calendar. When switched off, the next "
        "update removes the company's current and future dates.",
    )
    l10n_se_cc_company_form = fields.Selection(
        [
            ("ab", "Limited company (AB)"),
            ("ef", "Sole trader (enskild firma)"),
            ("hb", "Trading partnership (HB/KB)"),
            ("ek_forening", "Economic association"),
            ("ideell_forening", "Non-profit association"),
            ("samfallighet", "Joint property association"),
        ],
        string="Company Form",
        default="ab",
        required=True,
    )
    l10n_se_cc_vat_period = fields.Selection(
        [
            ("none", "Not registered for VAT"),
            ("month", "Month"),
            ("quarter", "Quarter"),
            ("year", "Financial year"),
        ],
        string="VAT Period",
        default="none",
        required=True,
    )
    l10n_se_cc_turnover = fields.Selection(
        [("le40", "Up to SEK 40 million"), ("gt40", "Over SEK 40 million")],
        string="VAT Base per Year",
        default="le40",
        required=True,
        help="The taxable amount for VAT per year. Over SEK 40 million the VAT period must be a "
        "month and other dates apply for VAT and the employer declaration.",
    )
    l10n_se_cc_eu_trade = fields.Boolean(
        string="EU Trade",
        help="Sales to or purchases from other EU countries. Changes the date of an annual VAT "
        "return and enables the EC sales list.",
    )
    l10n_se_cc_ec_sales_list = fields.Selection(
        [("none", "No"), ("month", "Month"), ("quarter", "Quarter")],
        string="EC Sales List",
        default="none",
        required=True,
    )
    l10n_se_cc_employer = fields.Boolean(string="Employer")
    l10n_se_cc_f_tax = fields.Boolean(string="Pays F-tax")

    l10n_se_cc_show_vat = fields.Boolean(string="VAT Returns", default=True)
    l10n_se_cc_show_ec_sales_list = fields.Boolean(string="EC Sales Lists", default=True)
    l10n_se_cc_show_employer = fields.Boolean(string="Employer Declarations", default=True)
    l10n_se_cc_show_f_tax = fields.Boolean(string="F-tax Payments", default=True)
    l10n_se_cc_show_income_tax = fields.Boolean(string="Income Tax Return", default=True)
    l10n_se_cc_show_agm = fields.Boolean(string="Annual General Meeting", default=True)
    l10n_se_cc_show_annual_report = fields.Boolean(string="Annual Report", default=True)
    l10n_se_cc_show_custom = fields.Boolean(string="Custom Rules and Anchor Dates", default=True)

    l10n_se_cc_alarm_id = fields.Many2one(
        "calendar.alarm",
        string="Default Reminder",
        help="Optional reminder on every date. Reminders by e-mail or SMS reach nobody, since the "
        "only attendee has no address; a notification reminder shows for users who have the "
        "date in their calendar.",
    )
    l10n_se_cc_partner_id = fields.Many2one(
        "res.partner", string="Calendar Contact", readonly=True, copy=False
    )
    l10n_se_cc_filter_group_ids = fields.Many2many(
        "res.groups",
        "l10n_se_cc_company_filter_group_rel",
        "company_id",
        "group_id",
        string="Show for Groups",
        help="Users in these groups get the calendar contact as a selected filter in their "
        "calendar, so the dates show without searching.",
    )
    l10n_se_cc_last_sync = fields.Datetime(string="Last Updated", readonly=True, copy=False)

    @api.constrains("l10n_se_cc_turnover", "l10n_se_cc_vat_period")
    def _check_l10n_se_cc_vat_period(self):
        for company in self:
            if company.l10n_se_cc_turnover == "gt40" and company.l10n_se_cc_vat_period in (
                "quarter", "year"
            ):
                raise ValidationError(self.env._(
                    "With a VAT base over SEK 40 million the VAT period must be a month."
                ))

    # ------------------------------------------------------------------
    # Profile and dates
    # ------------------------------------------------------------------

    def _l10n_se_cc_profile(self):
        self.ensure_one()
        flags = {
            "vat": self.l10n_se_cc_show_vat,
            "ec_sales_list": self.l10n_se_cc_show_ec_sales_list,
            "employer": self.l10n_se_cc_show_employer,
            "f_tax": self.l10n_se_cc_show_f_tax,
            "income_tax": self.l10n_se_cc_show_income_tax,
            "agm": self.l10n_se_cc_show_agm,
            "annual_report": self.l10n_se_cc_show_annual_report,
        }
        return rules.Profile(
            company_form=self.l10n_se_cc_company_form or "ab",
            vat_period=self.l10n_se_cc_vat_period or "none",
            turnover=self.l10n_se_cc_turnover or "le40",
            eu_trade=self.l10n_se_cc_eu_trade,
            ec_sales_list=self.l10n_se_cc_ec_sales_list or "none",
            employer=self.l10n_se_cc_employer,
            f_tax=self.l10n_se_cc_f_tax,
            fy_last_month=int(self.fiscalyear_last_month or 12),
            fy_last_day=self.fiscalyear_last_day or 31,
            kinds=frozenset(kind for kind, on in flags.items() if on),
        )

    @api.model
    def _l10n_se_cc_horizon(self):
        today = fields.Date.context_today(self)
        return date(today.year, 1, 1), date(today.year + HORIZON_YEARS, 12, 31)

    def _l10n_se_cc_entries(self, start, end):
        """Everything the calendar should hold for this company in ``[start, end]``, as dicts
        with key, date, title, description, legal_ref and kind."""
        self.ensure_one()
        lang = self.partner_id.lang or self.env.lang or "en_US"
        company = self.with_context(lang=lang)
        result = [
            {
                "key": d.key,
                "date": d.date,
                "title": d.title,
                "description": d.description,
                "legal_ref": d.legal_ref,
                "kind": d.kind,
            }
            for d in rules.compute_deadlines(company._l10n_se_cc_profile(), start, end, company.env._)
        ]
        if not self.l10n_se_cc_show_custom:
            return result
        custom_rules = company.env["l10n_se.compliance.custom.rule"].search([
            ("company_id", "in", [False, self.id]),
        ])
        for key, day, title, description in custom_rules._l10n_se_cc_entries(self, start, end):
            result.append({
                "key": key, "date": day, "title": title, "description": description,
                "legal_ref": "", "kind": "custom",
            })
        anchors = company.env["l10n_se.compliance.anchor"]._l10n_se_cc_for_company(self)
        for anchor in anchors.filtered("show_in_calendar"):
            if start <= anchor.date <= end:
                result.append({
                    "key": f"anchor:{anchor.id}", "date": anchor.date, "title": anchor.name,
                    "description": "", "legal_ref": "", "kind": "anchor",
                })
        return result

    # ------------------------------------------------------------------
    # Contact and filters
    # ------------------------------------------------------------------

    def _l10n_se_cc_contact_name(self):
        self.ensure_one()
        lang = self.partner_id.lang or self.env.lang or "en_US"
        return self.with_context(lang=lang).env._("Compliance calendar – %(company)s", company=self.name)

    def _l10n_se_cc_ensure_partner(self):
        """The company's calendar contact, created on first use. It never has an e-mail address
        (enforced by a constraint on res.partner)."""
        self.ensure_one()
        partner = self.l10n_se_cc_partner_id
        name = self._l10n_se_cc_contact_name()
        Partner = self.env["res.partner"].sudo().with_context(SILENT_CONTEXT)
        if not partner:
            partner = Partner.search([("l10n_se_cc_company_id", "=", self.id)], limit=1)
        if not partner:
            partner = Partner.create({
                "name": name,
                "l10n_se_cc_company_id": self.id,
                "company_id": self.id,
                "type": "contact",
                "is_company": False,
                "lang": self.partner_id.lang or False,
            })
        else:
            vals = {}
            if partner.name != name:
                vals["name"] = name
            if not partner.active:
                vals["active"] = True
            if vals:
                partner.write(vals)
        if self.l10n_se_cc_partner_id != partner:
            self.sudo().l10n_se_cc_partner_id = partner
        return partner

    def _l10n_se_cc_sync_filters(self, partner):
        """Give users in the chosen groups a selected calendar filter on the contact. Filters
        are only added; a user who removes one gets it back at the next update only if still
        in a group, and nothing is removed when a group is taken away."""
        self.ensure_one()
        users = self.l10n_se_cc_filter_group_ids.all_user_ids.filtered(
            lambda u: not u.share and self in u.company_ids
        )
        if not users:
            return
        Filters = self.env["calendar.filters"].sudo().with_context(active_test=False)
        existing = Filters.search([("partner_id", "=", partner.id), ("user_id", "in", users.ids)])
        missing = users - existing.user_id
        existing.filtered(lambda f: not f.active).write({"active": True})
        if missing:
            Filters.create([
                {"user_id": user.id, "partner_id": partner.id, "partner_checked": True}
                for user in missing
            ])

    # ------------------------------------------------------------------
    # Synchronisation
    # ------------------------------------------------------------------

    def _l10n_se_cc_event_vals(self, entry, partner):
        description = Markup("<p>%s</p>") % entry["description"] if entry["description"] else Markup()
        if entry["legal_ref"]:
            description += Markup("<p>%s %s</p>") % (
                self.env._("Legal basis:"), entry["legal_ref"]
            )
        day = entry["date"]
        vals = {
            "name": entry["title"],
            "allday": True,
            "start": datetime.combine(day, time(8, 0)),
            "stop": datetime.combine(day, time(18, 0)),
            "start_date": day,
            "stop_date": day,
            "description": description,
            "partner_ids": [Command.set(partner.ids)],
            "alarm_ids": [Command.set(self.l10n_se_cc_alarm_id.ids)],
            "user_id": False,
            "privacy": "confidential",
            "show_as": "free",
            "active": True,
            "l10n_se_cc_company_id": self.id,
            "l10n_se_cc_key": entry["key"],
            "l10n_se_cc_kind": entry["kind"],
            "l10n_se_cc_legal_ref": entry["legal_ref"] or False,
        }
        digest = json.dumps(
            [entry["title"], day.isoformat(), str(escape(description)), entry["legal_ref"],
             entry["kind"], partner.id, self.l10n_se_cc_alarm_id.id],
            ensure_ascii=False,
        )
        vals["l10n_se_cc_hash"] = hashlib.sha256(digest.encode()).hexdigest()
        return vals

    def _l10n_se_cc_sync(self):
        """Create, update and remove the companies' calendar events.

        Only events with this module's key and company are touched. Events dated before the
        start of the horizon (1 January of the current year) are kept as history.
        """
        start, end = self._l10n_se_cc_horizon()
        Event = self.env["calendar.event"].sudo().with_context(SILENT_CONTEXT)
        for company in self.sudo():
            existing = Event.search([
                ("l10n_se_cc_company_id", "=", company.id),
                ("l10n_se_cc_key", "!=", False),
            ])
            by_key = {event.l10n_se_cc_key: event for event in existing}
            created = updated = 0
            if company.l10n_se_cc_active:
                partner = company._l10n_se_cc_ensure_partner()
                to_create = []
                for entry in company._l10n_se_cc_entries(start, end):
                    vals = company._l10n_se_cc_event_vals(entry, partner)
                    event = by_key.pop(entry["key"], None)
                    if event is None:
                        to_create.append(vals)
                    elif event.l10n_se_cc_hash != vals["l10n_se_cc_hash"] or not event.active:
                        event.write(vals)
                        updated += 1
                if to_create:
                    Event.create(to_create)
                    created = len(to_create)
                company._l10n_se_cc_sync_filters(partner)
            stale = Event.browse([
                event.id for event in by_key.values()
                if (event.start_date or event.start.date()) >= start
            ])
            removed = len(stale)
            stale.unlink()
            company.l10n_se_cc_last_sync = fields.Datetime.now()
            if created or updated or removed:
                _logger.info(
                    "compliance calendar %s: %s created, %s updated, %s removed",
                    company.id, created, updated, removed,
                )
        return True

    @api.model
    def _l10n_se_cc_cron(self):
        companies = self.sudo().search([
            "|", ("l10n_se_cc_active", "=", True), ("l10n_se_cc_partner_id", "!=", False),
        ])
        companies._l10n_se_cc_sync()

    def action_l10n_se_cc_sync(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(self.env._("Only administrators can update the compliance calendar."))
        self._l10n_se_cc_sync()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": self.env._("The compliance calendar has been updated."),
            },
        }

    @api.model
    def action_l10n_se_cc_open_settings(self):
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Compliance Calendar Settings"),
            "res_model": "res.company",
            "res_id": self.env.company.id,
            "view_mode": "form",
            "views": [(self.env.ref("l10n_se_compliance_calendar.res_company_view_form_l10n_se_cc").id, "form")],
            "target": "current",
        }
