"""The deposit-detail parsers: Bankgirot's "Insättningsuppgifter Detaljer" (XLSX) and the bank's
camt.054 notification (XML).

All names, account numbers, Bankgiro numbers and references are invented. The parsers do not check
Bankgiro check digits; the OCR references carry a modulus 10 check digit only to look real.
"""

import datetime
import io

import openpyxl
from lxml import etree

from odoo import Command
from odoo.tests import TransactionCase, tagged

from ..wizards.bankgirot_import_wizard import (
    _bgnr,
    _cell_text,
    _looks_like_count,
    _to_number,
    parse_bankgirot_xlsx,
    parse_camt054,
    parse_deposit_file,
)

NBSP = "\xa0"
RECEIVER_BG = "5555-5555"

# ── XLSX ────────────────────────────────────────────────────────────────


def _xlsx(rows):
    """An .xlsx with one sheet holding ``rows`` (None for an empty cell, [] for an empty row)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Insättningsuppgifter"
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# Columns B, F and G are not read by the parser; the filler text would show a shifted column.
DETAIL_HEADER = ["Avsändare", "ej läst", "Referens", "Bankgironummer", "Belopp", "ej läst", "ej läst", "Meddelande"]


def _payer(sender, ref, bg, amount, message=None):
    """A payer row as the parser reads it: sender in A, reference in C, payer's Bankgiro in D,
    amount in E, message in H."""
    return [sender, "ej läst", ref, bg, amount, "ej läst", "ej läst", message]


def _sheet(payers, *, date="2026-09-15", total=("2", f"2{NBSP}000,00", "TOTALT")):
    """The export as the parser reads it: the date and receiving Bankgiro on the row under
    "Datum, Bankkontonummer, Bankgironummer, Mottagare", the TOTALT row (number of deposits, the
    amount as Swedish text, "TOTALT"), one row per payer under "Avsändare", and the footer."""
    rows = [["Insättningsuppgifter Detaljer"], []]
    if date:
        rows += [
            ["Datum", "Bankkontonummer", "Bankgironummer", "Mottagare"],
            [date, "0000-0 000 000 000-0", RECEIVER_BG, "Exempelföreningen"],
            [],
        ]
    if total:
        rows += [list(total), []]
    rows += [DETAIL_HEADER, *payers, [], ["© Bankgirot"]]
    return _xlsx(rows)


PAYERS = [
    _payer("Anna Testsson", "203100425", "555-5555", f"1{NBSP}000,00"),
    # reference and amount stored as numbers, not text
    _payer("Bo Exempelsson", 203100433, "999-9999", 750.0),
    _payer("Cecilia Provsson", None, None, "250,00", "ÅRSAVGIFT 2026 EXEMPELVÄGEN 11"),
]
PAYER_DETAILS = [
    {"sender": "Anna Testsson", "ref": "203100425", "bg": "555-5555", "amount": 1000.0, "message": ""},
    {"sender": "Bo Exempelsson", "ref": "203100433", "bg": "999-9999", "amount": 750.0, "message": ""},
    {"sender": "Cecilia Provsson", "ref": "", "bg": "", "amount": 250.0, "message": "ÅRSAVGIFT 2026 EXEMPELVÄGEN 11"},
]
# rows under "Avsändare" without a positive amount are not payments
NOT_PAYMENTS = [
    _payer("David Nollsson", "203100441", "555-5555", "0,00"),
    _payer("Eva Minusson", "203100466", "555-5555", "-50,00"),
    _payer("Filip Tomsson", None, None, None),
]


@tagged("post_install", "-at_install")
class TestBankgirotXlsx(TransactionCase):
    def test_deposit_with_payers(self):
        self.assertEqual(
            parse_bankgirot_xlsx(_sheet(PAYERS + NOT_PAYMENTS)),
            {"date": "2026-09-15", "total": 2000.0, "details": PAYER_DETAILS, "receiver_bg": RECEIVER_BG},
        )

    def test_total_row(self):
        """The amount on the TOTALT row in whichever cell and format; a digit string of up to three
        digits is the number of deposits, not the amount (2026-09-14: "2 000,00" as text gave no
        total at all)."""
        for total_row, expected in (
            (("2", f"2{NBSP}000,00", "TOTALT"), 2000.0),
            (("TOTALT", 2000), 2000.0),
            (("12", "TOTALT", "1 234,50"), 1234.5),
            (("TOTALT", None, "1.234,50", "3"), 1234.5),
            (("TOTALT", "1000"), 1000.0),
        ):
            parsed = parse_bankgirot_xlsx(_sheet([], total=total_row))
            self.assertEqual(parsed["total"], expected, total_row)

    def test_without_total_row_the_payers_are_summed(self):
        payers = [
            _payer("Anna Testsson", "203100425", "555-5555", "100,10"),
            _payer("Bo Exempelsson", "203100433", "999-9999", "200,20"),
        ]
        parsed = parse_bankgirot_xlsx(_sheet(payers, total=None))
        self.assertEqual(parsed["total"], 300.3, "rounded to öre, not 300.29999999999995")

    def test_date_row(self):
        """The first ISO date in column A is the deposit date, a time after it cut off; column C is
        the receiving Bankgiro only when it looks like one (3 or 4 digits, dash, 4 digits)."""
        rows = [
            ["Datum", "Bankkontonummer", "Bankgironummer", "Mottagare"],
            ["2026-09-15 00:00:00", None, "555-5555", None],
            ["2026-09-16", None, "ej angivet", None],
        ]
        parsed = parse_bankgirot_xlsx(_xlsx(rows))
        self.assertEqual(parsed["date"], "2026-09-15")
        self.assertEqual(parsed["receiver_bg"], "555-5555")

    def test_excel_date_and_numeric_reference(self):
        """A date stored as a real Excel date and an OCR reference stored as a number are read as the
        date and as the digits, not missed or read as "203100425.0"."""
        payers = [_payer("Anna Exempel", 203100425.0, "555-5556", 1000.0)]
        parsed = parse_bankgirot_xlsx(_sheet(payers, date=datetime.date(2026, 9, 15)))
        self.assertEqual(parsed["date"], "2026-09-15")
        self.assertEqual(parsed["receiver_bg"], RECEIVER_BG)
        self.assertEqual(parsed["details"][0]["ref"], "203100425")

    def test_without_date_or_total(self):
        parsed = parse_bankgirot_xlsx(_sheet(PAYERS, date=None))
        self.assertIsNone(parsed["date"])
        self.assertIsNone(parsed["receiver_bg"])
        self.assertEqual(parsed["total"], 2000.0)
        self.assertEqual(parsed["details"], PAYER_DETAILS)
        self.assertEqual(
            parse_bankgirot_xlsx(_sheet([], date=None, total=None)),
            {"date": None, "total": None, "details": [], "receiver_bg": None},
        )


# ── camt.054 ────────────────────────────────────────────────────────────

CAMT_02 = "urn:iso:std:iso:20022:tech:xsd:camt.054.001.02"
CAMT_08 = "urn:iso:std:iso:20022:tech:xsd:camt.054.001.08"


def _camt(*notifications, ns=CAMT_02):
    """A camt.054 document, one <Ntfctn> per argument (its entries as XML text)."""
    ntfctns = "".join(
        f"""
    <Ntfctn>
      <Id>TEST-NTFCTN-{i}</Id>
      <CreDtTm>2026-09-15T06:00:00</CreDtTm>
      <Acct><Id><IBAN>SE0000000000000000000001</IBAN></Id></Acct>{entries}
    </Ntfctn>"""
        for i, entries in enumerate(notifications, 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="{ns}">
  <BkToCstmrDbtCdtNtfctn>
    <GrpHdr><MsgId>TEST-MSG-1</MsgId><CreDtTm>2026-09-15T06:00:00</CreDtTm></GrpHdr>{ntfctns}
  </BkToCstmrDbtCdtNtfctn>
</Document>
""".encode()


def _bg(tag, number):
    """An account block holding a Bankgiro number (<tag><Id><Othr> with SchmeNm/Prtry BGNR)."""
    return f"<{tag}><Id><Othr><Id>{number}</Id><SchmeNm><Prtry>BGNR</Prtry></SchmeNm></Othr></Id></{tag}>"


# One Bankgiro deposit of 2 000,00 from two payers: an OCR reference with the payer's Bankgiro,
# and an invoice number with a free-text message in two parts.
DEPOSIT_ENTRY = f"""
      <Ntry>
        <Amt Ccy="SEK">2000.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Sts>BOOK</Sts>
        <BookgDt><Dt>2026-09-15</Dt></BookgDt>
        <ValDt><Dt>2026-09-15</Dt></ValDt>
        <NtryDtls>
          <TxDtls>
            <AmtDtls><TxAmt><Amt Ccy="SEK">1500.00</Amt></TxAmt></AmtDtls>
            <RltdPties>
              <Dbtr><Nm>Anna Testsson</Nm></Dbtr>
              {_bg("DbtrAcct", "5555555")}
              {_bg("CdtrAcct", "55555555")}
            </RltdPties>
            <RmtInf>
              <Strd>
                <CdtrRefInf><Tp><CdOrPrtry><Cd>SCOR</Cd></CdOrPrtry></Tp><Ref>203100425</Ref></CdtrRefInf>
              </Strd>
            </RmtInf>
          </TxDtls>
          <TxDtls>
            <AmtDtls><TxAmt><Amt Ccy="SEK">500.00</Amt></TxAmt></AmtDtls>
            <RltdPties>
              <Dbtr><Nm>Bo Exempelsson</Nm></Dbtr>
              {_bg("CdtrAcct", "55555555")}
            </RltdPties>
            <RmtInf>
              <Ustrd>ÅRSAVGIFT 2026</Ustrd>
              <Ustrd>EXEMPELVÄGEN 11</Ustrd>
              <Strd>
                <RfrdDocInf><Tp><CdOrPrtry><Cd>CINV</Cd></CdOrPrtry></Tp><Nb>INV/2031/0043</Nb></RfrdDocInf>
              </Strd>
            </RmtInf>
          </TxDtls>
        </NtryDtls>
      </Ntry>"""
DEBIT_ENTRY = """
      <Ntry>
        <Amt Ccy="SEK">750.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Sts>BOOK</Sts>
        <BookgDt><Dt>2026-09-15</Dt></BookgDt>
        <NtryDtls>
          <TxDtls>
            <AmtDtls><TxAmt><Amt Ccy="SEK">750.00</Amt></TxAmt></AmtDtls>
            <RltdPties><Cdtr><Nm>Exempel Elbolaget</Nm></Cdtr></RltdPties>
          </TxDtls>
        </NtryDtls>
      </Ntry>"""
PENDING_ENTRY = """
      <Ntry>
        <Amt Ccy="SEK">300.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Sts>PDNG</Sts>
        <BookgDt><Dt>2026-09-16</Dt></BookgDt>
        <NtryDtls>
          <TxDtls>
            <AmtDtls><TxAmt><Amt Ccy="SEK">300.00</Amt></TxAmt></AmtDtls>
            <RltdPties><Dbtr><Nm>Cecilia Provsson</Nm></Dbtr></RltdPties>
          </TxDtls>
        </NtryDtls>
      </Ntry>"""
CAMT_DEPOSIT = _camt(DEPOSIT_ENTRY + DEBIT_ENTRY + PENDING_ENTRY)
CAMT_DEPOSIT_PARSED = [
    {
        "date": "2026-09-15",
        "total": 2000.0,
        "receiver_bg": RECEIVER_BG,
        "details": [
            {"sender": "Anna Testsson", "ref": "203100425", "bg": "555-5555", "amount": 1500.0, "message": ""},
            {
                "sender": "Bo Exempelsson",
                "ref": "INV/2031/0043",
                "bg": "",
                "amount": 500.0,
                "message": "ÅRSAVGIFT 2026 EXEMPELVÄGEN 11",
            },
        ],
    }
]


@tagged("post_install", "-at_install")
class TestCamt054(TransactionCase):
    def test_booked_credit_entry(self):
        """One deposit per booked credit entry, one detail per payer; the debit and the pending
        credit on the same notification are not deposits."""
        self.assertEqual(parse_camt054(CAMT_DEPOSIT), CAMT_DEPOSIT_PARSED)

    def test_payment_split_over_invoices(self):
        """A payment naming several invoices, each with its own amount adding up to the payment,
        is one detail per invoice. When the amounts are missing or do not add up it stays one
        detail with all the references."""
        entry = f"""
      <Ntry>
        <Amt Ccy="SEK">1750.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Sts>BOOK</Sts>
        <BookgDt><Dt>2026-09-17</Dt></BookgDt>
        <NtryDtls>
          <TxDtls>
            <AmtDtls><TxAmt><Amt Ccy="SEK">1000.00</Amt></TxAmt></AmtDtls>
            <RltdPties><Dbtr><Nm>Cecilia Provsson</Nm></Dbtr>{_bg("DbtrAcct", "9999999")}</RltdPties>
            <RmtInf>
              <Strd>
                <RfrdDocInf><Nb>INV/2031/0044</Nb></RfrdDocInf>
                <RfrdDocAmt><RmtdAmt Ccy="SEK">600.00</RmtdAmt></RfrdDocAmt>
              </Strd>
              <Strd>
                <RfrdDocAmt><RmtdAmt Ccy="SEK">400.00</RmtdAmt></RfrdDocAmt>
                <CdtrRefInf><Ref>203100466</Ref></CdtrRefInf>
              </Strd>
            </RmtInf>
          </TxDtls>
          <TxDtls>
            <AmtDtls><TxAmt><Amt Ccy="SEK">500.00</Amt></TxAmt></AmtDtls>
            <RltdPties><Dbtr><Nm>David Nollsson</Nm></Dbtr></RltdPties>
            <RmtInf>
              <Strd>
                <RfrdDocInf><Nb>INV/2031/0045</Nb></RfrdDocInf>
                <RfrdDocAmt><RmtdAmt Ccy="SEK">300.00</RmtdAmt></RfrdDocAmt>
              </Strd>
              <Strd>
                <RfrdDocAmt><RmtdAmt Ccy="SEK">100.00</RmtdAmt></RfrdDocAmt>
                <CdtrRefInf><Ref>20310454</Ref></CdtrRefInf>
              </Strd>
            </RmtInf>
          </TxDtls>
          <TxDtls>
            <AmtDtls><TxAmt><Amt Ccy="SEK">250.00</Amt></TxAmt></AmtDtls>
            <RltdPties><Dbtr><Nm>Eva Minusson</Nm></Dbtr></RltdPties>
            <RmtInf>
              <Strd><CdtrRefInf><Ref>203100441</Ref></CdtrRefInf></Strd>
              <Strd><CdtrRefInf><Ref>12345674</Ref></CdtrRefInf></Strd>
            </RmtInf>
          </TxDtls>
        </NtryDtls>
      </Ntry>"""
        (deposit,) = parse_camt054(_camt(entry))
        self.assertEqual(deposit["total"], 1750.0)
        self.assertIsNone(deposit["receiver_bg"])
        self.assertEqual(
            deposit["details"],
            [
                {"sender": "Cecilia Provsson", "ref": "INV/2031/0044", "bg": "999-9999", "amount": 600.0, "message": ""},
                {"sender": "Cecilia Provsson", "ref": "203100466", "bg": "999-9999", "amount": 400.0, "message": ""},
                {"sender": "David Nollsson", "ref": "INV/2031/0045 20310454", "bg": "", "amount": 500.0, "message": ""},
                {"sender": "Eva Minusson", "ref": "203100441 12345674", "bg": "", "amount": 250.0, "message": ""},
            ],
        )

    def test_later_version_and_receiver_from_entry_reference(self):
        """camt.054.001.08: status as <Sts><Cd>, booking date as DtTm, the amount directly on
        TxDtls, the payer's name under Pty. Without a creditor account the receiving Bankgiro
        comes from NtryRef ("BG" and the number)."""
        entry = f"""
      <Ntry>
        <NtryRef>BG55555555</NtryRef>
        <Amt Ccy="SEK">300.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Sts><Cd>BOOK</Cd></Sts>
        <BookgDt><DtTm>2026-09-16T10:15:00+02:00</DtTm></BookgDt>
        <NtryDtls>
          <TxDtls>
            <Amt Ccy="SEK">300.00</Amt>
            <CdtDbtInd>CRDT</CdtDbtInd>
            <RltdPties>
              <Dbtr><Pty><Nm>Filip Exempelsson</Nm></Pty></Dbtr>
              {_bg("DbtrAcct", "555-5556")}
            </RltdPties>
            <RmtInf><Strd><CdtrRefInf><Ref>12345674</Ref></CdtrRefInf></Strd></RmtInf>
          </TxDtls>
        </NtryDtls>
      </Ntry>"""
        self.assertEqual(
            parse_camt054(_camt(entry, ns=CAMT_08)),
            [
                {
                    "date": "2026-09-16",
                    "total": 300.0,
                    "receiver_bg": RECEIVER_BG,
                    "details": [
                        {"sender": "Filip Exempelsson", "ref": "12345674", "bg": "555-5556", "amount": 300.0, "message": ""}
                    ],
                }
            ],
        )

    def test_pending_entry_in_a_later_version_is_ignored(self):
        """.001.08 nests the status (<Sts><Cd>); a pending entry must not become a deposit."""
        pending = DEPOSIT_ENTRY.replace("<Sts>BOOK</Sts>", "<Sts><Cd>PDNG</Cd></Sts>")
        self.assertNotEqual(pending, DEPOSIT_ENTRY)
        self.assertEqual(parse_camt054(_camt(pending, ns=CAMT_08)), [])
        booked = DEPOSIT_ENTRY.replace("<Sts>BOOK</Sts>", "<Sts><Cd>BOOK</Cd></Sts>")
        self.assertEqual(len(parse_camt054(_camt(booked, ns=CAMT_08))), 1)

    def test_whitespace_or_byte_order_mark_before_the_declaration(self):
        for prefix in (b"\r\n  ", b"\xef\xbb\xbf", b"\xef\xbb\xbf\n"):
            with self.subTest(prefix=prefix):
                self.assertEqual(parse_camt054(prefix + CAMT_DEPOSIT), CAMT_DEPOSIT_PARSED)
                self.assertEqual(parse_deposit_file(prefix + CAMT_DEPOSIT), CAMT_DEPOSIT_PARSED)

    def test_entries_without_date_or_details(self):
        """An entry without a booking date is skipped; one without details is still a deposit (the
        total only). Every notification in the file is read."""
        undated = """
      <Ntry>
        <Amt Ccy="SEK">100.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Sts>BOOK</Sts>
      </Ntry>"""
        bare = """
      <Ntry>
        <Amt Ccy="SEK">400.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Sts>BOOK</Sts>
        <BookgDt><Dt>2026-09-18</Dt></BookgDt>
      </Ntry>"""
        self.assertEqual(
            parse_camt054(_camt(undated, bare, DEPOSIT_ENTRY)),
            [{"date": "2026-09-18", "total": 400.0, "details": [], "receiver_bg": None}] + CAMT_DEPOSIT_PARSED,
        )
        self.assertEqual(parse_camt054(_camt(DEBIT_ENTRY)), [])


# ── File type and helpers ───────────────────────────────────────────────


@tagged("post_install", "-at_install")
class TestParseDepositFile(TransactionCase):
    def test_xlsx_is_one_deposit(self):
        data = _sheet(PAYERS)
        self.assertEqual(parse_deposit_file(data), [parse_bankgirot_xlsx(data)])

    def test_xml_is_camt054(self):
        self.assertEqual(parse_deposit_file(CAMT_DEPOSIT), CAMT_DEPOSIT_PARSED)
        # a byte order mark hides the "<"; the camt.054 namespace in the first 512 bytes tells
        self.assertEqual(parse_deposit_file(b"\xef\xbb\xbf" + CAMT_DEPOSIT), CAMT_DEPOSIT_PARSED)
        # whitespace before the root element (without an XML declaration, which must come first)
        no_declaration = CAMT_DEPOSIT.split(b"?>", 1)[1]
        self.assertEqual(parse_deposit_file(b"\r\n  " + no_declaration.lstrip()), CAMT_DEPOSIT_PARSED)
        # any other XML goes to the camt.054 parser too, and holds no deposits
        self.assertEqual(parse_deposit_file(b"<Document/>"), [])


@tagged("post_install", "-at_install")
class TestParserHelpers(TransactionCase):
    def test_cell_text(self):
        """openpyxl can give a stored reference back as a float; it must read as its digits."""
        self.assertEqual(_cell_text(203100425.0), "203100425")
        self.assertEqual(_cell_text(203100425), "203100425")
        self.assertEqual(_cell_text(12.5), "12.5")
        self.assertEqual(_cell_text("  INV/2031/0042 "), "INV/2031/0042")

    def test_to_number(self):
        for value, expected in (
            (f"2{NBSP}000,00", 2000.0),
            ("2 000,00", 2000.0),
            ("1 234,50", 1234.5),
            ("1.234,50", 1234.5),
            ("1234.50", 1234.5),
            ("-50,00", -50.0),
            ("12", 12.0),
            (1500, 1500.0),
            (750.25, 750.25),
        ):
            self.assertEqual(_to_number(value), expected, repr(value))
        for value in (None, "", "TOTALT", "ej läst", "12 st"):
            self.assertIsNone(_to_number(value), repr(value))

    def test_looks_like_count(self):
        for value in ("2", " 12 ", "999"):
            self.assertTrue(_looks_like_count(value), repr(value))
        for value in ("1000", "2,00", "2 000,00", "", "TOTALT", 2, 2.0, None):
            self.assertFalse(_looks_like_count(value), repr(value))

    def test_bgnr(self):
        def acct(xml, ns=CAMT_02):
            return etree.fromstring(f'<RltdPties xmlns="{ns}">{xml}</RltdPties>'.encode())[0]

        self.assertEqual(_bgnr(acct(_bg("CdtrAcct", "55555555"))), "5555-5555")
        self.assertEqual(_bgnr(acct(_bg("DbtrAcct", "5555555"), ns=CAMT_08)), "555-5555")
        self.assertEqual(_bgnr(acct(_bg("DbtrAcct", "555-5555"))), "555-5555")
        self.assertEqual(_bgnr(acct(_bg("DbtrAcct", "123456"))), "123456", "too short to format")
        self.assertEqual(_bgnr(etree.fromstring(_bg("DbtrAcct", "55555555"))), "5555-5555", "no namespace")
        other_scheme = "<DbtrAcct><Id><Othr><Id>55555555</Id><SchmeNm><Prtry>BBAN</Prtry></SchmeNm></Othr></Id></DbtrAcct>"
        self.assertEqual(_bgnr(acct(other_scheme)), "")
        self.assertEqual(_bgnr(acct("<DbtrAcct><Id><IBAN>SE0000000000000000000001</IBAN></Id></DbtrAcct>")), "")
        self.assertEqual(_bgnr(None), "")


# ── Through the wizard ──────────────────────────────────────────────────


@tagged("post_install", "-at_install")
class TestImportWizardFiles(TransactionCase):
    def test_unusable_files_are_reported_per_file(self):
        """A file that cannot be read, one without deposits, and one without a date each get their
        own result row; the import goes on with the next file."""
        files = [
            ("trasig.xlsx", b"inte ett kalkylark", "Kunde inte läsa fil"),
            ("bara-uttag.xml", _camt(DEBIT_ENTRY), "Inga insättningar i filen"),
            ("utan-datum.xlsx", _sheet(PAYERS, date=None), "Saknar datum eller totalbelopp"),
        ]
        wizard = self.env["account.bankgirot.import.wizard"].create(
            {"file_ids": [Command.create({"name": name, "raw": data}) for name, data, _message in files]}
        )
        wizard.action_import()
        html = str(wizard.result_html)
        self.assertIn("Filer: 3 | Detaljer: 0", html)
        rows = html.split("<tr")
        for name, _data, message in files:
            self.assertTrue(any(name in row and message in row for row in rows), name)
