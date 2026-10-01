from odoo import api, fields, models
from odoo.addons.l10n_se_compliance_calendar import rules
from odoo.exceptions import ValidationError

MONTH_SELECTION = [
    ("1", "January"), ("2", "February"), ("3", "March"), ("4", "April"),
    ("5", "May"), ("6", "June"), ("7", "July"), ("8", "August"),
    ("9", "September"), ("10", "October"), ("11", "November"), ("12", "December"),
]


class L10nSeComplianceCustomRule(models.Model):
    _name = "l10n_se.compliance.custom.rule"
    _description = "Compliance Calendar Custom Rule"
    _order = "sequence, id"

    name = fields.Char(string="Title", required=True)
    description = fields.Text()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
        help="Leave empty to apply the rule to every company that uses the compliance calendar.",
    )
    rule_type = fields.Selection(
        [("fixed", "Fixed day every year"), ("relative", "Relative to an anchor date")],
        string="Type",
        required=True,
        default="fixed",
    )
    month = fields.Selection(MONTH_SELECTION)
    day = fields.Integer(
        default=1, help="Day of the month. A day after the end of the month means its last day "
        "(31 = the last day of every month)."
    )
    anchor_name = fields.Char(
        string="Anchor",
        help="Name of the anchor dates this rule is relative to, e.g. “Annual meeting”. One date "
        "is computed for each anchor date with this name.",
    )
    offset = fields.Integer(string="Amount", default=0)
    offset_unit = fields.Selection(
        [("days", "Days"), ("weeks", "Weeks"), ("months", "Months")],
        string="Unit",
        default="days",
    )
    offset_direction = fields.Selection(
        [("before", "Before"), ("after", "After")], string="Direction", default="before"
    )
    non_business_day = fields.Selection(
        [
            ("keep", "Keep the date"),
            ("previous", "Previous weekday"),
            ("next", "Next weekday"),
        ],
        string="On a Weekend or Holiday",
        required=True,
        default="keep",
        help="What to do when the date falls on a Saturday, Sunday, public holiday, Midsummer "
        "Eve, Christmas Eve or New Year's Eve. For a deadline (“at the latest”), the previous "
        "weekday is the safe choice.",
    )

    @api.constrains("rule_type", "month", "day", "anchor_name", "offset")
    def _check_rule(self):
        for rule in self:
            if rule.rule_type == "fixed" and (not rule.month or not 1 <= rule.day <= 31):
                raise ValidationError(self.env._(
                    "The rule “%(name)s” needs a month and a day between 1 and 31.", name=rule.name
                ))
            if rule.rule_type == "relative" and not (rule.anchor_name or "").strip():
                raise ValidationError(self.env._(
                    "The rule “%(name)s” needs the name of an anchor date.", name=rule.name
                ))
            if rule.offset < 0:
                raise ValidationError(self.env._(
                    "The amount must not be negative; choose Before or After instead."
                ))

    def _l10n_se_cc_entries(self, company, start, end):
        """Dates of these rules for ``company`` in ``[start, end]`` as (key, date, title,
        description, kind) tuples."""
        result = []
        Anchor = self.env["l10n_se.compliance.anchor"]
        for rule in self:
            if rule.rule_type == "fixed":
                for year in range(start.year, end.year + 1):
                    day = rules.clamped_date(year, int(rule.month), rule.day)
                    day = rules.adjust_non_business_day(day, rule.non_business_day)
                    if start <= day <= end:
                        result.append((
                            f"custom:{rule.id}:{year}", day, rule.name, rule.description or "",
                        ))
                continue
            for anchor in Anchor._l10n_se_cc_for_company(company, rule.anchor_name):
                day = rules.offset_date(
                    anchor.date, rule.offset, rule.offset_unit or "days",
                    rule.offset_direction or "before",
                )
                day = rules.adjust_non_business_day(day, rule.non_business_day)
                if not start <= day <= end:
                    continue
                relation = self.env._(
                    "%(amount)s %(unit)s %(direction)s %(anchor)s (%(date)s).",
                    amount=rule.offset,
                    unit=dict(rule._fields["offset_unit"]._description_selection(self.env)).get(
                        rule.offset_unit, ""
                    ).lower(),
                    direction=dict(
                        rule._fields["offset_direction"]._description_selection(self.env)
                    ).get(rule.offset_direction, "").lower(),
                    anchor=anchor.name,
                    date=anchor.date.isoformat(),
                )
                description = " ".join(filter(None, [rule.description or "", relation]))
                result.append((f"custom:{rule.id}:{anchor.year}", day, rule.name, description))
        return result
