from odoo import fields, models

CHART_TYPES = [
    ("EUBAS97", "EUBAS97 (any BAS version)"),
    ("BAS2025", "BAS2025"),
    ("BAS2026", "BAS2026"),
    ("NE2007", "NE2007 (sole traders, K1)"),
]


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_se_sie_chart_type = fields.Selection(
        CHART_TYPES,
        string="SIE Chart Type",
        default="EUBAS97",
        help="Written as #KPTYP in SIE exports. The SIE standard reads any BAS20xx as EUBAS97, "
        "so EUBAS97 is understood by every program.",
    )
