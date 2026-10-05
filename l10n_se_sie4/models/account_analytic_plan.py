from odoo import fields, models


class AccountAnalyticPlan(models.Model):
    _inherit = "account.analytic.plan"

    l10n_se_sie_dimension = fields.Integer(
        string="SIE Dimension",
        help="The SIE dimension number of this plan (1 = cost centre, 6 = project, 20 and up "
        "free). Set by the SIE import; the SIE export writes the plan's analytic accounts as "
        "objects of this dimension. Empty: the export numbers the plan from 20 up.",
    )
