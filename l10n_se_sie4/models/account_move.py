import hashlib
import json

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
    l10n_se_sie_fingerprint = fields.Char(
        string="SIE Fingerprint",
        copy=False,
        readonly=True,
        help="A hash of the entry as the SIE import created it. Undoing the import is refused "
        "for an entry that has been changed since.",
    )

    # The database backstop against importing the same voucher twice (two imports at once). A
    # cancelled entry does not hold its voucher: it can be imported again.
    _l10n_se_sie_key_uniq = models.UniqueIndex(
        "(company_id, l10n_se_sie_key) WHERE l10n_se_sie_key IS NOT NULL AND state != 'cancel'",
        "This SIE voucher has already been imported into the company.",
    )

    def _l10n_se_sie_fingerprint(self):
        """A hash of what the entry books: date, journal, reference and every line's account,
        partner, amounts, label, analytic distribution and taxes. Not the state or the number,
        so posting an imported draft is not a change."""
        self.ensure_one()
        lines = [
            (
                line.account_id.id, line.partner_id.id, round(line.debit, 2),
                round(line.credit, 2), line.currency_id.id, round(line.amount_currency, 2),
                line.name or "", sorted((line.analytic_distribution or {}).items()),
                line.tax_ids.ids, line.tax_line_id.id,
            )
            for line in self.line_ids.sorted("id")
        ]
        data = [str(self.date), self.journal_id.id, self.company_id.id, self.ref or "",
                self.move_type, lines]
        return hashlib.sha256(json.dumps(data, default=str).encode()).hexdigest()
