from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class AccountReminder(models.Model):
    """En skickad (eller förberedd) betalningspåminnelse: en kund, en nivå, en eller flera förfallna fakturor,
    eventuell avgiftsfaktura. Är också det som mailmallen och PDF-rapporten renderas på."""
    _name = "account.reminder"
    _description = "Betalningspåminnelse"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one("res.company", required=True, readonly=True)
    partner_id = fields.Many2one("res.partner", string="Kund", required=True, readonly=True, index=True)
    level_id = fields.Many2one("account.reminder.level", string="Nivå", required=True, readonly=True)
    date = fields.Date(default=fields.Date.context_today, readonly=True)
    move_ids = fields.Many2many("account.move", "account_reminder_move_rel", "reminder_id", "move_id",
                                string="Fakturor", readonly=True)
    fee_move_id = fields.Many2one("account.move", string="Avgiftsfaktura", readonly=True, copy=False)
    fee_amount = fields.Monetary(compute="_compute_amounts", currency_field="currency_id")
    amount_overdue = fields.Monetary(string="Förfallet belopp", compute="_compute_amounts", currency_field="currency_id")
    amount_total = fields.Monetary(string="Att betala", compute="_compute_amounts", currency_field="currency_id")
    currency_id = fields.Many2one(related="company_id.currency_id")
    state = fields.Selection([("draft", "Förberedd"), ("sent", "Skickad"), ("cancel", "Avbruten")],
                             default="draft", readonly=True, tracking=True)
    user_id = fields.Many2one("res.users", string="Skickad av", readonly=True)
    channel = fields.Selection(
        [("email", "E-post"), ("manual", "Manuellt (utskriven, lagd i brevlådan)")], string="Sätt", default="email", required=True,
        help="Hur påminnelsen levereras. Andra moduler kan lägga till sätt (brev, SMS) med selection_add och en metod _send_<kod>.",
    )
    pdf_attachment_id = fields.Many2one("ir.attachment", string="Påminnelse-PDF", readonly=True, copy=False)

    @api.depends("partner_id", "level_id", "date")
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.level_id.name or _('Påminnelse')} {rec.date or ''} – {rec.partner_id.name or ''}"

    @api.depends("move_ids.amount_residual", "fee_move_id.amount_residual", "fee_move_id.amount_total")
    def _compute_amounts(self):
        for rec in self:
            rec.amount_overdue = sum(rec.move_ids.mapped("amount_residual"))
            rec.fee_amount = rec.fee_move_id.amount_total if rec.fee_move_id else 0.0
            rec.amount_total = rec.amount_overdue + (rec.fee_move_id.amount_residual if rec.fee_move_id else 0.0)

    # ------------------------------------------------------------------ hjälpare för mallar/rapport

    def _payment_lines(self):
        """Rader att visa i PDF och mail: fakturorna och avgiftsfakturan, med restbelopp."""
        self.ensure_one()
        moves = self.move_ids.sorted("invoice_date_due")
        if self.fee_move_id:
            moves |= self.fee_move_id
        return moves

    def _bank_account(self):
        self.ensure_one()
        bank = self.move_ids[:1].partner_bank_id or self.company_id.partner_id.bank_ids[:1]
        return bank.acc_number or ""

    # ------------------------------------------------------------------ skapande

    @api.model
    def _prepare_fee_move(self, partner, level, moves):
        product = level.fee_product_id
        price = level.fee_amount or product.lst_price
        names = ", ".join(moves.mapped("name"))
        return {
            "move_type": "out_invoice",
            "company_id": level.company_id.id,
            **({"journal_id": level.fee_journal_id.id} if level.fee_journal_id else {}),
            "partner_id": partner.id,
            "invoice_date": fields.Date.context_today(self),
            "invoice_origin": names,
            "narration": False,
            "invoice_line_ids": [Command.create({
                "product_id": product.id,
                "name": f"{product.name}, {names}",
                "quantity": 1,
                "price_unit": price,
                "tax_ids": [Command.set(product.taxes_id.filtered(lambda t: t.company_id == level.company_id).ids)],
            })],
        }

    @api.model
    def _default_channel(self, partner):
        """Förvalt sätt för en kund – e-post om det finns, annars manuellt. Utökas av andra moduler."""
        return "email" if partner.email else "manual"

    @api.model
    def create_for(self, partner, level, moves, channel=None):
        """Skapar påminnelsen och (om nivån har avgift) avgiftsfakturan, bokförd. Skickar inget."""
        moves = moves.filtered(lambda m: m.partner_id == partner and m.company_id == level.company_id)
        if not moves:
            raise UserError(_("Inga fakturor att påminna om för %s.", partner.name))
        reminder = self.create({"company_id": level.company_id.id, "partner_id": partner.id, "level_id": level.id,
                                "move_ids": [Command.set(moves.ids)], "channel": channel or self._default_channel(partner)})
        if level.fee_product_id:
            fee = self.env["account.move"].with_company(level.company_id).create(self._prepare_fee_move(partner, level, moves))
            fee.reminder_fee_for_id = reminder
            fee.action_post()
            reminder.fee_move_id = fee
        return reminder

    # ------------------------------------------------------------------ utskick

    def _attachments(self):
        """Fakturornas PDF:er (genereras vid behov) och avgiftsfakturans."""
        self.ensure_one()
        moves = self._payment_lines()
        missing = moves.filtered(lambda m: not m.invoice_pdf_report_id)
        if missing:
            # PDF utan utskick; markerar inte fakturan som skickad annat än första gången (Odoo-beteende)
            self.env["account.move.send"]._generate_and_send_invoices(missing, sending_methods=["manual"])
        return moves.mapped("invoice_pdf_report_id")

    def _render_pdf(self):
        """Påminnelsen som PDF (rapporten report_reminder); sparas som bilaga på påminnelsen."""
        self.ensure_one()
        if self.pdf_attachment_id:
            return self.pdf_attachment_id
        pdf, _type = self.env["ir.actions.report"]._render_qweb_pdf("account_invoice_reminder.report_reminder", res_ids=self.ids)
        att = self.env["ir.attachment"].create({
            "name": f"{self.name}.pdf", "raw": pdf, "mimetype": "application/pdf",
            "res_model": self._name, "res_id": self.id,
        })
        self.pdf_attachment_id = att
        return att

    def _send_email(self):
        self.ensure_one()
        template = self.level_id.mail_template_id
        if not template:
            raise UserError(_("Nivån %s har ingen mailmall.", self.level_id.name))
        if not self.partner_id.email:
            raise UserError(_("%s saknar e-postadress.", self.partner_id.name))
        attachments = self._attachments()
        # mallen bifogar påminnelse-PDF:en själv (report_template_ids); fakturorna läggs till här
        template.send_mail(
            self.id, force_send=True,
            email_values={"attachment_ids": [Command.link(a.id) for a in attachments]},
        )

    def _send_manual(self):
        """Ingen sändning: PDF:en (och fakturorna) finns på påminnelsen för utskrift; noteringen säger att den
        lämnats för hand."""
        self.ensure_one()
        self._attachments()
        self.message_post(body=_("Lämnad för hand (utskriven/lagd i brevlådan) av %s.", self.env.user.name),
                          message_type="comment", subtype_xmlid="mail.mt_note")

    def _send(self):
        self.ensure_one()
        self._render_pdf()
        handler = getattr(self, f"_send_{self.channel}", None)
        if not handler:
            raise UserError(_("Inget sändningssätt för %s.", self.channel))
        handler()

    def action_send(self):
        for rec in self:
            if rec.state != "draft":
                continue
            rec._send()
            rec._mark_sent()
        return True

    def _mark_sent(self):
        """Uppdaterar fakturorna och loggar. Separat så att andra kanaler (SMS, brev) kan återanvända den."""
        self.ensure_one()
        self.write({"state": "sent", "user_id": self.env.user.id, "date": fields.Date.context_today(self)})
        for move in self.move_ids:
            move.write({"reminder_level_id": self.level_id.id, "reminder_date": self.date,
                        "reminder_count": move.reminder_count + 1})
        channel = dict(self._fields["channel"]._description_selection(self.env)).get(self.channel, self.channel)
        body = Markup("<p><b>%s</b> (%s) %s, %s%s.</p>") % (
            self.level_id.name, _("nivå %s", self.level_id.sequence), self.date, channel,
            (Markup(", ") + _("avgiftsfaktura %s", self.fee_move_id.name)) if self.fee_move_id else "")
        for move in self.move_ids:
            move.message_post(body=body, message_type="comment", subtype_xmlid="mail.mt_note")
        self.message_post(body=_("Skickad."), message_type="comment", subtype_xmlid="mail.mt_note")

    def action_cancel(self):
        for rec in self:
            if rec.state == "sent":
                raise UserError(_("%s är redan skickad.", rec.name))
            if rec.fee_move_id and rec.fee_move_id.state == "posted":
                # Bokförd (kanske hashad) avgiftsfaktura kan inte backas – kreditera och stäm av
                rec.fee_move_id._reverse_moves(
                    [{"date": fields.Date.context_today(self), "ref": _("Påminnelse %s avbruten", rec.name)}], cancel=True)
            elif rec.fee_move_id:
                rec.fee_move_id.button_cancel()
            rec.state = "cancel"
        return True

    def action_print(self):
        return self.env.ref("account_invoice_reminder.action_report_reminder").report_action(self)
