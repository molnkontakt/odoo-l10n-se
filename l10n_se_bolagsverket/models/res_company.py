from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_se_bv_responsible_id = fields.Many2one(
        "res.users", string="Bolagsverket follow-up",
        help="Gets a to-do when a customer or supplier goes bankrupt, into liquidation or reorganisation, is "
             "deregistered, or when its name doesn't match its organisation number. Empty: only a note on the "
             "contact.")
