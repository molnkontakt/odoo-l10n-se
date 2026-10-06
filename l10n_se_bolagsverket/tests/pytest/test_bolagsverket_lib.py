"""pytest for lib/bolagsverket.py, without Odoo.

Run from the repository root:
``python -m pytest -p no:cacheprovider --confcutdir=l10n_se_bolagsverket/tests/pytest
l10n_se_bolagsverket/tests/pytest``

The JSON fixtures in ``tests/data`` have the exact structure of live answers from Bolagsverket's API (2026-10-06)
with invented names, numbers and addresses; ``not_found.json`` is the live answer for an unregistered number.
"""
import importlib.util
import json
import pathlib
from datetime import date

import pytest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("bolagsverket", HERE.parent.parent / "lib" / "bolagsverket.py")
bv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bv)


def load(name):
    return json.loads((HERE.parent / "data" / name).read_text())["organisationer"]


def parsed(name):
    return bv.parse_answer(load(name))


# -- identity numbers ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, vat, expected", [
    ("559588-0419", None, "5595880419"),
    ("5595880419", None, "5595880419"),
    ("16559588-0419", None, "5595880419"),
    (" 559588 0419 ", None, "5595880419"),
    (None, "SE559588041901", "5595880419"),
    ("", "se559588041901", "5595880419"),
    ("559588-0418", None, None),            # wrong check digit
    ("12345", None, None),
    (None, "DE123456789", None),
    ("802400-3399", None, None),             # Luhn fails
    ("19550101-1232", None, "5501011232"),   # sole trader (personal identity number)
    ("000000-0000", None, None),             # passes Luhn, but no real number
    (None, "SE000000000001", None),
])
def test_normalize_identity(value, vat, expected):
    assert bv.normalize_identity(value, vat) == expected


def test_registry_wins_over_vat():
    assert bv.normalize_identity("556000-0001", "SE559588041901") == "5560000001"


def test_format_identity():
    assert bv.format_identity("5595880419") == "559588-0419"


def test_identity_kind_and_request_identity():
    assert bv.identity_kind("5595880419") == bv.KIND_ORG
    assert bv.identity_kind("5501011232") == bv.KIND_PERSON
    assert bv.request_identity("5595880419") == "5595880419"
    assert bv.request_identity("5501011232", today=date(2026, 10, 6)) == "195501011232"
    assert bv.request_identity("0501011238", today=date(2026, 10, 6)) in ("200501011238", "0501011238")


# -- parsing --------------------------------------------------------------------------------------

def test_parse_active():
    p = bv.pick_organisation(parsed("active.json"))
    assert p["name"] == "Exempelbolaget i Teststad AB"
    assert [n["name"] for n in p["names"]] == ["Exempelbolaget i Teststad AB", "Testverkstan"]
    assert p["address"] == {"street": "Provgatan 1", "co": "c/o Test Testsson", "zip": "12345",
                            "city": "Upplands Väsby", "country": ""}
    assert p["sni"] == [("62100", "Dataprogrammering")], "blank SNI slots must be dropped"
    assert p["org_form_text"] == "Aktiebolag"
    assert p["registration_date"] == date(2019, 3, 14)
    assert p["active"] is True
    assert p["advertising_block"] is False
    assert not p["not_found"]
    assert bv.status_of(p) == (bv.STATUS_ACTIVE, None)


def test_parse_bankrupt():
    p = bv.pick_organisation(parsed("bankrupt.json"))
    status, note = bv.status_of(p)
    assert status == bv.STATUS_PROCEDURE
    assert note == "Konkurs från 2026-09-30"


def test_parse_deregistered_wins_over_inactive():
    p = bv.pick_organisation(parsed("deregistered.json"))
    assert p["active"] is False
    assert bv.status_of(p) == (bv.STATUS_DEREGISTERED, "Fusion, 2025-06-01")


def test_parse_not_found():
    p = bv.pick_organisation(parsed("not_found.json"))
    assert p["not_found"] and p["name"] is None
    assert bv.status_of(p) == (bv.STATUS_NOT_FOUND, None)


def test_sole_trader_with_several_businesses():
    # Bolagsverket's own example (anonymised): business 1 deregistered 2001 ("n/a" reason), business 2 active
    orgs = parsed("sole_trader_spec.json")
    assert [o["name_protection_no"] for o in orgs] == [1, 2]
    assert bv.status_of(orgs[0]) == (bv.STATUS_DEREGISTERED, "2001-03-15"), "an 'n/a' reason is left out"
    assert bv.pick_organisation(orgs)["name"] == "TESTSSONS CYKEL", "prefer a business that is not deregistered"
    assert bv.pick_organisation(orgs, "Skoaffären Test Testsson")["name"] == "TESTSSONS CYKEL", \
        "a deregistered business is not picked just because the name matches"
    assert bv.pick_organisation(orgs, name_protection_no=1)["name"] == "SKOAFFÄREN TEST TESTSSON"
    assert set(bv.all_names(orgs)) == {"SKOAFFÄREN TEST TESTSSON", "TESTSSONS CYKEL"}


def test_incomplete_answer_is_refused():
    # Bolagsverket's own example of a data source being unavailable
    with pytest.raises(bv.BolagsverketError) as exc:
        parsed("source_error_spec.json")
    assert exc.value.incomplete and not exc.value.retry and "OTILLGANGLIG_UPPGIFTSKALLA" in str(exc.value)


def test_invalid_request_is_about_the_number():
    org = load("active.json")[0]
    org["organisationsnamn"] = {"fel": {"typ": "OGILTIG_BEGARAN"}, "dataproducent": "Bolagsverket"}
    with pytest.raises(bv.BolagsverketError) as exc:
        bv.parse_answer([org])
    assert exc.value.status == 400 and not exc.value.retry and not exc.value.incomplete


def test_incomplete_procedure_part_is_refused():
    org = load("bankrupt.json")[0]
    org["pagaendeAvvecklingsEllerOmstruktureringsforfarande"] = {"fel": {"typ": "TIMEOUT"}, "dataproducent": "Bolagsverket"}
    with pytest.raises(bv.BolagsverketError):
        bv.parse_answer([org])


def test_scb_part_missing_is_fine():
    org = load("active.json")[0]
    org["verksamOrganisation"] = {"fel": {"typ": "OTILLGANGLIG_UPPGIFTSKALLA"}, "dataproducent": "SCB"}
    p = bv.parse_answer([org])[0]
    assert p["active"] is None and bv.status_of(p)[0] == bv.STATUS_ACTIVE


def test_misspelled_procedure_key_from_the_spec():
    org = load("active.json")[0]
    org["pagandeAvvecklingsEllerOmstruktureringsforfarande"] = {
        "pagandeAvvecklingsEllerOmstruktureringsforfarandeLista": [{"kod": "LI", "klartext": "Likvidation"}]}
    assert bv.status_of(bv.parse_answer([org])[0]) == (bv.STATUS_PROCEDURE, "Likvidation")


def test_empty_answer():
    assert bv.pick_organisation([]) is None
    assert bv.status_of(None) == (bv.STATUS_NOT_FOUND, None)


# -- names ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("odoo_name, expected", [
    ("Exempelbolaget i Teststad AB", True),
    ("EXEMPELBOLAGET I TESTSTAD AKTIEBOLAG", True),
    ("Exempelbolaget i Teststad", True),
    ("Testverkstan", True),                 # secondary business name
    ("Exempelbolaget", True),               # shortened at a word boundary
    ("Teststad AB", False),                 # a word in the middle is not enough
    ("Exempelbolaget i", True),
    ("Exempel AB", False),
    ("Nemoris AB", False),
    ("AB", False),
    ("", False),
])
def test_names_match(odoo_name, expected):
    names = bv.all_names(parsed("active.json"))
    assert bv.names_match(odoo_name, names) is expected


def test_normalize_name():
    assert bv.normalize_name("Bygg & Konsult i Nora AB (publ)") == "bygg och konsult i nora"
    assert bv.normalize_name("Åkeri Öst Ekonomisk förening") == "akeri ost"


# -- client ---------------------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status, body=None, raw=b""):
        self.status_code = status
        self._body = body
        self.content = raw or (json.dumps(body).encode() if body is not None else b"")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.token_calls = 0
        self.calls = []

    def post(self, url, data=None, auth=None, timeout=None):
        self.token_calls += 1
        assert data["grant_type"] == "client_credentials" and auth == ("id", "secret")
        return FakeResponse(200, {"access_token": f"tok{self.token_calls}", "expires_in": 3600})

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append((method, url, json, headers["Authorization"]))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def clear_tokens():
    bv._token_cache.clear()


def test_client_organisations_and_token_cache():
    s = FakeSession([FakeResponse(200, {"organisationer": load("active.json")}),
                     FakeResponse(200, {"organisationer": []})])
    c = bv.Client("id", "secret", "prod", session=s)
    assert c.organisations("5560000001")[0]["identity"] == "5560000001"
    assert c.organisations("5595880419") == []
    assert s.token_calls == 1, "token must be reused"
    assert s.calls[0][1].endswith("/vardefulla-datamangder/v1/organisationer")
    assert s.calls[0][2] == {"identitetsbeteckning": "5560000001"}


def test_client_refreshes_token_once_on_401():
    s = FakeSession([FakeResponse(401, {"title": "Unauthorized"}), FakeResponse(200, {"dokument": []})])
    c = bv.Client("id", "secret", "test", session=s)
    assert c.documents("5560000001") == []
    assert s.token_calls == 2
    assert s.calls[0][3] != s.calls[1][3]
    assert "gw-accept2" in s.calls[0][1]


@pytest.mark.parametrize("status, retry", [(400, False), (403, False), (429, True), (503, True)])
def test_client_errors(status, retry):
    s = FakeSession([FakeResponse(status, {"detail": "Felaktigt format för identitetsbeteckning."})])
    with pytest.raises(bv.BolagsverketError) as exc:
        bv.Client("id", "secret", session=s).organisations("12345")
    assert exc.value.status == status and exc.value.retry is retry
    assert "secret" not in str(exc.value)


def test_document_id_is_validated():
    c = bv.Client("id", "secret", session=FakeSession([]))
    with pytest.raises(bv.BolagsverketError):
        c.document("../../oauth2/token")


def test_token_errors_always_stop_the_run():
    class TokenSession(FakeSession):
        def post(self, url, data=None, auth=None, timeout=None):
            return FakeResponse(400, {"error": "invalid_scope"})
    with pytest.raises(bv.BolagsverketError) as exc:
        bv.Client("id", "secret", session=TokenSession([])).organisations("5560000001")
    assert exc.value.retry, "a token problem is never about one number"


def test_non_json_answer_is_retryable():
    s = FakeSession([FakeResponse(200, raw=b"<html>maintenance</html>")])
    with pytest.raises(bv.BolagsverketError) as exc:
        bv.Client("id", "secret", session=s).organisations("5560000001")
    assert exc.value.retry


def test_sole_trader_is_sent_with_century():
    s = FakeSession([FakeResponse(200, {"organisationer": load("sole_trader_spec.json")})])
    bv.Client("id", "secret", session=s).organisations("4001011230")
    assert s.calls[0][2] == {"identitetsbeteckning": "194001011230"}


def test_token_cache_depends_on_secret():
    s = FakeSession([FakeResponse(200, {"organisationer": []}), FakeResponse(200, {"organisationer": []})])
    bv.Client("id", "secret", session=s).organisations("5560000001")
    s2 = FakeSession([FakeResponse(200, {"organisationer": []})])
    s2.post = lambda url, data=None, auth=None, timeout=None: FakeResponse(200, {"access_token": "other"})
    bv.Client("id", "rotated", session=s2).organisations("5560000001")
    assert s2.calls[0][3] == "Bearer other"


@pytest.mark.parametrize("odoo_name, registered, expected", [
    ("IKEA", "IKEA of Sweden AB", True),                       # short brand name at the start
    ("Konsult AB", "Konsult Anna Svensson AB", False),         # a generic first word proves nothing
    ("Konsult Anna", "Konsult Anna Svensson AB", True),        # two words do
    ("H&M", "H & M Hennes & Mauritz AB", True),
])
def test_names_match_edge_cases(odoo_name, registered, expected):
    assert bv.names_match(odoo_name, [registered]) is expected
