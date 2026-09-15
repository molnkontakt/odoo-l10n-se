from odoo import api, models


class AccountMoveSend(models.AbstractModel):
    _inherit = "account.move.send"

    @api.model
    def _hook_if_success(self, moves_data, from_cron=False):
        for move, d in moves_data.items():
            if "hand" in d["sending_methods"]:
                user = self.env["res.users"].browse(d["author_user_id"]) if d.get("author_user_id") else None
                move._mark_hand_delivered(user=user)
        return super()._hook_if_success(moves_data, from_cron=from_cron)
