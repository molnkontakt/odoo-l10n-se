import contextlib
import csv
import hashlib
import io
import logging
import re
from datetime import datetime, timedelta

from odoo import models
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)

SWISH_RE = re.compile(r"Swish\s+(\+?\d[\d \-]{6,})", re.I)


class AccountStatementImport(models.TransientModel):
    _inherit = "account.statement.import"

    def _create_bank_statements(self, stmts_vals, result):
        look_alikes = self._swedbank_look_alikes(stmts_vals)
        if look_alikes:
            result.setdefault("notifications", []).append(
                f"{look_alikes} rad(er) har samma dag och belopp som rader som en annan kanal (Enable Banking) "
                "lagt in i journalen. De importerades (en tappad betalning märks inte) – kontrollera att det inte "
                "är samma betalningar och ta bort dubbletterna ur den här importen (Enable Banking hämtar sina "
                "rader igen). Importera inte filer för dagar som Enable Banking redan hämtar.")
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

    def _swedbank_look_alikes(self, stmts_vals):
        """How many new Swedbank rows have the day and amount of a line another channel (Enable Banking)
        brought into the journal. The channels give one transaction different ids, so they could be the
        same payment - or two payments. Nothing is left out (a lost payment would not be noticed); the
        user checks. Rows already imported are not counted."""
        Line = self.env["account.bank.statement.line"].sudo()
        count = 0
        for st_vals in stmts_vals:
            days = timedelta(days=self._swedbank_look_alike_days(st_vals.get("journal_id")))
            for t in st_vals["transactions"]:
                uid = t.get("unique_import_id") or ""
                if "SWED-" not in uid or Line.search_count([("unique_import_id", "=", uid)], limit=1):
                    continue
                if Line.search_count([
                    ("journal_id", "=", st_vals.get("journal_id")),
                    ("date", ">=", t["date"] - days), ("date", "<=", t["date"] + days),
                    ("amount", "=", round(t["amount"], 2)), ("unique_import_id", "!=", False),
                    ("unique_import_id", "not like", "%SWED-%"),
                ], limit=1):
                    count += 1
        return count

    def _swedbank_look_alike_days(self, journal_id):
        """The file has the booking day. A provider that dates its lines by value day can be a few days
        off (a weekend); then look that far either way. Enable Banking is not a dependency."""
        journal = self.env["account.journal"].browse(journal_id).exists()
        provider = journal.online_bank_statement_provider_id if "online_bank_statement_provider_id" in journal._fields else None
        return 4 if provider and provider._fields.get("eb_date_type") and provider.eb_date_type == "value_date" else 0

    @staticmethod
    def _swedbank_numbered_ids(transactions):
        """The ids in this (oldest first) order: rows that share one get -2, -3 after the first."""
        occurrences, ids = {}, []
        for t in transactions:
            uid = t["unique_import_id"]
            occurrences[uid] = occurrences.get(uid, 0) + 1
            ids.append(uid if occurrences[uid] == 1 else f"{uid}-{occurrences[uid]}")
        return ids

    def _swedbank_order_from_existing(self, transactions):
        """'newest' or 'oldest' first, from the lines an earlier import stored under the ids rows share:
        the text tells which payment the plain id and each -2, -3 belong to. None when nothing tells."""
        Line = self.env["account.bank.statement.line"].sudo()
        scores = {}
        for order in ("newest", "oldest"):
            rows = transactions[::-1] if order == "newest" else transactions
            scores[order] = 0
            for t, uid in zip(rows, self._swedbank_numbered_ids(rows), strict=True):
                # stored as <account>-<journal>-<id>
                existing = Line.search([("unique_import_id", "=like", f"%-{uid}")], limit=1)
                if existing:
                    scores[order] += 1 if existing.payment_ref == t["payment_ref"] else -1
        if scores["newest"] == scores["oldest"]:
            return None
        return max(scores, key=scores.get)

    @staticmethod
    def _swedbank_order_from_balances(transactions):
        """'newest' or 'oldest' first, from the balance after each row: newest first, a row's balance is
        the next row's plus its own amount; oldest first, the next row's balance is this one's plus the
        next amount. 'both' when every pair of rows cancels out (+100, -100, +100); None when the
        column is missing or neither fits."""
        pairs = [(a, b) for a, b in zip(transactions, transactions[1:], strict=False) if a["_saldo"] is not None and b["_saldo"] is not None]
        if not pairs:
            return None
        newest = all(abs(a["_saldo"] - (b["_saldo"] + a["amount"])) < 0.005 for a, b in pairs)
        oldest = all(abs(b["_saldo"] - (a["_saldo"] + b["amount"])) < 0.005 for a, b in pairs)
        if newest and oldest:
            return "both"
        if not (newest or oldest):
            return None
        return "newest" if newest else "oldest"

    def _parse_file(self, data_file):
        try:
            return self._parse_swedbank_csv(data_file)
        except UserError:
            raise
        except Exception:
            return super()._parse_file(data_file)

    def _parse_swedbank_csv(self, data_file):
        # UTF-8 first (with or without byte order mark): it is strict, so Swedbank's cp1252 export -
        # whose å/ä/ö are not valid UTF-8 - falls through to cp1252 as before, while a UTF-8 file no
        # longer gets its letters garbled by cp1252, which accepts almost any byte.
        for encoding in ("utf-8-sig", "cp1252", "latin-1"):
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
        data = data.replace("\ufffd", MOJIBAKE)  # the same bytes in a file read as UTF-8
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
                with contextlib.suppress(ValueError):
                    saldo = float(saldo_str)

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

        # Swish: avsändarens mobilnummer står i texten ("Swish +46700000000"). Slå upp det mot
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
        # internet bank. Detect it instead of assuming, otherwise the balances come out mirrored
        # (seen 2026-07-18: oldest-first file gave balance_start -73 610,15). The balance column
        # tells it even within one day; the dates only when they differ. When every pair of rows cancels
        # out, the balances fit both orders: which payment gets the id rows share then follows what an
        # earlier import of the day stored under it (else X imported before comes in again as -2 and
        # the new Y is skipped in its place).
        order = self._swedbank_order_from_balances(transactions)
        if order == "both":
            order = self._swedbank_order_from_existing(transactions)
        if order is None:
            order = "newest" if transactions[0]["date"] >= transactions[-1]["date"] else "oldest"
        if order == "newest":
            transactions.reverse()

        # Rows can share an id: +100, -100, +100 on one day gives the first and the third the same
        # amount and balance; without balances, two identical payments on one day. The later one was
        # dropped as already imported. Number them now that the order is oldest first: an export made
        # during the day and a later one differ only at the newest end, so the oldest occurrence keeps
        # the plain id (as imported before) and each later one keeps its -2, -3 in every export.
        for t, uid in zip(transactions, self._swedbank_numbered_ids(transactions), strict=True):
            t["unique_import_id"] = uid

        # Balances from the running balance column: after the newest transaction, and before the
        # oldest one - from the nearest rows that have one, when the first or last row lacks it.
        balance_start = balance_end = None
        saldos = [t.pop("_saldo", None) for t in transactions]
        with_saldo = [i for i, saldo in enumerate(saldos) if saldo is not None]
        if with_saldo:
            first, last = with_saldo[0], with_saldo[-1]
            balance_end = saldos[last] + sum(t["amount"] for t in transactions[last + 1:])
            balance_start = saldos[first] - sum(t["amount"] for t in transactions[: first + 1])

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
