import csv
import hashlib
import io
import logging
import re
from datetime import datetime

from odoo import models
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)

SWISH_RE = re.compile(r"Swish\s+(\+?\d[\d \-]{6,})", re.I)

# Swedbank CSV header columns
EXPECTED_HEADER = "Radnr,Clnr,Kontonr,Produkt,Valuta,Bokfdag,Transdag,Valutadag,Referens,Text,Belopp,Saldo"


class AccountStatementImport(models.TransientModel):
    _inherit = "account.statement.import"

    def _create_bank_statements(self, stmts_vals, result):
        before = set(result.get("statement_ids") or [])
        res = super()._create_bank_statements(stmts_vals, result)
        new_ids = [i for i in (result.get("statement_ids") or []) if i not in before]
        if new_ids:
            lines = self.env["account.bank.statement"].browse(new_ids).mapped("line_ids")
            done = lines.swedbank_auto_reconcile_swish()
            if done:
                result.setdefault("notifications", []).append(
                    f"{done} Swish-inbetalning(ar) stämdes av automatiskt mot faktura.")
        return res

    def _parse_file(self, data_file):
        try:
            return self._parse_swedbank_csv(data_file)
        except UserError:
            raise
        except Exception:
            return super()._parse_file(data_file)

    def _parse_swedbank_csv(self, data_file):
        # Try CP1252 first (Swedbank default), then UTF-8
        for encoding in ("cp1252", "utf-8", "latin-1"):
            try:
                data = data_file.decode(encoding)
                break
            except (UnicodeDecodeError, AttributeError):
                continue
        else:
            return super()._parse_file(data_file)

        # Swedbank exports have corrupted Swedish chars: UTF-8 replacement
        # character (ef bf bd) decoded as cp1252 becomes "ï¿½" (3 chars).
        # Replace known patterns, then strip remaining.
        MOJIBAKE = "ï¿½"  # ef bf bd decoded as cp1252
        SWEDBANK_CHAR_FIXES = {
            f"F{MOJIBAKE}retagskonto": "Företagskonto",
            f"F{MOJIBAKE}RETAG": "FÖRETAG",
            f"{MOJIBAKE}verf{MOJIBAKE}ring": "Överföring",
            f"l{MOJIBAKE}n": "lön",
            f"L{MOJIBAKE}n": "Lön",
            f"ins{MOJIBAKE}ttning": "insättning",
            f"Ins{MOJIBAKE}ttning": "Insättning",
            f"utm{MOJIBAKE}tning": "utmätning",
            f"r{MOJIBAKE}nta": "ränta",
            f" {MOJIBAKE} ": " – ",
        }
        for bad, good in SWEDBANK_CHAR_FIXES.items():
            data = data.replace(bad, good)
        data = data.replace(MOJIBAKE, "")

        lines = data.replace("\r\n", "\n").replace("\r", "\n").split("\n")

        # Find header row
        header_idx = None
        for i, line in enumerate(lines):
            if line.startswith("Radnr,Clnr,Kontonr"):
                header_idx = i
                break

        if header_idx is None:
            return super()._parse_file(data_file)

        # Parse CSV from header onwards
        csv_data = "\n".join(lines[header_idx:])
        reader = csv.DictReader(io.StringIO(csv_data))

        transactions = []
        currency = None
        account_number = None
        balance_start = None
        balance_end = None

        for row in reader:
            if not row.get("Radnr"):
                continue

            if currency is None:
                currency = row.get("Valuta", "SEK")
            if account_number is None:
                account_number = row.get("Kontonr", "").strip()

            # Parse amount
            amount_str = row.get("Belopp", "0").strip()
            try:
                amount = float(amount_str)
            except ValueError:
                continue

            # Parse date (Bokfdag = bokföringsdag)
            date_str = row.get("Bokfdag", "").strip()
            if not date_str:
                date_str = row.get("Transdag", "").strip()
            if not date_str:
                continue
            try:
                date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            # Build payment reference from Referens + Text
            referens = row.get("Referens", "").strip().strip('"')
            text = row.get("Text", "").strip().strip('"')
            if referens and text:
                payment_ref = f"{referens} — {text}"
            elif referens:
                payment_ref = referens
            elif text:
                payment_ref = text
            else:
                payment_ref = "/"

            # Track balance
            saldo = None
            saldo_str = row.get("Saldo", "").strip()
            if saldo_str:
                try:
                    saldo = float(saldo_str)
                    if balance_end is None:
                        balance_end = saldo
                    balance_start = saldo - amount
                except ValueError:
                    pass

            # Unique import ID to prevent duplicates.
            # Radnr är radens position i JUST DEN HÄR exporten och ändras varje gång kontoutdraget
            # exporteras med ett annat datumintervall – två överlappande exporter gav dubbletter
            # (2026-09-14: 4 sep-raderna kom in igen med nya radnummer). Saldot efter transaktionen
            # är däremot stabilt och unikt per rad på kontot, så bygg id:t på datum + belopp + saldo.
            if saldo is not None:
                unique_id = f"SWED-{account_number}-{date_str}-{amount:.2f}-{saldo:.2f}"
            else:
                digest = hashlib.sha1(f"{referens}|{text}".encode()).hexdigest()[:10]
                unique_id = f"SWED-{account_number}-{date_str}-{amount:.2f}-{digest}"

            transactions.append({
                "date": date,
                "amount": amount,
                "payment_ref": payment_ref,
                "unique_import_id": unique_id,
                "partner_name": referens if referens else None,
                "_saldo": saldo,
            })

        if not transactions:
            return super()._parse_file(data_file)

        # Apply partner mapping rules: for each transaction whose payment_ref
        # contains a label_param of an account.reconcile.model with mapped_partner_id,
        # set partner_id on the line. This ensures auto_reconcile rules pick up
        # the right partner (OCA's _reconcile_data_by_model uses bank line's partner).
        Model = self.env["account.reconcile.model"].sudo()
        partner_rules = Model.search([
            ("match_label", "=", "contains"),
            ("mapped_partner_id", "!=", False),
        ])
        for t in transactions:
            ref_lower = (t.get("payment_ref") or "").lower()
            for rule in partner_rules:
                lbl = (rule.match_label_param or "").lower()
                if lbl and lbl in ref_lower:
                    t["partner_id"] = rule.mapped_partner_id.id
                    break

        # Swish: avsändarens mobilnummer står i texten ("Swish +46763417683"). Slå upp det mot
        # kontakternas phone_sanitized (E.164) och sätt kundens commercial partner så avstämningsvyn föreslår rätt öppna faktura.
        # Flera kontakter med samma nummer är OK om de hör till samma kund; annars lämnas raden.
        Partner = self.env["res.partner"]
        for t in transactions:
            if t.get("partner_id"):
                continue
            mo = SWISH_RE.search(t.get("payment_ref") or "")
            if not mo:
                continue
            digits = re.sub(r"\D", "", mo.group(1))
            e164 = "+46" + digits[1:] if digits.startswith("0") else "+" + digits
            owners = Partner.search([("phone_sanitized", "=", e164)]).mapped("commercial_partner_id")
            if len(owners) == 1:
                t["partner_id"] = owners.id
                logger.info("Swedbank-import: Swish %s → %s", e164, owners.display_name)

        # Swedbank's export is normally sorted newest first, but the order can be flipped in the
        # internet bank. Detect it from the dates instead of assuming, otherwise the balances
        # come out mirrored (seen 2026-07-18: oldest-first file gave balance_start -73 610,15).
        if transactions[0]["date"] >= transactions[-1]["date"]:
            transactions.reverse()

        # Balances from the running balance column: after the newest transaction, and before
        # the oldest one. Fall back to summing when the column is missing.
        saldos = [t.pop("_saldo", None) for t in transactions]
        if saldos[-1] is not None and saldos[0] is not None:
            balance_end = saldos[-1]
            balance_start = saldos[0] - transactions[0]["amount"]
        elif balance_end is not None:
            balance_start = balance_end - sum(t["amount"] for t in transactions)

        stmt_name = f"Swedbank {transactions[0]['date']} - {transactions[-1]['date']}"

        stmt_vals = {
            "name": stmt_name,
            "date": transactions[-1]["date"],
            "transactions": transactions,
        }
        if balance_start is not None:
            stmt_vals["balance_start"] = balance_start
        if balance_end is not None:
            stmt_vals["balance_end_real"] = balance_end

        logger.info(
            "Swedbank CSV: parsed %d transactions, %s to %s",
            len(transactions),
            transactions[0]["date"],
            transactions[-1]["date"],
        )

        return [(currency, account_number, [stmt_vals])]
