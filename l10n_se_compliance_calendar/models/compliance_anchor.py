from odoo import api, fields, models
from odoo.exceptions import ValidationError


class L10nSeComplianceAnchor(models.Model):
    _name = "l10n_se.compliance.anchor"
    _description = "Compliance Calendar Anchor Date"
    _order = "date desc, name"

    name = fields.Char(
        required=True,
        help="Relative custom rules point to anchor dates by this name, e.g. “Annual meeting”. "
        "Enter one anchor date with the same name for each year.",
    )
    date = fields.Date(required=True)
    year = fields.Integer(compute="_compute_year", store=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
        help="Leave empty to use the date for every company.",
    )
    show_in_calendar = fields.Boolean(
        default=True, help="Also show the anchor date itself as an all-day event."
    )
    active = fields.Boolean(default=True)

    @api.depends("date")
    def _compute_year(self):
        for anchor in self:
            anchor.year = anchor.date.year if anchor.date else 0

    @staticmethod
    def _l10n_se_cc_name_key(name):
        return " ".join((name or "").split()).casefold()

    @api.constrains("name", "date", "company_id", "active")
    def _check_one_per_year(self):
        for anchor in self.filtered("active"):
            others = self.search([
                ("id", "!=", anchor.id),
                ("year", "=", anchor.year),
                ("company_id", "=", anchor.company_id.id),
            ])
            key = self._l10n_se_cc_name_key(anchor.name)
            if any(self._l10n_se_cc_name_key(o.name) == key for o in others):
                raise ValidationError(self.env._(
                    "There is already an anchor date “%(name)s” in %(year)s.",
                    name=anchor.name, year=anchor.year,
                ))

    def _l10n_se_cc_for_company(self, company, name=None):
        """Active anchors for ``company``: its own, plus the shared ones (no company) for a name
        and year it has no own date for."""
        anchors = self.with_context(active_test=True).search([
            ("company_id", "in", [False, company.id]),
        ])
        if name is not None:
            key = self._l10n_se_cc_name_key(name)
            anchors = anchors.filtered(lambda a: self._l10n_se_cc_name_key(a.name) == key)
        own = {
            (self._l10n_se_cc_name_key(a.name), a.year) for a in anchors if a.company_id
        }
        return anchors.filtered(
            lambda a: a.company_id or (self._l10n_se_cc_name_key(a.name), a.year) not in own
        )
