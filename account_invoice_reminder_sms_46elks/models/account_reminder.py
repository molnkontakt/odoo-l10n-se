from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountReminder(models.Model):
    _inherit = "account.reminder"

    channel = fields.Selection(selection_add=[("sms", "SMS via 46elks")], ondelete={"sms": "set default"})

    @api.model
    def _default_channel(self, partner, level=None):
        Send = self.env["account.move.send"]
        if not (level and level.require_letter) and not partner.email and Send._sms_configured() and Send._sms_number(partner):
            return "sms"
        return super()._default_channel(partner, level)

    def _sms_reminder_text(self):
        self.ensure_one()
        Send = self.env["account.move.send"]
        oldest = self.move_ids.sorted("invoice_date_due")[:1]
        return Send._sms_param("reminder_text").format(
            company=self.company_id.name, level=self.level_id.name.upper(), partner=self.partner_id.name,
            names=", ".join(self.move_ids.mapped("name")), overdue=f"{self.amount_overdue:.0f}", due=oldest.invoice_date_due,
            fee=f"{self.fee_amount + self.previous_fee_amount:.0f}", fee_name=self.fee_move_id.name or "", total=f"{self.amount_total:.0f}",
            pay_by=self.date_due or "",
            bank=self._bank_account() or "?", url=Send._sms_portal_url(oldest))

    def _send_sms(self):
        self.ensure_one()
        Send = self.env["account.move.send"]
        to = Send._sms_number(self.partner_id)
        if not to:
            raise UserError(_("%s: inget telefonnummer på kunden.", self.partner_id.name))
        msg = self._sms_reminder_text()
        res, cost, dry = Send._sms_post(to, msg)
        self.message_post(body=Send._sms_note(_("Påminnelse skickad som SMS"), to, res, cost, msg, dry),
                          message_type="comment", subtype_xmlid="mail.mt_note")
