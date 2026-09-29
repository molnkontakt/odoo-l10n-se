from datetime import timedelta

from odoo import api, fields, models

# Slots are kept at least this long, or for the longest configured window if that is longer.
KEEP_MINUTES = 24 * 60


class MailServerRateSlot(models.Model):
    """Messages counted against a rate-limited mail server at a moment in time.

    A separate log, because sent mail.mail rows are often deleted right after sending
    (auto_delete) and cannot be counted afterwards.
    """

    _name = "mail.server.rate.slot"
    _description = "Mail server rate limit slot"
    _order = "at, id"
    _log_access = False

    server_id = fields.Many2one("ir.mail_server", string="Mail Server", required=True, ondelete="cascade", index=True)
    at = fields.Datetime(string="Time", required=True, default=fields.Datetime.now, index=True)
    count = fields.Integer(string="Messages", required=True, default=0)

    @api.autovacuum
    def _gc_rate_slots(self):
        windows = self.env["ir.mail_server"].sudo().with_context(active_test=False).search(
            [("rate_limit_count", ">", 0)]).mapped("rate_limit_minutes")
        keep = timedelta(minutes=max([KEEP_MINUTES, *windows]))
        self.sudo().search([("at", "<", fields.Datetime.now() - keep)]).unlink()
