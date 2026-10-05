import base64
from datetime import date
from decimal import Decimal

from odoo import Command
from odoo.addons.l10n_se_sie4.lib import sie
from odoo.tests import tagged

from .common import DATA, SieCase


@tagged("post_install", "-at_install", "l10n_se")
class TestSieExport(SieCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.f2024 = sie.parse((DATA / "fortnox_2024.se").read_bytes())
        cls.f2025 = sie.parse((DATA / "fortnox_2025.se").read_bytes())

    def _export(self, company=None, **vals):
        company = company or self.company
        env = self.env(context=dict(self.env.context, allowed_company_ids=company.ids))
        wizard = env["l10n_se.sie.export.wizard"].create({
            "date_from": date(2025, 1, 1), "date_to": date(2025, 12, 31), **vals})
        wizard.action_export()
        self.assertEqual(wizard.state, "done")
        return wizard, base64.b64decode(wizard.file_data)

    def _books(self):
        self._import("fortnox_2024.se", "fortnox_2025.se")

    def test_export_type_4e(self):
        self._books()
        self.company.sudo().write({"company_registry": "559000-0000", "street": "Exempelgatan 1",
                                   "zip": "123 45", "city": "Exempelby"})
        wizard, data = self._export()
        self.assertEqual(wizard.file_name, "Testbolaget_SIE_AB_2025.se")
        self.assertTrue(data.startswith(b"#FLAGGA 0\r\n#PROGRAM \"Odoo\" 19.0\r\n#FORMAT PC8\r\n"))
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""), "CRLF line ends only")
        self.assertIn('#KONTO 3001 "Försäljning inom Sverige, 25 % moms"'.encode("cp437"), data,
                      "code page 437")
        parsed = sie.parse(data)
        self.assertEqual(parsed.encoding, "cp437")
        self.assertFalse(parsed.errors, parsed.issues)
        self.assertEqual(parsed.sie_type, "4")
        self.assertEqual(parsed.orgnr, "559000-0000")
        self.assertEqual(parsed.company_name, "Testbolaget SIE AB")
        self.assertEqual(parsed.address[1:3], ("Exempelgatan 1", "123 45 Exempelby"))
        self.assertEqual(parsed.chart_type, "EUBAS97")
        self.assertEqual(parsed.currency, "SEK")
        self.assertEqual((parsed.year(0).start, parsed.year(0).end),
                         (date(2025, 1, 1), date(2025, 12, 31)))
        self.assertEqual((parsed.year(-1).start, parsed.year(-1).end),
                         (date(2024, 1, 1), date(2024, 12, 31)))
        # the balances are the source files' balances
        self.assertEqual(parsed.amounts("opening"), self.f2025.amounts("opening"))
        self.assertEqual(parsed.amounts("closing"), self.f2025.amounts("closing"))
        self.assertEqual(parsed.amounts("results"), self.f2025.amounts("results"))
        self.assertEqual(parsed.amounts("opening", -1), self.f2024.amounts("opening"))
        self.assertEqual(parsed.amounts("closing", -1), self.f2024.amounts("closing"))
        self.assertEqual(parsed.amounts("results", -1), self.f2024.amounts("results"))
        # chart of accounts with types
        self.assertEqual(parsed.accounts["1930"].type, "T")
        self.assertEqual(parsed.accounts["2440"].type, "S")
        self.assertEqual(parsed.accounts["3001"].type, "I")
        self.assertEqual(parsed.accounts["6570"].type, "K")
        self.assertGreater(len(parsed.accounts), 300, "all active accounts")
        # monthly balances
        psaldo = {(p.period, p.account): p.amount for p in parsed.period_balances
                  if p.year == 0 and not p.objects}
        self.assertEqual(psaldo[("202501", "3001")], Decimal("-20000.00"))
        # vouchers: series = journal code, numbered from 1, balanced
        self.assertEqual(len(parsed.vouchers), 7)
        self.assertEqual({v.series for v in parsed.vouchers}, {"SIE"})
        self.assertEqual([v.number for v in parsed.vouchers], [str(n) for n in range(1, 8)])
        self.assertTrue(all(v.is_balanced for v in parsed.vouchers))
        first = parsed.vouchers[0]
        self.assertTrue(first.text.endswith("Försäljning, Åtta kunder"), first.text)
        sale = [t for t in first.transactions if t.account == "3001"][0]
        self.assertEqual(sale.objects, (("1", "10"), ("6", "P1")))
        self.assertEqual(parsed.dimensions["1"].name, "Kostnadsställe")
        self.assertEqual(parsed.objects[("1", "10")].name, "Butik Östra")
        # file-internal consistency: IB + vouchers = UB, vouchers = RES
        moved = {}
        for v in parsed.vouchers:
            for t in v.transactions:
                moved[t.account] = moved.get(t.account, Decimal(0)) + t.amount
        for code, amount in parsed.amounts("closing").items():
            self.assertEqual(parsed.amounts("opening").get(code, 0) + moved.get(code, 0), amount)
        for code, amount in parsed.amounts("results").items():
            self.assertEqual(moved.get(code, 0), amount)

    def test_round_trip(self):
        """Export from one company, import into a new one: identical balances."""
        self._books()
        _wizard, data = self._export()
        other = self._create_company("Mottagaren AB")
        env = self._as_user_of(other)
        record = self._import(files=[("export.se", data)], env=env)
        self.assertEqual(record.state, "done")
        self.assertEqual(record.deviation_count, 0)
        def split(balances):
            return ({c: a for c, a in balances.items() if c[0] in "12"},
                    {c: a for c, a in balances.items() if c[0] not in "12"})

        mine_balance, _mine_result = split(self._balances(self.company, date(2025, 12, 31)))
        theirs_balance, _theirs_result = split(self._balances(other, date(2025, 12, 31)))
        self.assertEqual(theirs_balance, mine_balance)
        _b, mine_year = split(self._balances(self.company, date(2025, 12, 31), date(2025, 1, 1)))
        _b, theirs_year = split(self._balances(other, date(2025, 12, 31), date(2025, 1, 1)))
        self.assertEqual(theirs_year, mine_year)
        self.assertTrue(mine_balance and mine_year)
        sale = record.move_ids.filtered(lambda m: "Försäljning, Åtta kunder" in m.ref)
        line = sale.line_ids.filtered(lambda ln: ln.account_id.code == "3001")
        self.assertTrue(line.analytic_distribution)

    def test_unclosed_result_goes_to_2099(self):
        self._entry(date(2024, 5, 1), [("1930", 1000.0), ("3740", -1000.0)])
        wizard, data = self._export()
        parsed = sie.parse(data)
        self.assertEqual(parsed.amounts("opening"), {"1930": Decimal("1000.00"),
                                                     "2099": Decimal("-1000.00")})
        self.assertEqual(parsed.amounts("results", -1), {"3740": Decimal("-1000.00")})
        self.assertEqual(sum(parsed.amounts("closing", -1).values())
                         + sum(parsed.amounts("results", -1).values()), 0)
        self.assertIn("not closed", str(wizard.summary_html))

    def test_types(self):
        self._books()
        _w, one = self._export(sie_type="1")
        parsed = sie.parse(one)
        self.assertEqual(parsed.sie_type, "1")
        self.assertFalse(parsed.vouchers or parsed.period_balances)
        self.assertTrue(parsed.closing)
        _w, two = self._export(sie_type="2")
        parsed = sie.parse(two)
        self.assertTrue(parsed.period_balances)
        self.assertEqual(parsed.balances_until, date(2025, 12, 31))
        self.assertFalse(parsed.vouchers)
        _w, three = self._export(sie_type="3")
        parsed = sie.parse(three)
        self.assertFalse(parsed.errors, parsed.issues)
        self.assertIn(("1", "10"), parsed.objects)
        object_psaldo = [p for p in parsed.period_balances if p.objects]
        self.assertIn((("1", "10"),), {p.objects for p in object_psaldo})
        _w, four_i = self._export(sie_type="4I")
        parsed = sie.parse(four_i)
        self.assertFalse(parsed.closing or parsed.opening or parsed.results)
        self.assertEqual(len(parsed.vouchers), 7)
        self.assertTrue(_w.file_name.endswith(".si"))

    def test_checksum(self):
        self._books()
        _w, data = self._export(checksum=True)
        parsed = sie.parse(data)
        self.assertTrue(parsed.checksum_ok)
        self.assertFalse(parsed.issues)

    def test_characters_outside_code_page_437(self):
        self.company.sudo().name = "Bolaget € AB"
        wizard, data = self._export(sie_type="1")
        self.assertIn(b'#FNAMN "Bolaget ? AB"', data)
        self.assertEqual(wizard.file_name, "Bolaget_AB_2025.se")

    def test_journal_filter(self):
        self._books()
        journal = self.env["account.journal"].create({
            "name": "Annan", "code": "ANN", "type": "general", "company_id": self.company.id})
        self._entry(date(2025, 2, 1), [("6570", 10.0), ("1930", -10.0)])
        wizard, data = self._export(journal_ids=[Command.set(journal.ids)])
        self.assertFalse(sie.parse(data).vouchers)
        self.assertIn("do not add up", str(wizard.summary_html))

    def test_split_analytic_distribution(self):
        """A line split 60/40 over two cost centres becomes two #TRANS lines."""
        self._books()
        plan = self.env["account.analytic.plan"].sudo().search([("l10n_se_sie_dimension", "=", 1)])
        store, stock = (self.env["account.analytic.account"].sudo().search(
            [("plan_id", "=", plan.id), ("code", "=", code)]) for code in ("10", "20"))
        move = self._entry(date(2025, 6, 1), [("6570", 100.01), ("1930", -100.01)], post=False)
        move.line_ids.filtered(lambda ln: ln.account_id.code == "6570").analytic_distribution = {
            str(store.id): 60, str(stock.id): 40}
        move.action_post()
        _w, data = self._export()
        parsed = sie.parse(data)
        voucher = [v for v in parsed.vouchers if v.date == date(2025, 6, 1)][0]
        fees = sorted((t.objects, t.amount) for t in voucher.transactions if t.account == "6570")
        self.assertEqual(fees, [((("1", "10"),), Decimal("60.01")),
                                ((("1", "20"),), Decimal("40.00"))])
        self.assertTrue(voucher.is_balanced)
