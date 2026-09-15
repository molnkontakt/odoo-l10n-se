from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    bankgirot_auto_reconcile = fields.Boolean(
        string="Stäm av Bankgirot automatiskt vid import",
        help="När alla detaljrader i Bankgirots insättningsuppgifter matchar varsin öppen kundfaktura och "
             "restbeloppen tillsammans är lika med bankraden, stäms raden av mot fakturorna direkt. "
             "Delvis matchade klumpsummor lämnas till avstämningsvyn med uppdelningen i chattern.",
    )

    def action_open_bankgirot_import_wizard(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Importera Bankgirot insättningsuppgifter",
            "res_model": "account.bankgirot.import.wizard",
            "view_mode": "form",
            "target": "new",
        }
