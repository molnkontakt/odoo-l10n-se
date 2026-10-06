"""Bolagsverket's *Värdefulla datamängder* (high-value datasets) API: client, response parsing and helpers.

Pure Python (only ``requests``), no Odoo import, so it can be tested and reused on its own.

API facts (OpenAPI "VärdefullaDatamängder v1", checked against the live service 2026-10-06):

* OAuth2 client credentials; token ``POST https://portal.api.bolagsverket.se/oauth2/token`` (test:
  ``portal-accept2``), scopes ``vardefulla-datamangder:read`` and ``vardefulla-datamangder:ping``; tokens live 3600 s.
* ``POST /organisationer`` and ``POST /dokumentlista`` with ``{"identitetsbeteckning": …}`` – an organisation number
  (10 digits) or a sole trader's personal identity number (12 digits), ``GET /dokument/{id}`` (ZIP), ``GET /isalive``.
* An unknown number is **200** with an organisation whose parts carry ``fel.typ = ORGANISATION_FINNS_EJ``. When a data
  source is down the parts carry ``OTILLGANGLIG_UPPGIFTSKALLA`` (or ``TIMEOUT``): the answer is then **incomplete** and
  must not be used to conclude anything. A malformed number is 400.
* One personal identity number can return several businesses, told apart by ``namnskyddslopnummer``.
* Free of charge; an account that is not used for six months is closed.
"""
import hashlib
import re
import threading
import time
import unicodedata
from datetime import date

import requests

ENVIRONMENTS = {
    "prod": ("https://portal.api.bolagsverket.se/oauth2/token",
             "https://gw.api.bolagsverket.se/vardefulla-datamangder/v1"),
    "test": ("https://portal-accept2.api.bolagsverket.se/oauth2/token",
             "https://gw-accept2.api.bolagsverket.se/vardefulla-datamangder/v1"),
}
SCOPE = "vardefulla-datamangder:read vardefulla-datamangder:ping"
TIMEOUT = 30

STATUS_DEREGISTERED = "deregistered"
STATUS_PROCEDURE = "procedure"     # ongoing bankruptcy, liquidation or reorganisation
STATUS_INACTIVE = "inactive"
STATUS_NOT_FOUND = "not_found"
STATUS_ACTIVE = "active"
STATUS_UNKNOWN = "unknown"
BAD_STATUSES = (STATUS_DEREGISTERED, STATUS_PROCEDURE)

NOT_FOUND_ERROR = "ORGANISATION_FINNS_EJ"
INVALID_REQUEST_ERROR = "OGILTIG_BEGARAN"   # about the number asked for – not a temporary problem
# Parts that decide the status; an error other than "not found" on any of them makes the answer incomplete.
STATUS_PARTS = ("organisationsnamn", "avregistreradOrganisation", "avregistreringsorsak",
                "pagaendeAvvecklingsEllerOmstruktureringsforfarande", "pagandeAvvecklingsEllerOmstruktureringsforfarande")

KIND_ORG = "org"
KIND_PERSON = "person"

# A single generic word at the start of a name proves nothing ("Konsult AB" vs "Konsult Anna Svensson AB").
_GENERIC_WORDS = {
    "konsult", "konsulting", "consulting", "bygg", "el", "data", "it", "service", "handel", "invest", "fastighet",
    "fastigheter", "transport", "redovisning", "ekonomi", "design", "media", "teknik", "hus", "mark", "tjanst",
    "tjanster", "group", "gruppen", "holding", "sverige", "sweden", "nordic", "svenska", "the", "nya", "ny",
}

_LEGAL_SUFFIXES = (
    "aktiebolag", "ab", "publ", "handelsbolag", "hb", "kommanditbolag", "kb", "ekonomisk forening", "ek for",
    "ekonomiska foreningen", "ideell forening", "bostadsrattsforening", "brf", "stiftelse", "enskild firma",
)


class BolagsverketError(Exception):
    """``retry`` = stop this run and try later (network, 429, 5xx, the token endpoint, an unreadable answer);
    ``incomplete`` = a data source behind the API was unavailable for this number – try this number later;
    otherwise the request for this one number was refused (400) or access is denied (403)."""

    def __init__(self, message, status=None, retry=False, incomplete=False):
        super().__init__(message)
        self.status = status
        self.retry = retry
        self.incomplete = incomplete


# ----------------------------------------------------------------------
# Identity numbers
# ----------------------------------------------------------------------

def _luhn_ok(digits):
    total = 0
    for i, ch in enumerate(digits):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def identity_kind(digits):
    """``org`` (third digit ≥ 2: organisation number), ``person`` (a valid date, day + 60 for a coordination
    number) or None for numbers that only pass the check digit by accident (``000000-0000``)."""
    if not digits or len(digits) != 10 or not digits.isdigit() or not _luhn_ok(digits) or set(digits) == {"0"}:
        return None
    if int(digits[2]) >= 2:
        return KIND_ORG
    month, day = int(digits[2:4]), int(digits[4:6])
    if day > 60:
        day -= 60
    if 1 <= month <= 12 and 1 <= day <= 31:
        return KIND_PERSON
    return None


def normalize_identity(value, vat=None):
    """10-digit Swedish organisation number (or a sole trader's personal identity number) from a company registry
    value such as ``559588-0419`` / ``16559588-0419`` / ``195501011232``, or from a Swedish VAT number
    ``SE559588041901`` when the registry is empty. ``None`` when nothing valid is found."""
    for raw in (value, vat):
        if not raw:
            continue
        text = str(raw).strip().upper()
        if text.startswith("SE"):
            digits = re.sub(r"\D", "", text[2:])
            if len(digits) == 12 and digits.endswith("01"):
                digits = digits[:10]
        else:
            digits = re.sub(r"\D", "", text)
            if len(digits) == 12 and digits[:2] in ("16", "18", "19", "20"):
                digits = digits[2:]
        if identity_kind(digits):
            return digits
    return None


def request_identity(digits, today=None):
    """What to send for a number: organisation numbers as 10 digits, personal identity numbers as 12 (the century
    guessed as for Swedish personal numbers: the most recent century that doesn't lie in the future)."""
    if identity_kind(digits) != KIND_PERSON:
        return digits
    today = today or date.today()
    century = today.year // 100
    if int(digits[:2]) > today.year % 100:
        century -= 1
    return f"{century:02d}{digits}"


def format_identity(digits):
    return f"{digits[:6]}-{digits[6:]}" if digits and len(digits) == 10 else digits


# ----------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------

def _part(org, key):
    """(value-dict, error-type) for a part of the organisation that may be null, a value or an error."""
    part = org.get(key)
    if not isinstance(part, dict):
        return None, None
    err = part.get("fel")
    if isinstance(err, dict) and err.get("typ"):
        return None, err.get("typ")
    return part, None


def _date(value):
    try:
        return date.fromisoformat(value[:10]) if value else None
    except (TypeError, ValueError):
        return None


def _text(part, key="klartext"):
    value = ((part or {}).get(key) or "").strip()
    return None if value.lower() in ("", "n/a") else value


def _title_city(city):
    """``JOHANNESHOV`` → ``Johanneshov``, ``UPPLANDS VÄSBY`` → ``Upplands Väsby``."""
    if not city or city != city.upper():
        return city or ""
    return " ".join(word.capitalize() for word in city.split(" "))


def parse_organisation(org):
    """One ``Organisation`` from the API as a flat dict (missing values are ``None``/empty)."""
    errors = {key: _part(org, key)[1] for key in org if isinstance(org.get(key), dict) and _part(org, key)[1]}
    incomplete = sorted({e for k, e in errors.items() if k in STATUS_PARTS and e != NOT_FOUND_ERROR})
    invalid = INVALID_REQUEST_ERROR in errors.values()

    names = []
    part, _err = _part(org, "organisationsnamn")
    for item in (part or {}).get("organisationsnamnLista") or []:
        if item and (item.get("namn") or "").strip():
            names.append({"name": item["namn"].strip(),
                          "type": ((item.get("organisationsnamntyp") or {}).get("kod") or "").strip()})
    main_name = next((n["name"] for n in names if n["type"] == "FORETAGSNAMN"), names[0]["name"] if names else None)

    address = {}
    part, _err = _part(org, "postadressOrganisation")
    post = (part or {}).get("postadress") or {}
    if post:
        address = {"street": (post.get("utdelningsadress") or "").strip(),
                   "co": (post.get("coAdress") or "").strip(),
                   "zip": (post.get("postnummer") or "").strip(),
                   "city": _title_city((post.get("postort") or "").strip()),
                   "country": (post.get("land") or "").strip()}

    sni = []
    part, _err = _part(org, "naringsgrenOrganisation")
    for item in (part or {}).get("sni") or []:
        code = (item.get("kod") or "").strip() if item else ""
        if code:
            sni.append((code, (item.get("klartext") or "").strip()))

    form, _err = _part(org, "organisationsform")
    legal, _err = _part(org, "juridiskForm")
    desc, _err = _part(org, "verksamhetsbeskrivning")
    dates, _err = _part(org, "organisationsdatum")
    active, _err = _part(org, "verksamOrganisation")
    advert, _err = _part(org, "reklamsparr")
    dereg, _err = _part(org, "avregistreradOrganisation")
    dereg_reason, _err = _part(org, "avregistreringsorsak")
    procedures = []
    for key in ("pagaendeAvvecklingsEllerOmstruktureringsforfarande",
                "pagandeAvvecklingsEllerOmstruktureringsforfarande"):   # the spec's example spells it like this
        procs_part, _err = _part(org, key)
        lists = [v for k, v in (procs_part or {}).items() if k.endswith("Lista") and isinstance(v, list)]
        for item in (lists[0] if lists else []):
            if item and (item.get("kod") or item.get("klartext")):
                procedures.append({"code": (item.get("kod") or "").strip(), "text": _text(item),
                                   "from": _date(item.get("fromDatum"))})

    identity = (org.get("organisationsidentitet") or {}).get("identitetsbeteckning")
    description = re.sub(r"\s+", " ", ((desc or {}).get("beskrivning") or "")).strip() or None
    return {
        "identity": identity,
        "name_protection_no": org.get("namnskyddslopnummer"),
        "name": main_name,
        "names": names,
        "org_form_text": _text(form),
        "legal_form_text": _text(legal),
        "address": address,
        "sni": sni,
        "description": description,
        "registration_date": _date((dates or {}).get("registreringsdatum")),
        "active": {"JA": True, "NEJ": False}.get(((active or {}).get("kod") or "").strip().upper()),
        "advertising_block": ((advert or {}).get("kod") or "").strip().upper() == "JA",
        "deregistered_date": _date((dereg or {}).get("avregistreringsdatum")),
        "deregistered_reason": _text(dereg_reason),
        "procedures": procedures,
        "not_found": NOT_FOUND_ERROR in errors.values() and not main_name,
        "incomplete": [e for e in incomplete if e != INVALID_REQUEST_ERROR],
        "invalid": invalid,
    }


def status_of(parsed):
    """(status, note) for a parsed organisation – see the STATUS_* constants. Never call this with an incomplete
    answer (``parsed["incomplete"]``): the client refuses those."""
    if parsed is None or parsed["not_found"]:
        return STATUS_NOT_FOUND, None
    if parsed["deregistered_date"] or parsed["deregistered_reason"]:
        note = ", ".join(x for x in (parsed["deregistered_reason"],
                                     parsed["deregistered_date"] and parsed["deregistered_date"].isoformat()) if x)
        return STATUS_DEREGISTERED, note or None
    if parsed["procedures"]:
        return STATUS_PROCEDURE, "; ".join(
            (p["text"] or p["code"]) + (f" från {p['from'].isoformat()}" if p["from"] else "")
            for p in parsed["procedures"])
    if parsed["active"] is False:
        return STATUS_INACTIVE, None
    if parsed["name"]:
        return STATUS_ACTIVE, None
    return STATUS_UNKNOWN, None


def parse_answer(organisations):
    """All organisations in an answer, parsed. Raises a retryable error when any of them is incomplete (a data
    source was unavailable) – nothing may be concluded from such an answer."""
    parsed = [parse_organisation(o) for o in organisations or [] if isinstance(o, dict)]
    if any(p["invalid"] for p in parsed):
        raise BolagsverketError("Bolagsverket refused the number (OGILTIG_BEGARAN)", 400)
    broken = sorted({e for p in parsed for e in p["incomplete"]})
    if broken:
        raise BolagsverketError(f"incomplete answer ({', '.join(broken)})", incomplete=True)
    return parsed


def pick_organisation(parsed, partner_name=None, name_protection_no=None):
    """The organisation to use when one number returns several (a sole trader's businesses): the one stored on the
    partner, else a business that isn't deregistered and whose name matches, else any that isn't deregistered,
    else the first with a name."""
    if not parsed:
        return None
    if name_protection_no:
        for p in parsed:
            if p["name_protection_no"] == name_protection_no:
                return p
    live = [p for p in parsed if p["name"] and status_of(p)[0] != STATUS_DEREGISTERED]
    if partner_name:
        for p in live:
            if names_match(partner_name, [n["name"] for n in p["names"]]):
                return p
    return (live or [p for p in parsed if p["name"]] or parsed)[0]


def all_names(parsed):
    return [n["name"] for p in parsed or [] for n in p["names"]]


# ----------------------------------------------------------------------
# Names
# ----------------------------------------------------------------------

def normalize_name(name):
    """Comparable form of a company name: lower case, no accents/punctuation, no legal-form words."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("&", " och ")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    for suffix in sorted(_LEGAL_SUFFIXES, key=len, reverse=True):
        text = re.sub(rf"(^| ){re.escape(suffix)}( |$)", " ", text).strip()
    return re.sub(r" +", " ", text)


def names_match(partner_name, registered_names):
    """True when the name in Odoo plausibly is one of the registered names: equal after normalising, or one starts
    with the other at a word boundary (``Exempelbolaget`` ~ ``Exempelbolaget i Teststad AB``). A word in the
    middle of the other name is not enough (``Bygg AB`` ≠ ``Total Bygg & Konsult AB``)."""
    mine = normalize_name(partner_name)
    if not mine:
        return False
    for name in registered_names or []:
        theirs = normalize_name(name)
        if not theirs:
            continue
        if mine == theirs:
            return True
        short, long_ = sorted((mine, theirs), key=len)
        if not long_.startswith(short + " "):
            continue
        words = short.split(" ")
        if len(words) >= 2 or (len(short) >= 3 and short not in _GENERIC_WORDS):
            return True
    return False


# ----------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------

_token_cache = {}
_token_lock = threading.Lock()


class Client:
    """Small API client. Tokens are cached per (environment, client id, secret) until shortly before they expire."""

    def __init__(self, client_id, client_secret, environment="prod", session=None):
        if environment not in ENVIRONMENTS:
            raise BolagsverketError(f"unknown environment {environment!r}")
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url, self.base_url = ENVIRONMENTS[environment]
        self.session = session or requests
        self._key = (environment, client_id, hashlib.sha256((client_secret or "").encode()).hexdigest())

    def _token(self, force=False):
        with _token_lock:
            cached = _token_cache.get(self._key)
        if cached and not force and cached[1] > time.time() + 60:
            return cached[0]
        try:
            resp = self.session.post(self.token_url, data={"grant_type": "client_credentials", "scope": SCOPE},
                                     auth=(self.client_id, self.client_secret), timeout=TIMEOUT)
            if resp.status_code != 200:
                # never a per-number problem: stop the run, touch no partner
                raise BolagsverketError(f"token request refused ({resp.status_code})", resp.status_code, retry=True)
            body = resp.json()
            token, lifetime = body["access_token"], int(body.get("expires_in") or 3600)
        except BolagsverketError:
            raise
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            raise BolagsverketError(f"token request failed: {exc.__class__.__name__}", retry=True) from None
        with _token_lock:
            _token_cache[self._key] = (token, time.time() + lifetime)
        return token

    def _request(self, method, path, payload=None, raw=False):
        for attempt in (1, 2):
            headers = {"Authorization": "Bearer " + self._token(force=attempt == 2), "Accept": "application/json"}
            try:
                resp = self.session.request(method, self.base_url + path, json=payload, headers=headers,
                                            timeout=TIMEOUT)
            except requests.RequestException as exc:
                raise BolagsverketError(f"request failed: {exc.__class__.__name__}", retry=True) from None
            if resp.status_code == 401 and attempt == 1:
                continue  # token revoked or expired early: fetch a new one once
            break
        endpoint = path.split("/")[1]
        if resp.status_code >= 400:
            detail = ""
            try:
                body = resp.json()
                if isinstance(body, dict):
                    detail = str(body.get("detail") or body.get("title") or body.get("message") or "")
            except ValueError:
                pass
            raise BolagsverketError(f"{method} {endpoint} → {resp.status_code} {detail[:200]}".strip(),
                                    resp.status_code,
                                    retry=resp.status_code in (401, 429) or resp.status_code >= 500)
        if raw:
            return resp.content
        try:
            body = resp.json()
        except ValueError:
            raise BolagsverketError(f"{method} {endpoint}: answer is not JSON", resp.status_code, retry=True) from None
        if not isinstance(body, dict):
            raise BolagsverketError(f"{method} {endpoint}: unexpected answer", resp.status_code, retry=True)
        return body

    def is_alive(self):
        self._request("GET", "/isalive", raw=True)
        return True

    def organisations(self, identity):
        """Parsed organisations for a 10-digit number (see ``parse_answer``)."""
        body = self._request("POST", "/organisationer", {"identitetsbeteckning": request_identity(identity)})
        return parse_answer(body.get("organisationer"))

    def documents(self, identity):
        return self._request("POST", "/dokumentlista",
                             {"identitetsbeteckning": request_identity(identity)}).get("dokument") or []

    def document(self, document_id):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", document_id or ""):
            raise BolagsverketError("invalid document id")
        return self._request("GET", "/dokument/" + document_id, raw=True)
