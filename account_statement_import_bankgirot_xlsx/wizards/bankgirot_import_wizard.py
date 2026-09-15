"""Wizard to import Bankgirot Insättningsuppgifter (XLSX) and enrich
existing bank statement lines with payer details + invoice matches.

The Swedbank CSV import creates one bank line per Bankgiro deposit (a daily
sum). Bankgirot's "Insättningsuppgifter" XLSX has the detailed breakdown
(sender, payment reference, amount per invoice). This wizard correlates the
two so each bank line gets:
  - partner_id set if a single sender / unique partner can be resolved
  - chatter message with the per-invoice breakdown and matched invoice IDs
"""

import base64
import io
import logging
import re

from odoo import _, fields, models
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)


# ── Bankgirot file parser ───────────────────────────────────────────────

def parse_bankgirot_xlsx(file_bytes):
    """Parse a single Bankgirot Insättningsuppgift .xlsx file.
    Returns: {"date": "YYYY-MM-DD", "total": float, "details": [
        {"sender": str, "ref": str, "bg": str, "amount": float}, ...
    ]}
    """
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))

    date = None
    total = None
    receiver_bg = None
    details = []
    in_details = False

    for row in rows:
        if not row:
            continue
        cells = [c if c is not None else "" for c in row]
        # Date row: first cell is an ISO date (raden under "Datum, Bankkontonummer, Bankgironummer, Mottagare")
        if isinstance(cells[0], str) and re.match(r"^\d{4}-\d{2}-\d{2}", cells[0]):
            if date is None:
                date = cells[0][:10]
            if len(cells) > 2 and re.match(r"^\d{3,4}-\d{4}$", str(cells[2]).strip()):
                receiver_bg = str(cells[2]).strip()
        # Total row: "TOTALT" står i kolumn 2 och beloppet i kolumn 1 – som TEXT i svenskt format
        # ("2\xa0000,00") i Bankgirots export via internet, så float() rakt av gav None och
        # "Saknar datum eller totalbelopp" (2026-09-14).
        if any(str(c).strip() == "TOTALT" for c in cells):
            for c in cells:
                val = _to_number(c)
                if val is not None and str(c).strip() != "TOTALT" and not _looks_like_count(c):
                    total = val
                    break
        # Details start after Avsändare header
        if cells[0] == "Avsändare":
            in_details = True
            continue
        if in_details and isinstance(cells[0], str) and cells[0].strip() and not cells[0].startswith("©"):
            sender = cells[0].strip()
            ref = str(cells[2]).strip() if len(cells) > 2 else ""
            bg = str(cells[3]).strip() if len(cells) > 3 else ""
            amount = _to_number(cells[4]) if len(cells) > 4 else None
            message = str(cells[7]).strip() if len(cells) > 7 else ""
            if amount and amount > 0:
                details.append({"sender": sender, "ref": ref, "bg": bg, "amount": amount, "message": message})

    if total is None and details:
        total = round(sum(d["amount"] for d in details), 2)
    return {"date": date, "total": total, "details": details, "receiver_bg": receiver_bg}


def _to_number(value):
    """1234.5 | '1 234,50' | '2\xa0000,00' → float, annars None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    txt = str(value).replace("\xa0", "").replace(" ", "").replace("\u202f", "")
    if "," in txt and "." not in txt:
        txt = txt.replace(",", ".")
    elif "," in txt and "." in txt:
        txt = txt.replace(".", "").replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return None


def _looks_like_count(value):
    """'2' på TOTALT-raden är antalet insättningar, inte beloppet."""
    return isinstance(value, str) and value.strip().isdigit() and len(value.strip()) <= 3


# ── Wizard ───────────────────────────────────────────────────────────────

class BankgirotImportWizard(models.TransientModel):
    _name = "account.bankgirot.import.wizard"
    _description = "Importera Bankgirot insättningsuppgifter"

    file_ids = fields.Many2many(
        "ir.attachment", string="Bankgirot XLSX-filer", required=True,
        help="Välj en eller flera .xlsx-filer från Bankgirots Insättningsuppgifter.",
    )
    result_html = fields.Html(string="Resultat", readonly=True)

    # ── Helpers ─────────────────────────────────────────────────────────

    def _candidates_from_ref(self, ref):
        """Generate candidate invoice numbers from a Bankgirot reference.
        Tries: full digits, strip 1, strip 2 (Luhn-style check), year-prefix variants.
        """
        if not ref:
            return
        digits = re.sub(r"[^0-9]", "", ref)
        if not digits:
            return
        yield digits
        if len(digits) > 1:
            yield digits[:-1]
        if len(digits) > 2:
            yield digits[:-2]
        # Year-prefixed: 2026XXXXY → strip 2026 + check
        if digits.startswith("2026") and len(digits) > 5:
            yield digits[4:-1]
            yield digits[4:-2]
            rest = digits[4:].lstrip("0")
            if rest and len(rest) > 1:
                yield rest[:-1]

    def _build_invoice_index(self):
        """Pre-fetch open out_invoices and SIE-imported sales entries."""
        Move = self.env["account.move"]
        out_invs = Move.search([
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("amount_residual", ">", 0),
        ])
        sie_invs = Move.search([
            ("state", "=", "posted"),
            ("ref", "=ilike", "%Kundfaktura%"),
        ])
        return list(out_invs) + list(sie_invs)

    def _find_invoice(self, ref, amount, all_invs, message=""):
        text = " ".join(t for t in (ref, message) if t)
        # 1) OCR-/betalningsreferens exakt (l10n_se_ocr sätter payment_reference på fakturan)
        for token in re.findall(r"\d{6,}", text):
            for inv in all_invs:
                if (inv.payment_reference or "").strip() == token:
                    return inv
        # 2) kundens namn i referens eller meddelande ("ÅRSAVGIFT 2026 HILDURS VÄG 11")
        def norm(t):
            return re.sub(r"[^a-z0-9åäö]", "", (t or "").lower())
        ntext = norm(text)
        if ntext:
            hits = [inv for inv in all_invs if inv.partner_id and len(norm(inv.partner_id.name)) >= 6
                    and norm(inv.partner_id.name) in ntext]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                same_amount = [inv for inv in hits if abs(inv.amount_residual - amount) < 0.01]
                if len(same_amount) == 1:
                    return same_amount[0]
        cands = list(self._candidates_from_ref(ref))
        for c in cands:
            if not c:
                continue
            for inv in all_invs:
                if inv.name == c or inv.name.endswith("/" + c) or inv.name.endswith("/" + c.zfill(4)):
                    return inv
            for inv in all_invs:
                r = inv.ref or ""
                if re.search(rf",\s*{c}\s*$", r):
                    return inv
        # Fallback: amount-based (only if exactly one match)
        matches = [inv for inv in all_invs if abs(inv.amount_residual - amount) < 0.01]
        if len(matches) == 1:
            return matches[0]
        return None

    def _find_partner_by_bg(self, bg):
        if not bg:
            return None
        bg_clean = bg.replace("-", "").replace(" ", "")
        bank = self.env["res.partner.bank"].search([
            ("sanitized_acc_number", "=", bg_clean),
        ], limit=1)
        return bank.partner_id.id if bank else None

    def _find_statement_line(self, date, total, receiver_bg=None):
        """Find a Swedbank 'Bankgiro inbetalning' line on the given date with matching amount.
        Swedbanks etikett är "<bankgironr utan bindestreck> — Bankgiro inbetalning"; matcha som
        delsträng (inte prefix) och föredra raden med rätt bankgironummer när flera bolag delar databas."""
        domain = [("date", "=", date), ("amount", "=", total), ("payment_ref", "ilike", "Bankgiro inbetalning")]
        lines = self.env["account.bank.statement.line"].search(domain)
        if receiver_bg and len(lines) > 1:
            digits = receiver_bg.replace("-", "")
            lines = lines.filtered(lambda ln: digits in (ln.payment_ref or "")) or lines
        return lines[:1]

    def _auto_reconcile(self, stl, enriched):
        """Stämmer av bankraden mot de matchade fakturorna med account_reconcile_oca:s logik, samma
        väg som avstämningsvyn. Bara när det är entydigt: varje detaljrad har en egen faktura, ingen
        faktura förekommer två gånger, och fakturornas restbelopp summerar till bankraden."""
        invoices = [x["inv"] for x in enriched if x.get("inv")]
        if len(invoices) != len(enriched) or len({i.id for i in invoices}) != len(invoices):
            return " · <i>ej automatiskt: alla rader matchade inte</i>"
        if abs(sum(i.amount_residual for i in invoices) - stl.amount) >= 0.005:
            return " · <i>ej automatiskt: summan avviker</i>"
        if not hasattr(stl, "_add_account_move_line"):
            return " · <i>ej automatiskt: account_reconcile_oca saknas</i>"
        try:
            # Börja från rent bord: avstämningsvyn sparar ett halvfärdigt läge om raden varit öppen,
            # och _add_account_move_line bygger vidare på det som ligger där.
            stl.clean_reconcile()
            for inv in invoices:
                receivable = inv.line_ids.filtered(
                    lambda ln: ln.account_id.account_type == "asset_receivable" and not ln.reconciled)
                if len(receivable) != 1:
                    stl.clean_reconcile()
                    return " · <i>ej automatiskt: fakturarad tvetydig</i>"
                stl._add_account_move_line(receivable)
            if not stl.reconcile_data_info.get("can_reconcile"):
                logger.info("Bankgirot: %s balanserar inte, data=%s", stl.payment_ref,
                            [(d.get("kind"), d.get("amount"), d.get("name")) for d in stl.reconcile_data_info.get("data", [])])
                stl.clean_reconcile()
                return " · <i>ej automatiskt: balanserar inte</i>"
            stl.reconcile_bank_line()
            logger.info("Bankgirot: %s avstämd mot %s", stl.payment_ref, ", ".join(i.name for i in invoices))
            return " · <b>avstämd mot {}</b>".format(", ".join(i.name for i in invoices))
        except Exception:
            logger.exception("Bankgirot: automatisk avstämning misslyckades för %s", stl.payment_ref)
            return " · <i>ej automatiskt: fel, se loggen</i>"

    # ── Main action ─────────────────────────────────────────────────────

    def action_import(self):
        self.ensure_one()
        if not self.file_ids:
            raise UserError(_("Välj minst en Bankgirot-XLSX-fil."))

        all_invs = self._build_invoice_index()
        result_rows = []
        total_matched = 0
        total_details = 0
        total_files = 0
        total_partner_set = 0

        for att in self.file_ids:
            total_files += 1
            try:
                file_bytes = base64.b64decode(att.datas)
                bg_data = parse_bankgirot_xlsx(file_bytes)
            except Exception as e:
                result_rows.append(
                    f"<tr><td>{att.name}</td><td colspan='4' style='color:red'>"
                    f"Kunde inte läsa fil: {e}</td></tr>")
                continue

            if not bg_data.get("date") or not bg_data.get("total"):
                result_rows.append(
                    f"<tr><td>{att.name}</td><td colspan='4' style='color:orange'>"
                    f"Saknar datum eller totalbelopp</td></tr>")
                continue

            stl = self._find_statement_line(bg_data["date"], bg_data["total"], bg_data.get("receiver_bg"))
            if not stl:
                result_rows.append(
                    f"<tr><td>{att.name}</td><td>{bg_data['date']}</td>"
                    f"<td>{bg_data['total']:.2f} kr</td>"
                    f"<td colspan='2' style='color:orange'>Ingen matchande bankrad</td></tr>")
                continue

            # Resolve each detail
            enriched = []
            for x in bg_data["details"]:
                total_details += 1
                inv = self._find_invoice(x["ref"], x["amount"], all_invs, x.get("message", ""))
                pid = inv.partner_id.id if inv and inv.partner_id else None
                if not pid:
                    pid = self._find_partner_by_bg(x["bg"])
                pname = self.env["res.partner"].browse(pid).name if pid else None
                enriched.append({
                    **x, "pid": pid, "pname": pname,
                    "inv_name": inv.name if inv else None, "inv": inv,
                })
                if pid:
                    total_matched += 1

            # Set partner on bank line if all details point to same partner
            distinct_pids = {x["pid"] for x in enriched if x["pid"]}
            if len(distinct_pids) == 1:
                stl.partner_id = list(distinct_pids)[0]
                total_partner_set += 1

            # Remove any previous Bankgirot chatter on this move
            old_msgs = self.env["mail.message"].search([
                ("model", "=", "account.move"),
                ("res_id", "=", stl.move_id.id),
                ("body", "ilike", "%Bankgirot insättningsuppgifter%"),
            ])
            old_msgs.unlink()

            # Post fresh chatter with detail breakdown
            body_parts = ["<p><b>Bankgirot insättningsuppgifter</b></p><ul>"]
            for x in enriched:
                inv_str = f" → <code>{x['inv_name']}</code>" if x.get("inv_name") else ""
                pname_str = x["pname"] or "—"
                body_parts.append(
                    f"<li><b>{x['amount']:.2f} kr</b> — {x['sender']} "
                    f"— ref <code>{x['ref'] or x.get('message', '').strip() or '—'}</code>{inv_str}"
                    f" → match: <i>{pname_str}</i></li>"
                )
            body_parts.append("</ul>")
            self.env["mail.message"].create({
                "model": "account.move",
                "res_id": stl.move_id.id,
                "body": "".join(body_parts),
                "subject": "Bankgirot detaljer",
                "message_type": "comment",
                "author_id": self.env.user.partner_id.id,
            })

            # Automatisk avstämning (journalval): alla detaljer → varsin öppen faktura, summan = bankraden
            auto_status = ""
            if stl.journal_id.bankgirot_auto_reconcile and not stl.is_reconciled:
                auto_status = self._auto_reconcile(stl, enriched)

            matched_count = sum(1 for x in enriched if x["pid"])
            partner_status = ""
            if len(distinct_pids) == 1:
                partner_status = f"<i>partner satt: {self.env['res.partner'].browse(list(distinct_pids)[0]).name}</i>"
            else:
                partner_status = "<i>flera avsändare</i>"
            result_rows.append(
                f"<tr><td>{att.name}</td><td>{bg_data['date']}</td>"
                f"<td>{bg_data['total']:.2f} kr</td>"
                f"<td>{matched_count}/{len(enriched)}</td>"
                f"<td>{partner_status}{auto_status}</td></tr>")

        # Build summary HTML
        html = (
            "<p><b>Bankgirot import klar</b></p>"
            f"<p>Filer: {total_files} | Detaljer: {total_details} | "
            f"Matchade: {total_matched} | Partner satt: {total_partner_set}</p>"
            "<table style='width:100%;border-collapse:collapse'>"
            "<thead><tr style='background:#2C3E50;color:white'>"
            "<th style='padding:6px;text-align:left'>Fil</th>"
            "<th style='padding:6px;text-align:left'>Datum</th>"
            "<th style='padding:6px;text-align:left'>Totalt</th>"
            "<th style='padding:6px;text-align:left'>Matchade</th>"
            "<th style='padding:6px;text-align:left'>Partner</th>"
            "</tr></thead><tbody>" + "".join(result_rows) + "</tbody></table>"
        )
        self.result_html = html

        # Reopen wizard with results displayed
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.bankgirot.import.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }
