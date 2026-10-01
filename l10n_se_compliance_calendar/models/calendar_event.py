from odoo import api, fields, models
from odoo.exceptions import AccessError

KIND_SELECTION = [
    ("vat", "VAT"),
    ("ec_sales_list", "EC Sales List"),
    ("employer", "Employer"),
    ("f_tax", "F-tax"),
    ("income_tax", "Income Tax Return"),
    ("agm", "Annual General Meeting"),
    ("annual_report", "Annual Report"),
    ("custom", "Custom Rule"),
    ("anchor", "Anchor Date"),
]


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    l10n_se_cc_company_id = fields.Many2one(
        "res.company",
        string="Compliance Calendar Company",
        index="btree_not_null",
        ondelete="cascade",
        readonly=True,
        help="Set on dates created by the compliance calendar. Only these events are updated or "
        "removed by it.",
    )
    l10n_se_cc_key = fields.Char(
        string="Compliance Calendar Key",
        readonly=True,
        copy=False,
        help="Stable key per rule and period, used to update the event instead of creating a "
        "duplicate.",
    )
    l10n_se_cc_kind = fields.Selection(KIND_SELECTION, string="Date Type", readonly=True)
    l10n_se_cc_legal_ref = fields.Char(string="Legal Basis", readonly=True)
    l10n_se_cc_hash = fields.Char(string="Compliance Calendar Hash", readonly=True, copy=False)

    _l10n_se_cc_key_uniq = models.Constraint(
        "UNIQUE(l10n_se_cc_company_id, l10n_se_cc_key)",
        "A compliance calendar key must be unique per company.",
    )

    @api.model
    def action_l10n_se_cc_refresh(self):
        """*Update Now*: synchronise the selected companies (all companies in the switcher)."""
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(self.env._("Only administrators can update the compliance calendar."))
        self.env.companies.sudo()._l10n_se_cc_sync()
        return {"type": "ir.actions.client", "tag": "soft_reload"}
