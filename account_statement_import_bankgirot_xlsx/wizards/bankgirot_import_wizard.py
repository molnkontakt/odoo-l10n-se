"""Wizard to import deposit details — Bankgirot Insättningsuppgifter (XLSX) or the bank's
ISO 20022 camt.054 notification — and enrich existing bank statement lines with payer
details + invoice matches.

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

from lxml import etree

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


# ── camt.054 (ISO 20022 Bank-to-Customer Debit/Credit Notification) ──────

def _ln(el):
    return etree.QName(el).localname


def _child(el, *path):
    """Namnrymdsoberoende sökväg: _child(tx, "RltdPties", "Dbtr", "Nm")."""
    for name in path:
        if el is None:
            return None
        el = next((c for c in el if isinstance(c.tag, str) and _ln(c) == name), None)
    return el


def _children(el, name):
    return [c for c in (el if el is not None else []) if isinstance(c.tag, str) and _ln(c) == name]


def _text(el, *path):
    found = _child(el, *path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _bgnr(acct):
    """Bankgironummer ur ett <...Acct>-block med SchmeNm/Prtry = BGNR, formaterat 123-4567/1234-5678."""
    other = _child(acct, "Id", "Othr")
    if other is None or _text(other, "SchmeNm", "Prtry") != "BGNR":
        return ""
    digits = re.sub(r"\D", "", _text(other, "Id"))
    return f"{digits[:-4]}-{digits[-4:]}" if len(digits) >= 7 else digits


def parse_camt054(file_bytes):
    """Tolkar en camt.054 (.001.02 eller senare) till samma struktur som XLSX-tolken, en post per
    kreditnotering (Ntry) med detaljer per betalare (TxDtls). En TxDtls med flera strukturerade
    referenser (CINV/SCOR med eget belopp) blir en detaljrad per referens."""
    root = etree.fromstring(file_bytes)
    result = []
    for ntfctn in root.iter("{*}Ntfctn"):
        for ntry in _children(ntfctn, "Ntry"):
            if _text(ntry, "CdtDbtInd") != "CRDT" or _text(ntry, "Sts") not in ("BOOK", ""):
                continue
            date = _text(ntry, "BookgDt", "Dt") or _text(ntry, "BookgDt", "DtTm")[:10]
            amount = _to_number(_text(ntry, "Amt"))
            receiver_bg = ""
            details = []
            for tx in ntry.iter("{*}TxDtls"):
                receiver_bg = receiver_bg or _bgnr(_child(tx, "RltdPties", "CdtrAcct"))
                sender = _text(tx, "RltdPties", "Dbtr", "Nm") or _text(tx, "RltdPties", "Dbtr", "Pty", "Nm")
                sender_bg = _bgnr(_child(tx, "RltdPties", "DbtrAcct"))
                tx_amount = _to_number(_text(tx, "AmtDtls", "TxAmt", "Amt")) or _to_number(_text(tx, "Amt"))
                rmt = _child(tx, "RmtInf")
                message = " ".join(u.text.strip() for u in _children(rmt, "Ustrd") if u.text)
                refs = []
                for strd in _children(rmt, "Strd"):
                    ref = _text(strd, "CdtrRefInf", "Ref") or _text(strd, "RfrdDocInf", "Nb")
                    ref_amount = _to_number(_text(strd, "RfrdDocAmt", "RmtdAmt"))
                    if ref:
                        refs.append((ref, ref_amount))
                split = len(refs) > 1 and all(a for _r, a in refs) and \
                    abs(sum(a for _r, a in refs) - (tx_amount or 0)) < 0.01
                if split:
                    for ref, ref_amount in refs:
                        details.append({"sender": sender, "ref": ref, "bg": sender_bg,
                                        "amount": ref_amount, "message": message})
                elif tx_amount:
                    details.append({"sender": sender, "ref": " ".join(r for r, _a in refs), "bg": sender_bg,
                                    "amount": tx_amount, "message": message})
            if not receiver_bg:
                m = re.match(r"BG\s*(\d{7,8})", _text(ntry, "NtryRef"))
                if m:
                    receiver_bg = f"{m.group(1)[:-4]}-{m.group(1)[-4:]}"
            if date and amount:
                result.append({"date": date, "total": amount, "details": details,
                               "receiver_bg": receiver_bg or None})
    return result


def parse_deposit_file(file_bytes):
    """XLSX (Bankgirot) eller camt.054 (XML) → lista av insättningar."""
    head = file_bytes[:512].lstrip()
    if head.startswith(b"<") or b"camt.054" in head:
        return parse_camt054(file_bytes)
    return [parse_bankgirot_xlsx(file_bytes)]


# ── Wizard ───────────────────────────────────────────────────────────────

class BankgirotImportWizard(models.TransientModel):
    _name = "account.bankgirot.import.wizard"
    _description = "Importera Bankgirot insättningsuppgifter"

    file_ids = fields.Many2many(
        "ir.attachment", string="Filer (XLSX eller camt.054)", required=True,
        help="Bankgirots Insättningsuppgifter (.xlsx) eller bankens camt.054-avisering (.xml).",
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
        yield digits, None
        if len(digits) > 1:
            yield digits[:-1], None
        if len(digits) > 2:
            yield digits[:-2], None
        # Year-prefixed: 2026XXXXY → strip the year + check digit; only invoices from that year
        # (2026-09-15: "INV/2026/0011" → "001" → "/0001" träffade MISC/2024/0001)
        if re.match(r"20\d\d", digits) and len(digits) > 5:
            yield digits[4:-1], digits[:4]
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
        # 3) fakturanumret skrivet som det står på fakturan ("INV/2026/0011", "1010")
        utext = text.upper()
        for inv in all_invs:
            if inv.name and len(inv.name) >= 4 and re.search(r"(?<![\w/])" + re.escape(inv.name.upper()) + r"(?![\w/])", utext):
                return inv
        # 4) siffervarianter av referensen (OCR utan kontrollsiffra, årsprefix …)
        for c, year in self._candidates_from_ref(ref):
            if not c:
                continue
            for inv in all_invs:
                if year and year not in inv.name:
                    continue
                if inv.name == c or inv.name.endswith("/" + c) or (len(c) >= 3 and inv.name.endswith("/" + c.zfill(4))):
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
        Line = self.env["account.bank.statement.line"]
        domain = [("date", "=", date), ("amount", "=", total), ("payment_ref", "ilike", "Bankgiro inbetalning")]
        lines = Line.search(domain)
        if not lines:
            # Andra banker (t.ex. SEB via Enable Banking) märker inte raden "Bankgiro inbetalning":
            # samma dag och belopp, ännu oavstämd
            lines = Line.search([("date", "=", date), ("amount", "=", total), ("is_reconciled", "=", False)])
        if receiver_bg and len(lines) > 1:
            digits = receiver_bg.replace("-", "")
            lines = lines.filtered(lambda ln: digits in (ln.payment_ref or "")) or lines
        return lines[:1]

    def _propose_reconcile(self, stl, enriched, auto):
        """Lägger de matchade fakturorna som förslag i avstämningsvyn (account_reconcile_oca:s
        reconcile_data_info), så att bankraden bara behöver bekräftas. Med ``auto`` (journalvalet)
        bekräftas den direkt när det är entydigt: varje detaljrad har en egen faktura, ingen faktura
        förekommer två gånger, och restbeloppen summerar till bankraden."""
        if not hasattr(stl, "_add_account_move_line"):
            return " · <i>inget förslag: account_reconcile_oca saknas</i>"
        invoices, seen = [], set()
        for x in enriched:
            inv = x.get("inv")
            if inv and inv.id not in seen and inv.amount_residual > 0:
                invoices.append(inv)
                seen.add(inv.id)
        if not invoices:
            return " · <i>inget förslag: ingen faktura matchade</i>"
        complete = len(invoices) == len(enriched)
        try:
            # Börja från rent bord: avstämningsvyn sparar ett halvfärdigt läge om raden varit öppen,
            # och _add_account_move_line bygger vidare på det som ligger där.
            stl.clean_reconcile()
            for inv in invoices:
                receivable = inv.line_ids.filtered(
                    lambda ln: ln.account_id.account_type == "asset_receivable" and not ln.reconciled)
                if len(receivable) != 1:
                    stl.clean_reconcile()
                    return f" · <i>inget förslag: fakturarad tvetydig ({inv.name})</i>"
                stl._add_account_move_line(receivable)
            names = ", ".join(i.name for i in invoices)
            balanced = bool(stl.reconcile_data_info.get("can_reconcile"))
            if auto and complete and balanced:
                stl.reconcile_bank_line()
                logger.info("Bankgirot: %s avstämd mot %s", stl.payment_ref, names)
                return f" · <b>avstämd mot {names}</b>"
            why = "" if balanced else ", summan avviker" if complete else ", alla rader matchade inte"
            return f" · <i>föreslagen i avstämningsvyn: {names}{why}</i>"
        except Exception:
            logger.exception("Bankgirot: avstämningsförslag misslyckades för %s", stl.payment_ref)
            stl.clean_reconcile()
            return " · <i>inget förslag: fel, se loggen</i>"

    def _import_deposit(self, att, bg_data, all_invs, counters):
        """En insättning (datum, total, detaljer) → berikad bankrad. Returnerar en resultatrad (HTML)."""
        if not bg_data.get("date") or not bg_data.get("total"):
            return (
                f"<tr><td>{att.name}</td><td colspan='4' style='color:orange'>"
                f"Saknar datum eller totalbelopp</td></tr>")

        stl = self._find_statement_line(bg_data["date"], bg_data["total"], bg_data.get("receiver_bg"))
        if not stl:
            return (
                f"<tr><td>{att.name}</td><td>{bg_data['date']}</td>"
                f"<td>{bg_data['total']:.2f} kr</td>"
                f"<td colspan='2' style='color:orange'>Ingen matchande bankrad</td></tr>")

        # Resolve each detail
        enriched = []
        for x in bg_data["details"]:
            counters["details"] += 1
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
                counters["matched"] += 1

        # Set partner on bank line if all details point to same partner
        distinct_pids = {x["pid"] for x in enriched if x["pid"]}
        if len(distinct_pids) == 1:
            stl.partner_id = list(distinct_pids)[0]
            counters["partner_set"] += 1

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
        if not stl.is_reconciled:
            auto_status = self._propose_reconcile(stl, enriched, stl.journal_id.bankgirot_auto_reconcile)

        matched_count = sum(1 for x in enriched if x["pid"])
        partner_status = ""
        if len(distinct_pids) == 1:
            partner_status = f"<i>partner satt: {self.env['res.partner'].browse(list(distinct_pids)[0]).name}</i>"
        else:
            partner_status = "<i>flera avsändare</i>"
        return (
            f"<tr><td>{att.name}</td><td>{bg_data['date']}</td>"
            f"<td>{bg_data['total']:.2f} kr</td>"
            f"<td>{matched_count}/{len(enriched)}</td>"
            f"<td>{partner_status}{auto_status}</td></tr>")

    # ── Main action ─────────────────────────────────────────────────────

    def action_import(self):
        self.ensure_one()
        if not self.file_ids:
            raise UserError(_("Välj minst en fil (Bankgirot-XLSX eller camt.054)."))

        all_invs = self._build_invoice_index()
        result_rows = []
        counters = {"details": 0, "matched": 0, "partner_set": 0}
        total_files = 0

        for att in self.file_ids:
            total_files += 1
            try:
                deposits = parse_deposit_file(base64.b64decode(att.datas))
            except Exception as e:
                result_rows.append(
                    f"<tr><td>{att.name}</td><td colspan='4' style='color:red'>"
                    f"Kunde inte läsa fil: {e}</td></tr>")
                continue
            if not deposits:
                result_rows.append(
                    f"<tr><td>{att.name}</td><td colspan='4' style='color:orange'>"
                    f"Inga insättningar i filen</td></tr>")
            for bg_data in deposits:
                result_rows.append(self._import_deposit(att, bg_data, all_invs, counters))

        total_details, total_matched, total_partner_set = (
            counters["details"], counters["matched"], counters["partner_set"])

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
