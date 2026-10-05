from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    l10n_se_sie_import_id = fields.Many2one(
        "l10n_se.sie.import",
        string="SIE Import",
        index="btree_not_null",
        copy=False,
        readonly=True,
        ondelete="set null",
        help="The SIE import that created this entry.",
    )
    l10n_se_sie_key = fields.Char(
        string="SIE Voucher Key",
        index="btree_not_null",
        copy=False,
        readonly=True,
        help="Financial year, series and number of the SIE voucher this entry was imported from. "
        "An entry with the same key is never imported twice into the same company.",
    )

    # The database backstop against importing the same voucher twice (two imports at once).
    _l10n_se_sie_key_uniq = models.UniqueIndex(
        "(company_id, l10n_se_sie_key) WHERE l10n_se_sie_key IS NOT NULL",
        "This SIE voucher has already been imported into the company.",
    )
