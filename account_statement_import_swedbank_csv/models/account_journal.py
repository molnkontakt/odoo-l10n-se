from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    swedbank_swish_auto_reconcile = fields.Boolean(
        string="Stäm av Swish automatiskt vid import",
        help="När en Swish-inbetalning kan knytas till en kund via avsändarens mobilnummer och kunden "
             "har exakt en öppen kundfaktura på samma belopp, stäms bankraden av mot fakturan direkt "
             "när kontoutdraget importeras. Övriga rader lämnas till avstämningsvyn.",
    )
