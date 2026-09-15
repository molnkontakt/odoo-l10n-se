from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    reminder_level_id = fields.Many2one("account.reminder.level", string="Senaste påminnelsenivå", readonly=True, copy=False)
    reminder_date = fields.Date(string="Senast påmind", readonly=True, copy=False)
    reminder_count = fields.Integer(string="Antal påminnelser", readonly=True, copy=False, default=0)
    reminder_ids = fields.Many2many("account.reminder", "account_reminder_move_rel", "move_id", "reminder_id",
                                    string="Påminnelser", readonly=True, copy=False)
    reminder_fee_for_id = fields.Many2one("account.reminder", string="Avgift för påminnelse", readonly=True, copy=False,
                                          help="Satt på avgiftsfakturor: de påminns inte själva.")
    reminder_next_level_id = fields.Many2one(
        "account.reminder.level", string="Nästa påminnelsenivå", compute="_compute_reminder_next_level",
        search="_search_reminder_next_level",
        help="Nivån som skulle skickas om påminnelse görs idag. Tom = inget att göra.",
    )

    def _reminder_candidate_domain(self):
        return [("move_type", "=", "out_invoice"), ("state", "=", "posted"), ("payment_state", "in", ("not_paid", "partial")),
                ("invoice_date_due", "<", fields.Date.context_today(self)), ("reminder_fee_for_id", "=", False)]

    @api.depends("invoice_date_due", "payment_state", "reminder_level_id", "state")
    def _compute_reminder_next_level(self):
        Level = self.env["account.reminder.level"]
        today = fields.Date.context_today(self)
        for move in self:
            ok = (move.move_type == "out_invoice" and move.state == "posted" and move.payment_state in ("not_paid", "partial")
                  and not move.reminder_fee_for_id)
            move.reminder_next_level_id = Level._next_level(move, today) if ok else False

    def _search_reminder_next_level(self, operator, value):
        if operator not in ("=", "!=") or value not in (False, True):
            raise UserError(_("Sök bara på satt/inte satt för nästa påminnelsenivå."))
        candidates = self.search(self._reminder_candidate_domain())
        ids = candidates.filtered("reminder_next_level_id").ids
        want_set = (operator == "=") != (value is False)      # "= True" / "!= False" → satt
        return [("id", "in", ids)] if want_set else [("id", "not in", ids)]

    def action_open_reminder_wizard(self):
        return {
            "type": "ir.actions.act_window", "res_model": "account.reminder.send.wizard", "view_mode": "form", "target": "new",
            "name": _("Skicka betalningspåminnelse"),
            "context": {"active_model": "account.move", "active_ids": self.ids},
        }
