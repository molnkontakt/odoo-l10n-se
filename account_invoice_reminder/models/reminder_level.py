from odoo import api, fields, models


class AccountReminderLevel(models.Model):
    """En påminnelsenivå per bolag, t.ex. "Påminnelse" (dag 14, 60 kr) och "Inkassokrav" (dag 44, 180 kr).
    Nivåerna gås igenom i sequence-ordning; en faktura får nästa nivå när den är tillräckligt många dagar
    efter förfallodatum och inte redan påmints på den nivån (account.move.reminder_level_id)."""
    _name = "account.reminder.level"
    _description = "Påminnelsenivå"
    _order = "company_id, sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    days = fields.Integer(
        string="Dagar efter förfallodatum", required=True, default=14,
        help="Nivån blir aktuell när fakturan är så här många dagar efter sitt förfallodatum (räknat från förfallodatum, inte från föregående påminnelse).",
    )
    fee_product_id = fields.Many2one(
        "product.product", string="Avgiftsprodukt",
        help="Tomt = ingen avgift. Annars skapas och bokförs en egen faktura med den här produkten (en rad per påminnelse), som bifogas påminnelsen.",
        domain="[('sale_ok', '=', True)]",
    )
    fee_journal_id = fields.Many2one(
        "account.journal", string="Journal för avgiftsfaktura", domain="[('type', '=', 'sale'), ('company_id', '=', company_id)]",
        help="Tomt = bolagets första försäljningsjournal.", check_company=True,
    )
    fee_amount = fields.Float(
        string="Avgift", help="Tomt/0 = produktens pris. Annars det här beloppet (t.ex. lagstadgade 60 kr).",
    )
    mail_template_id = fields.Many2one(
        "mail.template", string="Mailmall", domain="[('model', '=', 'account.reminder')]",
        default=lambda self: self.env.ref("account_invoice_reminder.mail_template_reminder", raise_if_not_found=False),
    )
    note = fields.Html(
        string="Text i påminnelsen", translate=True,
        help="Visas i PDF:en och i mailet ovanför fakturatabellen, t.ex. hänvisning till stadgar och vad som händer vid fortsatt dröjsmål.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [("days_positive", "CHECK(days >= 0)", "Dagar måste vara 0 eller mer.")]

    @api.model
    def _next_level(self, move, today=None):
        """Nästa nivå för en förfallen kundfaktura, eller tom recordset. Hoppar över nivåer vars dagar inte
        nåtts; tar den första i ordningen som är aktuell och ligger efter fakturans senaste nivå."""
        today = today or fields.Date.context_today(self)
        if not move.invoice_date_due or move.invoice_date_due >= today:
            return self.browse()
        overdue_days = (today - move.invoice_date_due).days
        current = move.reminder_level_id
        levels = self.search([("company_id", "=", move.company_id.id)])
        for level in levels:
            if current and (level.sequence, level.id) <= (current.sequence, current.id):
                continue
            if overdue_days >= level.days:
                return level
        return self.browse()
