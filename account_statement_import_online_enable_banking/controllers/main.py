# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import werkzeug
from werkzeug.urls import url_encode

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request


class EnableBankingController(http.Controller):
    @http.route("/enable_banking/callback", type="http", auth="public", csrf=False)
    def enable_banking_callback(self, state=None, code=None, error=None, **kw):
        """Landing page after the bank's consent screen.

        Enable Banking redirects here with `state` (ours, random per authorisation)
        and `code`. The provider is looked up by state so a stray or replayed
        callback cannot attach a session to another journal.
        """
        Provider = request.env["online.bank.statement.provider"].sudo()
        provider = Provider.search([("eb_auth_state", "=", state)], limit=1) if state else Provider
        params = {
            "action": request.env.ref(
                "account_statement_import_online.online_bank_statement_provider_action"
            ).id,
            "model": "online.bank.statement.provider",
            "view_type": "list",
        }
        if provider:
            if code and not error:
                try:
                    provider._enable_banking_finish_authorization(code)
                except UserError as err:  # fixed texts; the raw answer is in the server log
                    provider.message_post(body=str(err))
            else:
                provider.message_post(
                    body=request.env._("Enable Banking authorisation failed: %s", (error or request.env._("no code"))[:100])
                )
            params.update({"view_type": "form", "id": provider.id})
        return werkzeug.utils.redirect("/web#" + url_encode(params), 303)
