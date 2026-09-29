"""Swedbank's CSV statement export, parsed through the OCA import wizard's ``_parse_file``.

All account numbers, names and references are invented. The phone numbers are from the range PTS
reserves for fiction (070-174 06 05 to 070-174 06 99).
"""

import base64
from datetime import date

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

ACCOUNT = "1234567890"
HEADER = "Radnr,Clnr,Kontonr,Produkt,Valuta,Bokfdag,Transdag,Valutadag,Referens,Text,Belopp,Saldo"


def _row(radnr, day, ref, text, amount, balance):
    return f'{radnr},8000-0,{ACCOUNT},"Företagskonto",SEK,{day},{day},{day},"{ref}","{text}",{amount},{balance}'


def _csv(rows):
    """The export as Swedbank writes it: cp1252, CRLF, a title line, the column header, then one
    row per transaction. ``rows``: (booking day, reference, text, amount, balance after) as the
    file spells them, or a complete line."""
    lines = ["* Transaktioner Period 2026-09-01 – 2026-09-15 Skapad 2026-09-15 08:00 CEST", HEADER]
    for radnr, row in enumerate(rows, 1):
        lines.append(row if isinstance(row, str) else _row(radnr, *row))
    return ("\r\n".join(lines) + "\r\n").encode("cp1252")


# Swedbank's default order: newest first. Opening balance 10 000,00.
ROWS = [
    ("2026-09-15", "55555555", "Bankgiro inbetalning", "2000.00", "11500.00"),
    ("2026-09-10", "", "Insättning", "250.00", "9500.00"),
    ("2026-09-02", "Exempel Elbolaget, avi 7", "Bg-bet. via internet", "-750.00", "9250.00"),
]
STATEMENT = {
    "name": "Swedbank 2026-09-02 - 2026-09-15",
    "date": date(2026, 9, 15),
    "balance_start": 10000.0,
    "balance_end_real": 11500.0,
    "transactions": [
        {
            "date": date(2026, 9, 2),
            "amount": -750.0,
            "payment_ref": "Exempel Elbolaget, avi 7 — Bg-bet. via internet",
            "unique_import_id": "SWED-1234567890-2026-09-02--750.00-9250.00",
            "partner_name": "Exempel Elbolaget, avi 7",
        },
        {
            "date": date(2026, 9, 10),
            "amount": 250.0,
            "payment_ref": "Insättning",
            "unique_import_id": "SWED-1234567890-2026-09-10-250.00-9500.00",
            "partner_name": None,
        },
        {
            "date": date(2026, 9, 15),
            "amount": 2000.0,
            "payment_ref": "55555555 — Bankgiro inbetalning",
            "unique_import_id": "SWED-1234567890-2026-09-15-2000.00-11500.00",
            "partner_name": "55555555",
        },
    ],
}


@tagged("post_install", "-at_install")
class TestSwedbankCsv(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Reconciliation models that set a partner from the label would add partner_id to lines
        # in a database that has some; the tests below create their own.
        cls.env["account.reconcile.model"].search([("mapped_partner_id", "!=", False)]).action_archive()
        cls.se = cls.env.ref("base.se")

    def _parse(self, data):
        wizard = self.env["account.statement.import"].create(
            {"statement_file": base64.b64encode(data), "statement_filename": "swedbank.csv"}
        )
        # as the OCA wizard's import_single_file calls it
        return wizard.with_context(active_id=wizard.id)._parse_file(data)

    def _statement(self, data):
        ((currency, account_number, statements),) = self._parse(data)
        self.assertEqual((currency, account_number), ("SEK", ACCOUNT))
        (statement,) = statements
        return statement

    def _complete(self, data, journal):
        """Parsed and completed as the OCA wizard does before creating statements (journal, ids)."""
        wizard = self.env["account.statement.import"].create(
            {"statement_file": base64.b64encode(data), "statement_filename": "swedbank.csv"}
        )
        ((_currency, account_number, statements),) = wizard.with_context(active_id=wizard.id)._parse_file(data)
        return wizard, wizard._complete_stmts_vals(statements, journal, account_number)

    def _eb_line(self, journal, day, amount, text="Bankgiro inbetalning", n=0):
        """A line Enable Banking brought into the journal (an id without SWED-)."""
        stmt = self.env["account.bank.statement"].create({"journal_id": journal.id, "name": "Enable Banking"})
        return self.env["account.bank.statement.line"].create({
            "journal_id": journal.id, "statement_id": stmt.id, "date": day, "amount": amount,
            "payment_ref": text, "unique_import_id": f"SE000000-{journal.id}-00E34AB1D-{day}-{n}",
        })

    def test_look_alikes_of_enable_banking_lines_are_imported_and_counted(self):
        """A row with the day and amount of a line Enable Banking brought in could be the same payment or
        another: nothing is left out, the rows are counted for the notification; a journal without such a
        line has none."""
        journal = self.env["account.journal"].create({"name": "Swedbank test", "type": "bank", "code": "SWT1"})
        self._eb_line(journal, "2026-09-15", 2000.0)
        wizard, stmts = self._complete(_csv(ROWS), journal)
        self.assertEqual(wizard._swedbank_look_alikes(stmts), 1)
        self.assertEqual(len(stmts[0]["transactions"]), 3, "nothing left out")
        self.assertEqual((stmts[0]["balance_start"], stmts[0]["balance_end_real"]), (10000.0, 11500.0))
        other = self.env["account.journal"].create({"name": "Swedbank test 2", "type": "bank", "code": "SWT2"})
        wizard, stmts = self._complete(_csv(ROWS), other)
        self.assertEqual(wizard._swedbank_look_alikes(stmts), 0)

    def test_look_alikes_are_imported_with_a_notification(self):
        journal = self.env["account.journal"].create({"name": "Swedbank test", "type": "bank", "code": "SWT1"})
        self._eb_line(journal, "2026-09-15", 2000.0)
        self._eb_line(journal, "2026-09-10", -250.0, n=1)  # the other sign
        self._eb_line(journal, "2026-09-11", 250.0, n=2)  # another day
        wizard, stmts = self._complete(_csv(ROWS), journal)
        result = {"statement_ids": [], "notifications": []}
        wizard._create_bank_statements(stmts, result)
        self.assertEqual(len(self.env["account.bank.statement"].browse(result["statement_ids"]).line_ids), 3)
        (note,) = [n for n in result["notifications"] if "Enable Banking" in n]
        self.assertTrue(note.startswith("1 rad(er) har samma dag och belopp"), note)
        wizard, stmts = self._complete(_csv(ROWS), journal)
        self.assertEqual(wizard._swedbank_look_alikes(stmts), 0, "rows already imported are not counted again")

    def test_both_orders_fit_the_id_follows_the_earlier_import(self):
        """+100 (A), -100, +100 (B) on one day fits the balances in either order. X came in with an export
        made during the day; a later export sorted oldest first must give X its id again and bring in
        B - not X again as -2, with B skipped as already imported."""
        journal = self.env["account.journal"].create({"name": "Swedbank test", "type": "bank", "code": "SWT1"})
        x = ("2026-09-15", "Swish", "Kund A", "100.00", "1100.00")
        z = ("2026-09-15", "Swish", "Kund A", "-100.00", "1000.00")
        y = ("2026-09-15", "Swish", "Kund B", "100.00", "1100.00")
        for rows in ([x], [x, z, y]):
            wizard, stmts = self._complete(_csv(rows), journal)
            wizard._create_bank_statements(stmts, {"statement_ids": [], "notifications": []})
        lines = self.env["account.bank.statement.line"].search([("journal_id", "=", journal.id)])
        self.assertEqual(
            sorted((line.amount, line.payment_ref) for line in lines),
            [(-100.0, "Swish — Kund A"), (100.0, "Swish — Kund A"), (100.0, "Swish — Kund B")],
        )

    def test_one_day_oldest_first_export(self):
        """All rows on one day, oldest first: the balance column tells the order (the dates cannot)."""
        rows = [("2026-09-15", "A", "Insättning", "100.00", "1100.00"), ("2026-09-15", "B", "Insättning", "200.00", "1300.00")]
        statement = self._statement(_csv(rows))
        self.assertEqual([t["amount"] for t in statement["transactions"]], [100.0, 200.0])
        self.assertEqual(statement["balance_start"], 1000.0)
        self.assertEqual(statement["balance_end_real"], 1300.0)

    def test_statement_newest_first(self):
        self.assertEqual(self._parse(_csv(ROWS)), [("SEK", ACCOUNT, [STATEMENT])])

    def test_oldest_first_export(self):
        """The internet bank can sort the export oldest first; the order is taken from the dates,
        so the statement and its balances come out the same (2026-07-18: mirrored balances)."""
        self.assertEqual(self._statement(_csv(list(reversed(ROWS)))), STATEMENT)

    def test_one_day_export(self):
        """All rows on one day: read as Swedbank's default order, newest first."""
        statement = self._statement(
            _csv(
                [
                    ("2026-09-15", "55555555", "Bankgiro inbetalning", "2000.00", "11500.00"),
                    ("2026-09-15", "Exempel Elbolaget", "Bg-bet. via internet", "-750.00", "9500.00"),
                ]
            )
        )
        self.assertEqual(statement["name"], "Swedbank 2026-09-15 - 2026-09-15")
        self.assertEqual([t["amount"] for t in statement["transactions"]], [-750.0, 2000.0])
        self.assertEqual(statement["balance_start"], 10250.0)
        self.assertEqual(statement["balance_end_real"], 11500.0)

    def test_unique_import_id_format(self):
        """unique_import_id is how an import recognises a line it already has. Changing its format
        imports every line of an overlapping export again, as duplicates of the lines imported
        before the change. The expected strings are spelled out on purpose; do not derive them.

        With a balance: SWED-<Kontonr>-<day>-<amount>-<balance after>. Without one: the first 10
        hex digits of sha1("<Referens>|<Text>") instead of the balance. The day is Bokfdag, or
        Transdag when Bokfdag is empty. Radnr is not part of it: it changes with the period
        exported (2026-09-14: overlapping exports gave duplicates)."""
        data = _csv(
            [
                ("2026-09-15", "55555555", "Bankgiro inbetalning", "2000.00", "11500.00"),
                ("2026-09-02", "Exempel Elbolaget", "Bg-bet. via internet", "-750.00", "9500.00"),
                ("2026-09-01", "55555555", "Bankgiro inbetalning", "2000.00", ""),
                f'4,8000-0,{ACCOUNT},"Företagskonto",SEK,,2026-08-31,2026-08-31,"","Insättning",250.00,',
            ]
        )
        self.assertEqual(
            [t["unique_import_id"] for t in self._statement(data)["transactions"]],
            [
                "SWED-1234567890-2026-08-31-250.00-4efa6a30cd",
                "SWED-1234567890-2026-09-01-2000.00-3b2f2f6300",
                "SWED-1234567890-2026-09-02--750.00-9500.00",
                "SWED-1234567890-2026-09-15-2000.00-11500.00",
            ],
        )

    def test_rows_that_share_an_id_are_all_kept(self):
        """+100, -100, +100 on one day: the first and the third have the same amount and balance after.
        Without balances, two identical payments on one day are the same row. The later one used to
        be dropped as already imported; the first keeps its plain id, the next ones get -2, -3."""
        data = _csv(
            [
                ("2026-09-15", "Swish", "Exempel", "100.00", "1100.00"),
                ("2026-09-15", "Swish", "Exempel", "-100.00", "1000.00"),
                ("2026-09-15", "Swish", "Exempel", "100.00", "1100.00"),
            ]
        )
        ids = [t["unique_import_id"] for t in self._statement(data)["transactions"]]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)
        self.assertIn("SWED-1234567890-2026-09-15-100.00-1100.00", ids, "the first keeps the plain id")
        self.assertIn("SWED-1234567890-2026-09-15-100.00-1100.00-2", ids)
        same = _csv([("2026-09-15", "Swish", "Exempel", "50.00", ""), ("2026-09-15", "Swish", "Exempel", "50.00", "")])
        ids = [t["unique_import_id"] for t in self._statement(same)["transactions"]]
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(ids[0] + "-2" in ids or ids[1] + "-2" in ids)

    def test_shared_ids_are_stable_across_exports(self):
        """An export made during the day has X; a later one (newest first, Swedbank's default) has Y, Z,
        X. X must keep the id it was imported with and Y - not X again - gets -2."""
        x = ("2026-09-15", "Swish", "Kund A", "100.00", "1100.00")
        z = ("2026-09-15", "Swish", "Kund A", "-100.00", "1000.00")
        y = ("2026-09-15", "Swish", "Kund B", "100.00", "1100.00")
        (first,) = self._statement(_csv([x]))["transactions"]
        later = {t["unique_import_id"]: t["payment_ref"] for t in self._statement(_csv([y, z, x]))["transactions"]}
        self.assertEqual(later[first["unique_import_id"]], first["payment_ref"], "X keeps its id")
        self.assertIn("Kund B", later[first["unique_import_id"] + "-2"], "the new row is the -2")

    def test_utf8_export_with_swedbanks_broken_letters(self):
        """Swedbank's real export can be ASCII plus EF BF BD only - valid UTF-8. The broken letters are
        restored exactly as in a cp1252 file, and the id of a row without balance does not change."""
        u = "\ufffd"
        rows = [
            ("2026-09-15", f"L{u}n september", f"{u}verf{u}ring", "-500.00", "1500.00"),
            ("2026-09-14", f"Ins{u}ttning", "Kaffe", "300.00", ""),
        ]
        cp1252 = _csv([(d, r.replace(u, "ï¿½"), t.replace(u, "ï¿½"), a, b) for d, r, t, a, b in rows])
        text = cp1252.decode("cp1252").replace("ï¿½", u).replace("Företagskonto", f"F{u}retagskonto")
        utf8 = text.encode("utf-8")
        self.assertNotIn(b"\xf6", utf8)
        utf8.decode("utf-8")  # valid UTF-8: the UTF-8 branch is taken

        def refs(data):
            return [(t["payment_ref"], t["unique_import_id"]) for t in self._statement(data)["transactions"]]

        self.assertEqual(refs(utf8), refs(cp1252))

    def test_utf8_export(self):
        """A UTF-8 file (with or without byte order mark) keeps its letters; cp1252 used to garble them."""
        text = _csv(ROWS).decode("cp1252")
        for data in (text.encode("utf-8"), b"\xef\xbb\xbf" + text.encode("utf-8")):
            with self.subTest(bom=data.startswith(b"\xef")):
                self.assertEqual(self._parse(data), [("SEK", ACCOUNT, [STATEMENT])])

    def test_without_balances(self):
        """Without the Saldo column filled in, the statement has no balances; a row with neither
        reference nor text gets "/" as its label."""
        statement = self._statement(
            _csv(
                [
                    ("2026-09-15", "55555555", "Bankgiro inbetalning", "2000.00", ""),
                    ("2026-09-14", "", "", "-10.00", ""),
                ]
            )
        )
        self.assertNotIn("balance_start", statement)
        self.assertNotIn("balance_end_real", statement)
        first = statement["transactions"][0]
        self.assertEqual((first["payment_ref"], first["partner_name"]), ("/", None))

    def test_swedish_characters(self):
        """cp1252 letters come through as they are. Swedbank writes some letters as the UTF-8
        replacement character (bytes EF BF BD, read as cp1252: "ï¿½"): restored in the words the
        parser knows, an en dash between spaces, and dropped elsewhere."""
        bad = "ï¿½"
        data = _csv(
            [
                ("2026-09-15", f"L{bad}n september", f"{bad}verf{bad}ring", "-500.00", "1500.00"),
                ("2026-09-14", f"Ins{bad}ttning", f"Kaffe {bad} Exempel", "300.00", "2000.00"),
                ("2026-09-13", f"Bj{bad}rkvik", "Ränta", "0.50", "1700.00"),
            ]
        )
        self.assertIn(b"\xef\xbf\xbd", data)
        self.assertEqual(
            [t["payment_ref"] for t in self._statement(data)["transactions"]],
            ["Bjrkvik — Ränta", "Insättning — Kaffe – Exempel", "Lön september — Överföring"],
        )

    def test_rows_that_are_not_transactions(self):
        """A row without Radnr, or without either date, is not a transaction."""
        statement = self._statement(
            _csv(
                [
                    ("2026-09-15", "55555555", "Bankgiro inbetalning", "2000.00", "11500.00"),
                    f',8000-0,{ACCOUNT},"Företagskonto",SEK,2026-09-14,2026-09-14,2026-09-14,"","Utan radnummer",1.00,9500.00',
                    f'3,8000-0,{ACCOUNT},"Företagskonto",SEK,,,,"","Utan datum",5.00,9500.00',
                ]
            )
        )
        self.assertEqual([t["payment_ref"] for t in statement["transactions"]], ["55555555 — Bankgiro inbetalning"])
        self.assertEqual((statement["balance_start"], statement["balance_end_real"]), (9500.0, 11500.0))

    def test_swish_payer_by_phone(self):
        """"Swish <number>" in the text: the customer whose contacts have that number (E.164) is
        the line's partner, also when two of its contacts have it. A number two customers share,
        or nobody has, sets no partner."""
        Partner = self.env["res.partner"]
        customer = Partner.create({"name": "Swishkund Exempel AB", "is_company": True, "country_id": self.se.id})
        for name in ("Kontakt Ett", "Kontakt Två"):
            Partner.create({"name": name, "parent_id": customer.id, "phone": "+46701740612", "country_id": self.se.id})
        for name in ("Delad Ett", "Delad Två"):
            Partner.create({"name": name, "phone": "+46701740613", "country_id": self.se.id})
        transactions = self._statement(
            _csv(
                [
                    ("2026-09-15", "12345674", "Swish +46701740612", "250.00", "10750.00"),
                    ("2026-09-14", "", "Swish 070-174 06 13", "100.00", "10500.00"),
                    ("2026-09-13", "", "Swish 0701740699", "400.00", "10400.00"),
                ]
            )
        )["transactions"]
        unknown, shared, known = transactions
        self.assertEqual(known["payment_ref"], "12345674 — Swish +46701740612")
        self.assertEqual(known["partner_id"], customer.id)
        self.assertNotIn("partner_id", shared)
        self.assertNotIn("partner_id", unknown)

    def test_reconcile_model_sets_partner(self):
        """A reconciliation model matching the label ("contains", any case) that sets a partner (one
        line with a partner and no account: mapped_partner_id) puts that partner on the line, before
        and instead of the Swish lookup."""
        Partner = self.env["res.partner"]
        supplier = Partner.create({"name": "Exempel Elbolaget AB", "is_company": True})
        Partner.create({"name": "Swishkund Exempel", "phone": "+46701740614", "country_id": self.se.id})
        rule = self.env["account.reconcile.model"].create(
            {
                "name": "Elbolaget",
                "match_label": "contains",
                "match_label_param": "ELBOLAGET",
                "line_ids": [Command.create({"partner_id": supplier.id})],
            }
        )
        self.assertEqual(rule.mapped_partner_id, supplier)
        transactions = self._statement(
            _csv(
                [
                    ("2026-09-15", "Exempel Elbolaget", "Swish +46701740614", "100.00", "10100.00"),
                    ("2026-09-14", "Exempel Elbolaget", "Bg-bet. via internet", "-750.00", "10000.00"),
                    ("2026-09-13", "Annan mottagare", "Bg-bet. via internet", "-50.00", "10750.00"),
                ]
            )
        )["transactions"]
        other, bill, swish = transactions
        self.assertNotIn("partner_id", other)
        self.assertEqual(bill["partner_id"], supplier.id)
        self.assertEqual(swish["partner_id"], supplier.id)

    def test_other_files_are_left_to_the_next_parser(self):
        """Not Swedbank's layout, or the header without rows: the file goes on down the parser
        chain, which ends in the OCA base's "format not supported"."""
        for data in (
            b"Datum;Text;Belopp\r\n2026-09-15;Exempel;100,00\r\n",
            (HEADER + "\r\n").encode("cp1252"),
        ):
            with self.assertRaises(UserError):
                self._parse(data)
