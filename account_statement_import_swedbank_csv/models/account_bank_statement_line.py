import logging
import re

from odoo import models

_logger = logging.getLogger(__name__)
SWISH_RE = re.compile(r"Swish\s+(\+?\d[\d \-]{6,})", re.I)


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    def swedbank_auto_reconcile_swish(self):
        """Stämmer av Swish-inbetalningar mot kundfakturor helt automatiskt när det är entydigt:
        raden har partner (satt av importen via mobilnumret), är oavstämd, positiv, och kunden har
        exakt EN öppen kundfaktura vars restbelopp är lika med inbetalningen. Använder OCA:s
        avstämningslogik (account_reconcile_oca) så resultatet blir detsamma som i vyn.
        Returnerar antalet avstämda rader. Publik så den kan köras i efterhand på gamla rader."""
        done = 0
        for line in self:
            if line.is_reconciled or line.amount <= 0 or not line.partner_id:
                continue
            if not SWISH_RE.search(line.payment_ref or ""):
                continue
            if not line.journal_id.swedbank_swish_auto_reconcile:
                continue
            partner = line.partner_id.commercial_partner_id
            invoices = self.env["account.move"].search([
                ("company_id", "=", line.company_id.id),
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("payment_state", "in", ("not_paid", "partial")),
                ("commercial_partner_id", "=", partner.id),
            ])
            invoices = invoices.filtered(lambda inv, amount=line.amount: abs(inv.amount_residual - amount) < 0.005)
            if len(invoices) != 1:
                _logger.info("Swish-avstämning: %s → %s har %d öppna fakturor på %.2f, lämnas",
                             line.payment_ref, partner.display_name, len(invoices), line.amount)
                continue
            receivable = invoices.line_ids.filtered(
                lambda ln: ln.account_id.account_type == "asset_receivable" and not ln.reconciled)
            if len(receivable) != 1:
                continue
            if not hasattr(line, "_add_account_move_line"):
                _logger.warning("Swish-avstämning kräver account_reconcile_oca – hoppar")
                return done
            try:
                line.clean_reconcile()   # rent bord – vyn kan ha sparat ett halvfärdigt läge
                line._add_account_move_line(receivable)
                if not line.reconcile_data_info.get("can_reconcile"):
                    _logger.info("Swish-avstämning: %s → förslaget balanserar inte, lämnas", line.payment_ref)
                    line.clean_reconcile()
                    continue
                line.reconcile_bank_line()
                done += 1
                _logger.info("Swish-avstämning: %s → %s (%s) avstämd", line.payment_ref, invoices.name, partner.display_name)
            except Exception:
                _logger.exception("Swish-avstämning misslyckades för %s", line.payment_ref)
        return done
