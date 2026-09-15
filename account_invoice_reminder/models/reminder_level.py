from odoo import _, api, fields, models


class AccountReminderLevel(models.Model):
    """En påminnelsenivå per bolag, t.ex. "Påminnelse" (dag 14, 60 kr) och "Inkassokrav" (dag 44, 180 kr).
    Nivåerna gås igenom i sequence-ordning; en faktura får nästa nivå när den är tillräckligt många dagar
    efter förfallodatum och inte redan påmints på den nivån (account.move.reminder_level_id).

    Avgiften faktureras INTE separat (som i Fortnox/Visma/Bokio): den står på påminnelsen och bokförs mot
    intäktskontot först när kunden betalar in den, via avstämningsmodellen (knappen) som skapas per nivå."""
    _name = "account.reminder.level"
    _description = "Påminnelsenivå"
    _order = "company_id, sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    days = fields.Integer(
        string="Dagar efter förfallodatum", required=True, default=14,
        help="Nivån blir aktuell när fakturan är så här många dagar efter sitt förfallodatum (räknat från förfallodatum, inte från föregående påminnelse).",
    )
    days_to_pay = fields.Integer(
        string="Betalningsfrist (dagar)", default=10,
        help="Dagar från påminnelsens datum till 'betala senast'. Inkassokrav: minst 8 dagar (inkassolagen 5 §, IMY).",
    )
    require_letter = fields.Boolean(
        string="Kräver brev",
        help="Förvalt sätt blir brev även när kunden har e-post (inkassokrav ska vara skriftliga och kunna bevisas avsända).",
    )
    fee_amount = fields.Monetary(
        string="Avgift", currency_field="currency_id",
        help="Påminnelse-/inkassoavgift som står på påminnelsen (t.ex. lagstadgade 60 kr). 0 = ingen avgift. "
             "Bokförs först när den betalas, med knappen i avstämningsvyn.",
    )
    fee_account_id = fields.Many2one(
        "account.account", string="Intäktskonto för avgift", check_company=True,
        domain="[('account_type', 'in', ('income', 'income_other')), ('company_ids', 'in', company_id)]",
        help="Kontot avgiften bokförs på när den betalas in (Sverige: 3930 Påminnelseavgifter, momsfritt).",
    )
    reconcile_model_id = fields.Many2one(
        "account.reconcile.model", string="Avstämningsknapp", readonly=True, copy=False,
        help="Manuell avstämningsmodell som bokar det överskjutande beloppet mot intäktskontot; skapas automatiskt.",
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

    @api.model_create_multi
    def create(self, vals_list):
        levels = super().create(vals_list)
        levels._ensure_reconcile_model()
        return levels

    def write(self, vals):
        res = super().write(vals)
        if {"fee_amount", "fee_account_id", "name"} & set(vals):
            self._ensure_reconcile_model()
        return res

    def _ensure_reconcile_model(self):
        """En manuell avstämningsmodell (knapp i avstämningsvyn) per nivå med avgift: bokar restbeloppet på
        bankraden – avgiften – mot intäktskontot. Odoo 19: trigger 'manual', 100 % av öppet belopp."""
        Model = self.env["account.reconcile.model"]
        for level in self:
            if not level.fee_amount or not level.fee_account_id:
                continue
            label = _("Påminnelseavgift (%s)", level.name)
            vals = {
                "name": label, "company_id": level.company_id.id, "trigger": "manual",
                "line_ids": [(5, 0, 0), (0, 0, {"account_id": level.fee_account_id.id, "amount_type": "percentage",
                                                "amount_string": "100", "label": label})],
            }
            if level.reconcile_model_id:
                level.reconcile_model_id.write(vals)
            else:
                level.reconcile_model_id = Model.create(vals)

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
