from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.pdf import merge_pdf


class AccountReminder(models.Model):
    _inherit = "account.reminder"

    channel = fields.Selection(selection_add=[("ekopost", "Brev via Ekopost")], ondelete={"ekopost": "set default"})

    @api.model
    def _default_channel(self, partner):
        Send = self.env["account.move.send"]
        if not partner.email and Send._ekopost_configured() and Send._ekopost_address_ok(partner):
            return "ekopost"
        return super()._default_channel(partner)

    def _send_ekopost(self):
        """Påminnelse-PDF:en följd av fakturorna (och avgiftsfakturan), sammanslagna till ett brev."""
        self.ensure_one()
        Send = self.env["account.move.send"]
        if not Send._ekopost_configured():
            raise UserError(_("Ekopost är inte konfigurerat (systemparametrarna ekopost.*)."))
        pdf = merge_pdf([self._render_pdf().raw] + [a.raw for a in self._attachments()])
        cid, envelopes, closed = Send._ekopost_post_pdfs([(self.partner_id, self.name, pdf)], f"Odoo {self.name}")
        eid, addr = envelopes[0]
        self.message_post(body=Send._ekopost_note(closed, eid, cid, addr, f" ({len(pdf) // 1024} kB)"),
                          message_type="comment", subtype_xmlid="mail.mt_note")
