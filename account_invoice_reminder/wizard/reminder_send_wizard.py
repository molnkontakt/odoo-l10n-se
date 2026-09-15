from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class AccountReminderSendWizard(models.TransientModel):
    """Granskning före utskick: en rad per kund och nivå med fakturorna, förfallet belopp och avgift.
    Öppnas från fakturalistan (markerade fakturor) eller från menyn (alla aktuella)."""
    _name = "account.reminder.send.wizard"
    _description = "Skicka betalningspåminnelser"

    line_ids = fields.One2many("account.reminder.send.wizard.line", "wizard_id", string="Påminnelser")
    skipped = fields.Text(string="Hoppas över", readonly=True)
    only_prepare = fields.Boolean(
        string="Bara förbered (skicka inte)",
        help="Skapar påminnelserna (och avgiftsfakturorna) som Förberedd så att de kan granskas och skickas från Kunder → Betalningspåminnelser.",
    )
    count = fields.Integer(compute="_compute_count")

    @api.depends("line_ids.selected")
    def _compute_count(self):
        for w in self:
            w.count = len(w.line_ids.filtered("selected"))

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        Move = self.env["account.move"]
        if self.env.context.get("active_model") == "account.move" and self.env.context.get("active_ids"):
            moves = Move.browse(self.env.context["active_ids"])
        else:
            moves = Move.search(Move._reminder_candidate_domain())
        groups, skipped = defaultdict(lambda: Move), []
        for move in moves:
            level = move.reminder_next_level_id
            if not level:
                if move.move_type != "out_invoice" or move.state != "posted":
                    why = _("inte en bokförd kundfaktura")
                elif move.payment_state not in ("not_paid", "partial"):
                    why = _("betald")
                elif move.reminder_fee_for_id:
                    why = _("avgiftsfaktura")
                elif not move.invoice_date_due or move.invoice_date_due >= fields.Date.context_today(self):
                    why = _("inte förfallen")
                elif move.reminder_level_id:
                    why = _("nästa nivå inte nådd (senast %s %s)", move.reminder_level_id.name, move.reminder_date)
                else:
                    why = _("för få dagar efter förfallodatum")
                skipped.append(f"{move.name}: {why}")
                continue
            groups[(move.partner_id, level)] |= move
        Reminder = self.env["account.reminder"]
        res["line_ids"] = [Command.create({
            "partner_id": partner.id, "level_id": level.id, "move_ids": [Command.set(ms.ids)],
            "channel": Reminder._default_channel(partner, level),
        }) for (partner, level), ms in groups.items()]
        res["skipped"] = "\n".join(skipped)
        return res

    def action_send(self):
        self.ensure_one()
        lines = self.line_ids.filtered("selected")
        if not lines:
            raise UserError(_("Inga påminnelser valda."))
        Reminder = self.env["account.reminder"]
        reminders = Reminder
        for line in lines:
            reminders |= Reminder.create_for(line.partner_id, line.level_id, line.move_ids, channel=line.channel)
        if not self.only_prepare:
            reminders.action_send()
        return {
            "type": "ir.actions.act_window", "res_model": "account.reminder", "name": _("Betalningspåminnelser"),
            "view_mode": "list,form", "domain": [("id", "in", reminders.ids)],
        }


class AccountReminderSendWizardLine(models.TransientModel):
    _name = "account.reminder.send.wizard.line"
    _description = "Påminnelse att skicka"

    wizard_id = fields.Many2one("account.reminder.send.wizard", required=True, ondelete="cascade")
    selected = fields.Boolean(default=True)
    partner_id = fields.Many2one("res.partner", string="Kund", readonly=True)
    level_id = fields.Many2one("account.reminder.level", string="Nivå", readonly=True)
    move_ids = fields.Many2many("account.move", string="Fakturor", readonly=True)
    channel = fields.Selection(lambda self: self.env["account.reminder"]._fields["channel"]._description_selection(self.env),
                               string="Sätt", required=True, default="email")
    move_names = fields.Char(compute="_compute_info")
    amount_overdue = fields.Float(string="Förfallet", compute="_compute_info")
    fee_amount = fields.Float(string="Avgift", compute="_compute_info")
    email = fields.Char(related="partner_id.email")

    @api.depends("move_ids", "level_id")
    def _compute_info(self):
        for line in self:
            line.move_names = ", ".join(line.move_ids.mapped("name"))
            line.amount_overdue = sum(line.move_ids.mapped("amount_residual"))
            line.fee_amount = line.level_id.fee_amount + self.env["account.reminder"]._previous_fee_amount_for(line.partner_id, line.move_ids)
