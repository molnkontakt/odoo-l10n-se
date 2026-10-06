from odoo import api, fields, models

PREFIX = "l10n_se_bolagsverket."


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    l10n_se_bv_enabled = fields.Boolean(string="Bolagsverket", config_parameter=PREFIX + "enabled")
    l10n_se_bv_client_id = fields.Char(string="Client id", config_parameter=PREFIX + "client_id")
    l10n_se_bv_environment = fields.Selection(
        [("prod", "Production"), ("test", "Test")], string="Environment", default="prod",
        config_parameter=PREFIX + "environment")
    l10n_se_bv_responsible_id = fields.Many2one(related="company_id.l10n_se_bv_responsible_id", readonly=False)
    l10n_se_bv_secret_set = fields.Boolean(compute="_compute_l10n_se_bv_status")
    l10n_se_bv_last_run = fields.Char(compute="_compute_l10n_se_bv_status", string="Last check run")
    l10n_se_bv_last_error = fields.Char(compute="_compute_l10n_se_bv_status", string="Last error")

    @api.depends("l10n_se_bv_enabled")
    def _compute_l10n_se_bv_status(self):
        ICP = self.env["ir.config_parameter"].sudo()
        for rec in self:
            rec.l10n_se_bv_secret_set = bool(ICP.get_param(PREFIX + "client_secret"))
            cron = self.env.ref("l10n_se_bolagsverket.ir_cron_bolagsverket_check", raise_if_not_found=False)
            rec.l10n_se_bv_last_run = cron and cron.sudo().lastcall and fields.Datetime.to_string(cron.sudo().lastcall) \
                or False
            rec.l10n_se_bv_last_error = ICP.get_param(PREFIX + "last_error") or False
