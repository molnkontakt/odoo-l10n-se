from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    l10n_se_auto_debit = fields.Boolean(
        string="Paid by direct debit",
        copy=False,
        tracking=True,
        help="The supplier collects this bill from the company's account (autogiro or an agreed direct "
        "debit). Payment files never include it: reconcile the bill against the bank line instead.",
    )
