from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import formatLang


class AccountReminderSendWizard(models.TransientModel):
    """Granskning före utskick: en rad per kund och nivå (det som blir en påminnelse, med avgiften en gång) och
    under den en rad per faktura med datum, dagar försenad och kvar att betala.
    Öppnas från fakturalistan (markerade fakturor) eller från menyn (alla aktuella)."""
    _name = "account.reminder.send.wizard"
    _description = "Skicka betalningspåminnelser"

    line_ids = fields.One2many("account.reminder.send.wizard.line", "wizard_id", string="Påminnelser")
    invoice_line_ids = fields.One2many("account.reminder.send.wizard.invoice", "wizard_id", string="Fakturor")
    skipped = fields.Text(string="Hoppas över", readonly=True)
    only_prepare = fields.Boolean(
        string="Bara förbered (skicka inte)",
        help="Skapar påminnelserna som Förberedd så att de kan granskas och skickas från Kunder → Betalningspåminnelser.",
    )
    count = fields.Integer(compute="_compute_count")
    fee_note = fields.Text(compute="_compute_fee_note")

    @api.depends("line_ids.selected")
    def _compute_count(self):
        for w in self:
            w.count = len(w.line_ids.filtered("selected"))

    @api.depends("line_ids.level_id")
    def _compute_fee_note(self):
        """Förklarar avgiften: en rad på påminnelsen (ingen faktura), bokförs vid betalning med nivåns knapp i
        bankavstämningen, och en avgift per påminnelse oavsett antal fakturor. Tom när ingen nivå har avgift."""
        for w in self:
            levels = w.line_ids.level_id.filtered("fee_amount")
            if not levels:
                w.fee_note = ""
                continue
            accounts = levels.fee_account_id
            account = ", ".join(accounts.mapped("display_name")) if accounts else _("inget intäktskonto valt på nivån")
            # samma etikett som nivåns avstämningsknapp (reminder_level._ensure_reconcile_model); _() utanför
            # genexpr så att språket hittas
            buttons = []
            for level in levels:
                buttons.append(f'"{_("Påminnelseavgift (%s)", level.name)}"')
            w.fee_note = _(
                "Avgiften läggs som en rad på påminnelsen (PDF/e-post) – ingen ny faktura skapas. Den bokförs på "
                "nivåns intäktskonto (%(account)s) först när kunden betalar, med knappen %(button)s i "
                "bankavstämningen. Avgiften gäller per påminnelse enligt lag (1981:739) om ersättning för "
                "inkassokostnader, oavsett antal fakturor.",
                account=account, button=", ".join(buttons),
            )

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
        # samma ordning i båda listorna: kund, nivå; fakturorna efter förfallodatum
        ordered = sorted(groups.items(), key=lambda item: (item[0][0].display_name or "", item[0][1].sequence, item[0][1].id))
        Reminder = self.env["account.reminder"]
        res["line_ids"] = [Command.create({
            "partner_id": partner.id, "level_id": level.id, "move_ids": [Command.set(ms.ids)],
            "channel": Reminder._default_channel(partner, level),
        }) for (partner, level), ms in ordered]
        res["invoice_line_ids"] = [Command.create({"partner_id": partner.id, "level_id": level.id, "move_id": move.id})
                                   for (partner, level), ms in ordered
                                   for move in ms.sorted(lambda m: (m.invoice_date_due or fields.Date.today(), m.name or ""))]
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
    """En påminnelse som ska skapas: kund + nivå med alla dess fakturor. Avgiften är en per påminnelse."""
    _name = "account.reminder.send.wizard.line"
    _description = "Påminnelse att skicka"

    wizard_id = fields.Many2one("account.reminder.send.wizard", required=True, ondelete="cascade")
    selected = fields.Boolean(default=True)
    partner_id = fields.Many2one("res.partner", string="Kund", readonly=True)
    level_id = fields.Many2one("account.reminder.level", string="Nivå", readonly=True)
    move_ids = fields.Many2many("account.move", string="Fakturor", readonly=True)
    channel = fields.Selection(lambda self: self.env["account.reminder"]._fields["channel"]._description_selection(self.env),
                               string="Sätt", required=True, default="email")
    currency_id = fields.Many2one(related="level_id.currency_id")
    move_names = fields.Char(string="Fakturanummer", compute="_compute_info")
    move_count = fields.Integer(string="Antal fakturor", compute="_compute_info")
    amount_overdue = fields.Monetary(string="Förfallet", compute="_compute_info", currency_field="currency_id")
    fee_amount = fields.Monetary(string="Avgift", compute="_compute_info", currency_field="currency_id",
                                 help="Nivåns avgift plus obetalda avgifter från tidigare påminnelser på samma fakturor.")
    fee_basis = fields.Char(string="Avgiften avser", compute="_compute_info")
    email = fields.Char(related="partner_id.email")

    @api.depends("move_ids", "level_id")
    def _compute_info(self):
        Reminder = self.env["account.reminder"]
        for line in self:
            line.move_names = ", ".join(line.move_ids.mapped("name"))
            line.move_count = len(line.move_ids)
            line.amount_overdue = sum(line.move_ids.mapped("amount_residual"))
            fee = line.level_id.fee_amount
            previous = Reminder._previous_fee_amount_for(line.partner_id, line.move_ids)
            line.fee_amount = fee + previous
            if not line.fee_amount:
                line.fee_basis = _("ingen avgift")
            elif previous:
                digits = line.currency_id.decimal_places
                line.fee_basis = _("%(fee)s + %(previous)s tidigare",
                                   fee=formatLang(self.env, fee, digits=digits),
                                   previous=formatLang(self.env, previous, digits=digits))
            else:
                line.fee_basis = _("1 avgift per påminnelse")


class AccountReminderSendWizardInvoice(models.TransientModel):
    """En faktura i dialogen: visas under sin kund och nivå med datum, dagar försenad och kvar att betala.
    Vald/inte vald följer kundens rad (påminnelsen är det som väljs, inte fakturan)."""
    _name = "account.reminder.send.wizard.invoice"
    _description = "Faktura i påminnelse att skicka"

    wizard_id = fields.Many2one("account.reminder.send.wizard", required=True, ondelete="cascade")
    partner_id = fields.Many2one("res.partner", string="Kund", readonly=True)
    level_id = fields.Many2one("account.reminder.level", string="Nivå", readonly=True)
    move_id = fields.Many2one("account.move", string="Faktura", required=True, readonly=True)
    selected = fields.Boolean(compute="_compute_selected")
    name = fields.Char(string="Fakturanummer", related="move_id.name")
    invoice_date = fields.Date(string="Fakturadatum", related="move_id.invoice_date")
    invoice_date_due = fields.Date(string="Förfallodatum", related="move_id.invoice_date_due")
    days_overdue = fields.Integer(string="Dagar försenad", compute="_compute_days_overdue")
    currency_id = fields.Many2one(related="move_id.currency_id")
    amount_residual = fields.Monetary(string="Kvar att betala", related="move_id.amount_residual")

    @api.depends("wizard_id.line_ids.selected", "wizard_id.line_ids.partner_id", "wizard_id.line_ids.level_id",
                 "partner_id", "level_id")
    def _compute_selected(self):
        for inv in self:
            inv.selected = any(line.selected for line in inv.wizard_id.line_ids
                               if line.partner_id == inv.partner_id and line.level_id == inv.level_id)

    @api.depends("move_id.invoice_date_due")
    def _compute_days_overdue(self):
        today = fields.Date.context_today(self)
        for inv in self:
            due = inv.move_id.invoice_date_due
            inv.days_overdue = (today - due).days if due and due < today else 0
