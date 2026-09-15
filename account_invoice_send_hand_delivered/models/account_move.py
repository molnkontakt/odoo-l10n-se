from datetime import date

from markupsafe import Markup

from odoo import _, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_mark_hand_delivered(self):
        """Åtgärder → "Markera som lämnad för hand" i fakturalistan: samma som kryssrutan i Skicka-dialogen,
        för flera fakturor på en gång (batch-dialogen har inga kryssrutor)."""
        bad = self.filtered(lambda m: m.state != "posted" or not m.is_invoice(include_receipts=True))
        if bad:
            raise UserError(_("Bara bokförda fakturor kan markeras: %s", ", ".join(bad.mapped("name"))))
        for move in self:
            move._mark_hand_delivered()
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"type": "success", "message": _("%s fakturor markerade som lämnade för hand", len(self)),
                           "next": {"type": "ir.actions.act_window_close"}}}

    def _mark_hand_delivered(self, user=None):
        # Odoo sätter bara is_move_sent när PDF:en genereras första gången; sätt det explicit.
        self.ensure_one()
        self.is_move_sent = True
        self.message_post(
            body=Markup("<p><b>%s</b> %s av %s.</p>") % (_("Lämnad för hand"), date.today().isoformat(), (user or self.env.user).name),
            message_type="comment", subtype_xmlid="mail.mt_note")
