"""Deterministic detector: settles what code can settle, with no model in the loop.

Arithmetic, calendar facts, whether a cited URL/DOI resolves, and whether a stdlib
name exists are checked by computation, over both the claim's restatement and its
verbatim anchor (a decomposer may reword "12 * 12 = 144" into words). Every other
claim gets NOT_FOUND with confidence 0, which is not support.

Support is deliberately narrow. Some checks settle only part of a claim: a resolving
link proves the reference exists, not what it says; a stdlib name existing says
nothing about its behaviour; consistent percent changes and year gaps do not prove
the figures. Such a *partial* check yields NOT_FOUND (with its computed evidence
attached as information) unless the claim is purely that check (see ``_is_pure``),
in which case it is SUPPORTED with reduced confidence. Contradictions always count:
one false part makes the claim false. Each check is wrapped: a failure becomes
UNVERIFIABLE, never support (P1).
"""

from __future__ import annotations

import asyncio
import calendar
import importlib
import ipaddress
import re
import socket
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import ModuleType
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx2

from saife.detectors.base import DetectorContext, enforce_evidence
from saife.evidence import computed
from saife.types import Claim, ClaimKind, DetectorName, DetectorVerdict, Evidence, Verdict

Resolver = Callable[[str], Awaitable[list[str]]]

NO_CHECK = "no deterministic check applies"
MAX_REFS_PER_CLAIM = 5
MAX_CONCURRENT_REQUESTS = 8
USER_AGENT = "saife-deterministic/0.1 (reference existence check)"

# Never import anything outside this set: importing runs module code.
CODE_API_ALLOWLIST = frozenset(
    {
        "json",
        "os",
        "os.path",
        "re",
        "math",
        "itertools",
        "functools",
        "collections",
        "datetime",
        "pathlib",
        "asyncio",
        "statistics",
        "random",
        "string",
        "typing",
        "dataclasses",
        "hashlib",
        "urllib.parse",
    }
)

# Full support only where the computation is the whole claim; partial where the
# claim also asserts real-world inputs (figures, years) that code cannot check.
FULL, PARTIAL, EXISTS = 1.0, 0.6, 0.5


@dataclass(frozen=True)
class _Finding:
    verdict: Verdict
    confidence: float
    rationale: str
    evidence: Evidence | None = None
    error: str | None = None
    # Set on support that covers only part of a claim: the fact the check established.
    partial: str | None = None


def _ok(check: str, detail: str, confidence: float, rationale: str | None = None) -> _Finding:
    return _Finding("SUPPORTED", confidence, rationale or detail, computed(check, detail))


def _part(check: str, fact: str, confidence: float, rationale: str, detail: str = "") -> _Finding:
    """Support for part of a claim; ``_settle`` keeps it only if the claim is pure."""
    ev = computed(check, detail or fact)
    return _Finding("SUPPORTED", confidence, rationale, ev, partial=fact)


def _wrong(check: str, detail: str, confidence: float = FULL) -> _Finding:
    return _Finding("CONTRADICTED", confidence, detail, computed(check, detail))


def _unchecked(error: str) -> _Finding:
    return _Finding("UNVERIFIABLE", 0.0, error, error=error)


def _no_evidence(rationale: str) -> _Finding:
    return _Finding("NOT_FOUND", 0.0, rationale)


# ---------------------------------------------------------------------------
# (a) Arithmetic
# ---------------------------------------------------------------------------

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_EQ = r"(?:=|\b(?:equals|is\s+equal\s+to|equal\s+to|is|makes|gives|comes\s+to)\b)"
_CUR = r"[$€£]?"
_OP_WORDS = r"multiplied\s+by|divided\s+by|times|plus|minus|x"
# A result followed by another operator is a chained expression: skipped, like a prefix.
_NOT_CHAINED = rf"(?!\s*(?:[+\-−*/÷×^]|\s(?:{_OP_WORDS})\s)\s*\d)"
_BINARY = re.compile(
    rf"(?<![\w.,])(?P<a>{_NUM})(?:\s*(?P<op>[+\-−*/÷×])\s*|\s+(?P<wop>{_OP_WORDS})\s+)"
    # The result must end the number: "is 3x faster" or "is 3.0.1" is not a result.
    rf"(?P<b>{_NUM})\s*{_EQ}\s*(?P<neg>[-−])?(?P<c>{_NUM})(?!\.?\d|[^\W\d_]){_NOT_CHAINED}",
    re.IGNORECASE,
)
_PERCENT_OF = re.compile(
    rf"(?<![\w.,])(?P<p>{_NUM})\s*(?:%|percent\b|per\s+cent\b)\s+of\s+{_CUR}(?P<base>{_NUM})"
    rf"\s*{_EQ}\s*{_CUR}(?P<c>{_NUM})(?!\.?\d)",
    re.IGNORECASE,
)
_CHANGE = re.compile(
    rf"\bfrom\s+{_CUR}(?P<a>{_NUM})\s*%?\s+to\s+{_CUR}(?P<b>{_NUM})\s*%?(?P<gap>[^\d%]{{0,40}}?)"
    rf"(?P<sign>[+\-−])?(?P<p>{_NUM})\s*(?:%|percent\b|per\s+cent\b)",
    re.IGNORECASE,
)
_UP = re.compile(
    r"\b(?:rise|rose|risen|increase[sd]?|grew|grown|growth|gain(?:ed|s)?|jump(?:ed|s)?"
    r"|climb(?:ed|s)?|went\s+up|go(?:es)?\s+up)\b",
    re.IGNORECASE,
)
_DOWN = re.compile(
    r"\b(?:fall|fell|fallen|drop(?:ped|s)?|decline[sd]?|decrease[sd]?|shr[aiu]nk|loss"
    r"|lost|went\s+down|go(?:es)?\s+down)\b",
    re.IGNORECASE,
)
_PRECEDING_OP = re.compile(rf"(?:[+\-−*/÷×^]|\s(?:{_OP_WORDS}))\s*$", re.IGNORECASE)
_WORD_OPS = {"x": "×", "times": "×", "multiplied by": "×", "divided by": "÷", "plus": "+"}
_WORD_OPS["minus"] = "−"

_OPS: dict[str, tuple[str, Callable[[float, float], float]]] = {
    "+": ("+", lambda a, b: a + b),
    "-": ("−", lambda a, b: a - b),
    "−": ("−", lambda a, b: a - b),
    "*": ("×", lambda a, b: a * b),
    "×": ("×", lambda a, b: a * b),
    "/": ("÷", lambda a, b: a / b),
    "÷": ("÷", lambda a, b: a / b),
}


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _tolerance(stated: str) -> float:
    """Half a unit in the last stated digit: '0.33' accepts 1/3, '48' rejects 49."""
    decimals = len(stated.split(".")[1]) if "." in stated else 0
    return 0.5 * 10.0**-decimals + 1e-9


def _fmt(x: float) -> str:
    return str(round(x)) if abs(x - round(x)) < 1e-9 else f"{x:.6g}"


def _arithmetic(text: str) -> list[_Finding]:
    out: list[_Finding] = []
    for m in _BINARY.finditer(text):
        if _PRECEDING_OP.search(text, 0, m.start()):
            continue  # part of a longer expression; precedence is out of scope
        sym, fn = _OPS[m["op"] or _WORD_OPS[" ".join(m["wop"].lower().split())]]
        a, b, c = _num(m["a"]), _num(m["b"]), _num(m["c"]) * (-1 if m["neg"] else 1)
        expr = f"{m['a']} {sym} {m['b']}"
        if sym == "÷" and b == 0:
            out.append(_wrong("arithmetic", f"{expr} is undefined (division by zero)"))
            continue
        result = fn(a, b)
        claimed = f"{'-' if m['neg'] else ''}{m['c']}"
        if abs(result - c) <= _tolerance(m["c"]):
            out.append(_ok("arithmetic", f"{expr} = {_fmt(result)}", FULL))
        else:
            out.append(_wrong("arithmetic", f"{expr} = {_fmt(result)}, claim says {claimed}"))
    for m in _PERCENT_OF.finditer(text):
        result = _num(m["p"]) / 100 * _num(m["base"])
        expr = f"{m['p']}% of {m['base']} = {_fmt(result)}"
        if abs(result - _num(m["c"])) <= _tolerance(m["c"]):
            out.append(_ok("arithmetic", expr, FULL))
        else:
            out.append(_wrong("arithmetic", f"{expr}, claim says {m['c']}"))
    for m in _CHANGE.finditer(text):
        out.extend(_percent_change(text, m))
    return out


def _percent_change(text: str, m: re.Match[str]) -> list[_Finding]:
    a, b, p = _num(m["a"]), _num(m["b"]), _num(m["p"])
    if a == 0:
        return []
    actual = (b - a) / a * 100
    window = text[max(0, m.start() - 40) : m.end() + 25]
    up = bool(_UP.search(window)) or m["sign"] == "+"
    down = bool(_DOWN.search(window)) or m["sign"] in ("-", "−")
    head = f"{m['a']}→{m['b']} is {actual:+.1f}%"
    if up != down and actual != 0 and (actual > 0) != up:
        said = f"a {m['p']}% {'rise' if up else 'fall'}"
        return [_wrong("arithmetic", f"{head}, claim says {said}")]
    if abs(abs(actual) - p) > _tolerance(m["p"]):
        return [_wrong("arithmetic", f"{head}, claim says {m['p']}%")]
    return [
        _part(
            "arithmetic",
            head,
            PARTIAL,
            f"{head} (the arithmetic is consistent; the figures themselves are not checked)",
        )
    ]


# ---------------------------------------------------------------------------
# (b) Dates
# ---------------------------------------------------------------------------

_MONTH_NAMES = tuple(calendar.month_name[1:])
_WEEKDAY_NAMES = tuple(calendar.day_name)  # Monday first, matching date.weekday()
_MONTH_ALIASES: dict[str, int] = {
    **{name.lower(): i for i, name in enumerate(_MONTH_NAMES, 1)},
    **{name[:3].lower(): i for i, name in enumerate(_MONTH_NAMES, 1)},
    "sept": 9,
}
_MONTH_RE = "|".join(sorted(_MONTH_ALIASES, key=len, reverse=True))
_WEEKDAY_RE = "|".join(_WEEKDAY_NAMES)
_ORD = r"(?:st|nd|rd|th)?"
_DATE = re.compile(
    rf"(?<![\w-])(?P<d1>\d{{1,2}}){_ORD}\s+(?:of\s+)?(?P<m1>{_MONTH_RE})\b\.?"
    rf"(?:,?\s+(?P<y1>\d{{4}})(?!\d))?"
    rf"|\b(?P<m2>{_MONTH_RE})\b\.?\s+(?P<d2>\d{{1,2}}){_ORD}(?!\w)(?:,?\s+(?P<y2>\d{{4}})(?!\d))?"
    rf"|(?<![\w-])(?P<y3>\d{{4}})-(?P<m3>\d{{2}})-(?P<d3>\d{{2}})(?![\w-])",
    re.IGNORECASE,
)
_WEEKDAY_AFTER = re.compile(
    r"\s*,?\s*(?:was|is|will\s+be|would\s+be|fell\s+on|falls\s+on|fall\s+on|landed\s+on"
    rf"|lands\s+on)\s+(?:on\s+)?(?:an?\s+)?(?P<w>{_WEEKDAY_RE})\b",
    re.IGNORECASE,
)
_WEEKDAY_BEFORE = re.compile(rf"\b(?P<w>{_WEEKDAY_RE}),?\s+(?:the\s+)?$", re.IGNORECASE)
_NEGATED = re.compile(r"\b(?:not|never)\b|n't\b", re.IGNORECASE)

_YEAR = r"(?<!\d)(?P<{}>\d{{4}})(?!\d)"
_BEFORE_WORDS = ("before", "earlier than", "prior to", "preceded", "predated", "predates")
_ORDERING = re.compile(
    rf"(?:\(|\bin\s+){_YEAR.format('a')}\)?(?P<gap>[^()\d]{{0,80}}?)"
    r"\b(?P<cmp>before|after|earlier\s+than|later\s+than|prior\s+to|preceded|predated|predates"
    r"|followed|postdated|postdates)\b"
    rf"(?P<gap2>[^()\d]{{0,80}}?)(?:\(|\bin\s+){_YEAR.format('b')}",
    re.IGNORECASE,
)
_YEARS_APART = re.compile(
    rf"{_YEAR.format('a')}(?P<gap>[^\d]{{0,60}}?)(?<!\d)(?P<n>\d{{1,4}})\s+years?\s+"
    r"(?P<cmp>after|before|later\s+than|earlier\s+than|prior\s+to)\b"
    rf"(?P<gap2>[^\d]{{0,60}}?){_YEAR.format('b')}",
    re.IGNORECASE,
)
_YEARS_BETWEEN = re.compile(
    r"(?<!\d)(?P<n>\d{1,4})\s+years?\s+(?:(?:have\s+|had\s+)?(?:passed|elapsed)\s+)?between\s+"
    rf"[^\d]{{0,40}}?{_YEAR.format('a')}[^\d]{{0,40}}?\band\b[^\d]{{0,40}}?{_YEAR.format('b')}"
    rf"|\bbetween\s+{_YEAR.format('a2')}\s+and\s+{_YEAR.format('b2')}[^\d]{{0,30}}?"
    r"(?<!\d)(?P<n2>\d{1,4})\s+years?\b",
    re.IGNORECASE,
)
_HEDGE = re.compile(
    r"\b(?P<h>about|around|roughly|nearly|almost|approximately|some|over|more\s+than"
    r"|less\s+than|under)\s*$",
    re.IGNORECASE,
)


def _days_in_month(year: int | None, month: int) -> int:
    return calendar.monthrange(year if year is not None else 2000, month)[1]  # 2000: leap


def _dates(text: str) -> list[_Finding]:
    out: list[_Finding] = []
    for m in _DATE.finditer(text):
        out.extend(_check_date(text, m))
    for m in _ORDERING.finditer(text):
        if _NEGATED.search(m["gap"] + " " + m["gap2"]):
            continue
        a, b = int(m["a"]), int(m["b"])
        if a == b:
            continue  # same year: order within it is unknowable from years alone
        cmp = " ".join(m["cmp"].lower().split())
        says_before = cmp in _BEFORE_WORDS
        detail = f"{a} is {'before' if a < b else 'after'} {b}"
        if (a < b) == says_before:
            out.append(_part("date", detail, PARTIAL, f"{detail} (the years are not checked)"))
        else:
            out.append(_wrong("date", f"{detail}, claim says {cmp}"))
    for m in _YEARS_APART.finditer(text):
        if _NEGATED.search(m["gap"] + " " + m["gap2"]):
            continue
        a, b, n = int(m["a"]), int(m["b"]), int(m["n"])
        cmp = " ".join(m["cmp"].lower().split())
        diff = a - b if cmp in ("after", "later than") else b - a
        out.append(_year_gap(text, m.start("n"), diff, n, f"{a} is {diff} years {cmp} {b}"))
    for m in _YEARS_BETWEEN.finditer(text):
        n_key = "n" if m["n"] is not None else "n2"
        a = int(m["a"] or m["a2"])
        b = int(m["b"] or m["b2"])
        n = int(m[n_key])
        diff = abs(b - a)
        out.append(_year_gap(text, m.start(n_key), diff, n, f"{a} to {b} is {diff} years"))
    return out


def _check_date(text: str, m: re.Match[str]) -> list[_Finding]:
    raw = m.group(0).strip().rstrip(",.")
    if m["y3"] is not None:
        year: int | None = int(m["y3"])
        month, day = int(m["m3"]), int(m["d3"])
        if year == 0:
            return []
        if not 1 <= month <= 12:
            return [_wrong("date", f"{raw} is not a valid date: there is no month {month}")]
    else:
        y = m["y1"] or m["y2"]
        year = int(y) if y else None
        month = _MONTH_ALIASES[(m["m1"] or m["m2"]).lower()]
        day = int(m["d1"] or m["d2"])
        if year == 0:
            return []
    name = _MONTH_NAMES[month - 1]
    limit = _days_in_month(year, month)
    if not 1 <= day <= limit:
        where = f"{name} {year}" if year is not None else name
        return [_wrong("date", f"{raw} is not a valid date: {where} has {limit} days")]
    if year is None:
        return []
    said = _WEEKDAY_AFTER.match(text, m.end())
    if said is None:
        said = _WEEKDAY_BEFORE.search(text[max(0, m.start() - 20) : m.start()])
    if said is None:
        return []
    actual = _WEEKDAY_NAMES[calendar.weekday(year, month, day)]
    detail = f"{day} {name} {year} was a {actual}"
    if said["w"].lower() == actual.lower():
        return [_ok("date", detail, FULL)]
    return [_wrong("date", f"{detail}, claim says {said['w'].capitalize()}")]


def _year_gap(text: str, n_start: int, diff: int, n: int, detail: str) -> _Finding:
    hedge = _HEDGE.search(text[max(0, n_start - 20) : n_start])
    h = " ".join(hedge["h"].lower().split()) if hedge else None
    if h in ("over", "more than"):
        ok, said = diff > n, f"{h} {n}"
    elif h in ("less than", "under"):
        ok, said = diff < n, f"{h} {n}"
    elif h is not None:
        ok, said = abs(diff - n) <= 1, f"{h} {n}"
    else:
        ok, said = diff == n, str(n)
    if ok:
        return _part("date", detail, PARTIAL, f"{detail} (the years are not checked)")
    return _wrong("date", f"{detail}, claim says {said}")


# ---------------------------------------------------------------------------
# (c) URLs and DOIs
# ---------------------------------------------------------------------------

_URL = re.compile(r"\bhttps?://[^\s<>\"'`]+", re.IGNORECASE)
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s<>\"'`]+")
_TRAILING = ".,;:!?'\"]}>"
_PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet")
# Hosts like 2130706433 or 0x7f.1 that resolvers read as IPv4 addresses.
_NUMERIC_HOST = re.compile(r"(?:0x[0-9a-f]*|\d+)(?:\.(?:0x[0-9a-f]*|\d+))*\.?", re.IGNORECASE)

ProbeOutcome = Literal["exists", "missing", "unreachable", "unchecked", "blocked"]


@dataclass(frozen=True)
class _Ref:
    check: Literal["url", "doi"]
    label: str  # what the claim cited, for messages
    target: str  # the URL actually requested


@dataclass(frozen=True)
class _Probe:
    outcome: ProbeOutcome
    detail: str


def _trim(s: str) -> str:
    """Drop sentence punctuation after a link; keep balanced parens (wiki-style URLs)."""
    while s and (s[-1] in _TRAILING or (s[-1] == ")" and s.count("(") < s.count(")"))):
        s = s[:-1]
    return s


def _extract_refs(text: str) -> tuple[list[_Ref], int]:
    """References in order of appearance, deduplicated and capped; returns (refs, dropped)."""
    refs: list[_Ref] = []
    spans: list[tuple[int, int]] = []
    for m in _URL.finditer(text):
        url = _trim(m.group(0))
        spans.append((m.start(), m.start() + len(url)))
        refs.append(_Ref("url", url, url))
    for m in _DOI.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue  # already part of a URL (e.g. https://doi.org/10....)
        doi = _trim(m.group(0))
        refs.append(_Ref("doi", f"DOI {doi}", f"https://doi.org/{quote(doi, safe='/:;()._-')}"))
    unique = list(dict.fromkeys(refs))
    return unique[:MAX_REFS_PER_CLAIM], max(0, len(unique) - MAX_REFS_PER_CLAIM)


def _static_block(url: str) -> str | None:
    """SSRF guard, before any network use. Returns the reason a URL must not be fetched."""
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return "malformed URL"
    if parts.scheme.lower() not in ("http", "https"):
        return "only http(s) URLs are fetched"
    if parts.username is not None or parts.password is not None:
        return "URLs with credentials are not fetched"
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        return "URL has no host"
    try:
        ipaddress.ip_address(host)
        return "IP-literal hosts are not fetched"
    except ValueError:
        pass
    if _NUMERIC_HOST.fullmatch(host):
        return "numeric hosts are not fetched"
    if host == "localhost" or host.endswith(_PRIVATE_SUFFIXES) or "." not in host:
        return f"{host} is not a public host"
    return None


def _is_public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def dns_resolve(host: str) -> list[str]:
    """Default resolver: every address the host resolves to (all must be public)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _classify(method: str, url: str, status: int) -> _Probe:
    detail = f"{method} {url} → HTTP {status}"
    if 200 <= status < 400:
        return _Probe("exists", detail)
    if status in (404, 410):
        return _Probe("missing", detail)
    if status == 429 or status >= 500:
        return _Probe("unchecked", f"{detail} (outage, not evidence)")
    return _Probe("unchecked", f"{detail} (inconclusive)")


def _ref_finding(ref: _Ref, probe: _Probe, kind: ClaimKind) -> _Finding:
    match probe.outcome:
        case "exists":
            return _part(
                ref.check,
                f"{ref.label} exists ({probe.detail})",
                EXISTS,
                f"{ref.label} exists ({probe.detail}); its content is not checked",
                probe.detail,
            )
        case "missing" | "unreachable":
            if kind == "citation":
                return _wrong(ref.check, f"cited {ref.label} does not resolve: {probe.detail}", 0.8)
            return _no_evidence(f"{ref.label} does not resolve: {probe.detail}")
        case "blocked":
            return _no_evidence(f"{ref.label} not fetched: {probe.detail}")
        case _:
            return _unchecked(f"{ref.label} could not be checked: {probe.detail}")


# ---------------------------------------------------------------------------
# (d) code_api
# ---------------------------------------------------------------------------

_DOTTED = re.compile(r"(?<![\w.])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_CODE_NEGATION = re.compile(r"\b(?:not|no|never|removed|deprecated|lacks?)\b|n't\b", re.IGNORECASE)


def _code_api(claim: Claim, text: str) -> list[_Finding]:
    if claim.kind != "code_api" or _CODE_NEGATION.search(text):
        return []
    version = f"Python {sys.version_info.major}.{sys.version_info.minor}"
    out: list[_Finding] = []
    for name in dict.fromkeys(m.group(0) for m in _DOTTED.finditer(text)):
        parts = name.split(".")
        split = next(
            (i for i in range(len(parts) - 1, 0, -1) if ".".join(parts[:i]) in CODE_API_ALLOWLIST),
            None,
        )
        if split is None:
            continue
        module_name = ".".join(parts[:split])
        if module_name not in CODE_API_ALLOWLIST:  # belt and braces: never import outside it
            continue
        obj: object = importlib.import_module(module_name)
        path = module_name
        for i, attr in enumerate(parts[split:]):
            if i and isinstance(obj, ModuleType) and obj.__name__ not in CODE_API_ALLOWLIST:
                break  # walked into a module we may not inspect further
            if not hasattr(obj, attr):
                out.append(_wrong("code_api", f"{path} has no attribute {attr!r} ({version})", 0.9))
                break
            obj = getattr(obj, attr)
            path = f"{path}.{attr}"
        else:
            detail = f"{name} exists ({version})"
            out.append(
                _part("code_api", detail, PARTIAL, f"{detail}; its behaviour is not checked")
            )
    return out


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

_SEVERITY: dict[Verdict, int] = {
    "CONTRADICTED": 0,
    "UNVERIFIABLE": 1,
    "NOT_FOUND": 2,
    "SUPPORTED": 3,
}


# Purity heuristic vocabulary. Function words carry no checkable content; check words
# only restate what a partial check computes (existence, a change, a span of years).
_STOPWORDS = frozenset(
    "a an the is are was were be been being it its this that these those of in on at to"
    " for from by with and or as than then which do does did has have had can will would"
    " also both each just".split()
)
_CHECK_WORDS = frozenset(
    # existence of a name or reference
    "exist exists existed existing available defined valid real function method class"
    " attribute module submodule constant object callable name python stdlib standard"
    " library builtin see link url doi reference resolves resolve online accessible live"
    # percent change
    " change changed changes percent cent rise rises rose risen increase increased"
    " increases grew grown growth gain gained gains jump jumped jumps climb climbed climbs"
    " went go goes up down fall falls fell fallen drop dropped drops decline declined"
    " declines decrease decreased decreases shrank shrunk shrink loss lost"
    # year ordering and gaps
    " year years before after earlier later prior preceded predated predates followed"
    " postdated postdates came come comes span between passed elapsed apart about around"
    " roughly nearly almost approximately some over more less under".split()
)
_WORD = re.compile(r"[^\W\d_]+")


def _is_pure(text: str) -> bool:
    """Whether the claim says nothing beyond what a partial check computes.

    Heuristic: remove URLs, DOIs and dotted API names (a name's parts become allowed
    words, so "json.loads is a function in the json module" stays pure), then every
    remaining word must be a stopword or a check word. Numbers and punctuation are
    what the checks read, so they are ignored. Any other word ("twice", "fast",
    "Smith", "parses") is content no check looked at, so the claim is not pure.
    """
    allowed = set(_STOPWORDS | _CHECK_WORDS)
    text = _DOI.sub(" ", _URL.sub(" ", text))
    for m in _DOTTED.finditer(text):
        allowed.update(part.lower() for part in m.group(0).split("."))
    text = _DOTTED.sub(" ", text)
    return all(w in allowed for w in _WORD.findall(text.lower()))


def _settle(findings: list[_Finding], pure: bool) -> list[_Finding]:
    """A partial check supports only a pure claim; otherwise it is information, not support."""
    return [
        _Finding(
            "NOT_FOUND",
            0.0,
            f"{f.partial}; the rest of the claim is not checked",
            f.evidence,
        )
        if f.partial is not None and f.verdict == "SUPPORTED" and not pure
        else f
        for f in findings
    ]


_NUMBER = re.compile(rf"(?:{_NUM})")
_FIGURE_CHECKS = frozenset({"deterministic:arithmetic", "deterministic:date"})


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def _relevant_to(claim_text: str, finding: _Finding) -> bool:
    """An anchor may carry several claims (claims from one sentence share it), so a
    computed finding from the anchor counts only if it shares a figure with this claim."""
    if finding.evidence is None or finding.evidence.source_id not in _FIGURE_CHECKS:
        return True  # errors are never hidden; code_api only runs on code_api claims
    figures = _numbers(finding.evidence.quote)
    return not figures or bool(figures & _numbers(claim_text))


def _unique(findings: list[_Finding]) -> list[_Finding]:
    """The same check firing on the restatement and the anchor is one finding."""
    seen: dict[tuple[str, str], _Finding] = {}
    for f in findings:
        seen.setdefault((f.verdict, f.rationale), f)
    return list(seen.values())


def _combine(claim: Claim, findings: list[_Finding]) -> DetectorVerdict:
    """Worst finding wins: one refuted part refutes the claim, and an unchecked or
    unsupported part keeps the rest from reading as support."""
    if not findings:
        return DetectorVerdict(
            detector="deterministic",
            claim_id=claim.id,
            verdict="NOT_FOUND",
            confidence=0.0,
            support_kind="computed",
            rationale=NO_CHECK,
        )
    verdict = min((f.verdict for f in findings), key=_SEVERITY.__getitem__)
    winners = [f for f in findings if f.verdict == verdict]
    if verdict == "CONTRADICTED":
        confidence = max(f.confidence for f in winners)
    elif verdict == "SUPPORTED":
        confidence = min(f.confidence for f in winners)
    else:
        confidence = 0.0
    errors = [f.error for f in winners if f.error]
    return DetectorVerdict(
        detector="deterministic",
        claim_id=claim.id,
        verdict=verdict,
        confidence=confidence,
        support_kind="computed",
        evidence=[f.evidence for f in winners if f.evidence is not None],
        rationale="; ".join(f.rationale for f in findings),
        error="; ".join(errors) if errors else None,
    )


def _guarded(name: str, fn: Callable[..., list[_Finding]], *args: object) -> list[_Finding]:
    try:
        return fn(*args)
    except Exception as exc:
        return [_unchecked(f"{name} check failed: {type(exc).__name__}: {exc}")]


class DeterministicDetector:
    name: DetectorName = "deterministic"

    def __init__(
        self,
        http_client: httpx2.AsyncClient | None = None,
        *,
        timeout: float = 5.0,
        resolver: Resolver | None = None,
    ) -> None:
        self._http = http_client
        self._timeout = timeout
        self._resolve: Resolver = resolver or dns_resolve

    def applies(self, ctx: DetectorContext) -> bool:
        return True

    async def check(self, claims: list[Claim], ctx: DetectorContext) -> list[DetectorVerdict]:
        try:
            return await self._check(claims)
        except Exception as exc:
            error = f"deterministic detector failed: {type(exc).__name__}: {exc}"
            return [_combine(c, [_unchecked(error)]) for c in claims]

    async def _check(self, claims: list[Claim]) -> list[DetectorVerdict]:
        findings: list[list[_Finding]] = []
        refs: list[list[_Ref]] = []
        for claim in claims:
            found: list[_Finding] = []
            texts = [claim.text]
            if claim.anchor.strip() and claim.anchor.strip() != claim.text.strip():
                texts.append(claim.anchor)
            for i, text in enumerate(texts):
                here = [
                    *_guarded("arithmetic", _arithmetic, text),
                    *_guarded("date", _dates, text),
                    *_guarded("code_api", _code_api, claim, text),
                ]
                found += here if i == 0 else [f for f in here if _relevant_to(claim.text, f)]
            # The anchor's links count only for a citation; otherwise they may be another
            # claim's reference from the same sentence.
            ref_text = " ".join(texts) if claim.kind == "citation" else claim.text
            try:
                claim_refs, dropped = _extract_refs(ref_text)
            except Exception as exc:
                claim_refs, dropped = [], 0
                found.append(_unchecked(f"reference extraction failed: {exc}"))
            if dropped:
                found.append(
                    _no_evidence(
                        f"{dropped} more reference(s) not checked (limit {MAX_REFS_PER_CLAIM})"
                    )
                )
            findings.append(found)
            refs.append(claim_refs)

        probes = await self._probe_all(list(dict.fromkeys(r.target for rs in refs for r in rs)))
        verdicts: list[DetectorVerdict] = []
        for claim, found, rs in zip(claims, findings, refs, strict=True):
            all_found = [*found, *(_ref_finding(r, probes[r.target], claim.kind) for r in rs)]
            settled = _settle(_unique(all_found), _is_pure(claim.text))
            verdicts.append(enforce_evidence(_combine(claim, settled)))
        return verdicts

    async def _probe_all(self, targets: list[str]) -> dict[str, _Probe]:
        if not targets:
            return {}
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

        async def limited(url: str, client: httpx2.AsyncClient) -> _Probe:
            async with sem:
                return await self._probe(url, client)

        async with self._client() as client:
            results = await asyncio.gather(*(limited(t, client) for t in targets))
        return dict(zip(targets, results, strict=True))

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[httpx2.AsyncClient]:
        if self._http is not None:
            yield self._http
            return
        async with httpx2.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
            yield client

    async def _probe(self, url: str, client: httpx2.AsyncClient) -> _Probe:
        """Never raises. Redirects are not followed: a 3xx already shows the reference
        exists, and following one could reach a private host the guard never saw."""
        reason = _static_block(url)
        if reason is not None:
            return _Probe("blocked", reason)
        try:
            async with asyncio.timeout(self._timeout * 2 + 1):
                return await self._request(url, client)
        except TimeoutError:
            return _Probe("unchecked", f"timed out after {self._timeout:g}s (outage, not evidence)")
        except Exception as exc:
            return _Probe("unchecked", f"unexpected error: {type(exc).__name__}: {exc}")

    async def _request(self, url: str, client: httpx2.AsyncClient) -> _Probe:
        host = urlsplit(url).hostname or ""
        try:
            addrs = await self._resolve(host)
        except (OSError, UnicodeError):
            return _Probe("unreachable", f"DNS lookup for {host} failed")
        if not addrs:
            return _Probe("unreachable", f"DNS lookup for {host} returned no address")
        private = [a for a in addrs if not _is_public(a)]
        if private:
            return _Probe("blocked", f"{host} resolves to a non-public address ({private[0]})")
        headers = {"User-Agent": USER_AGENT}
        method = "HEAD"
        try:
            resp = await client.head(
                url, headers=headers, timeout=self._timeout, follow_redirects=False
            )
            status = resp.status_code
            if status == 405:
                method = "GET"
                async with client.stream(
                    "GET", url, headers=headers, timeout=self._timeout, follow_redirects=False
                ) as streamed:
                    status = streamed.status_code
        except httpx2.TimeoutException:
            return _Probe("unchecked", f"{method} {url} timed out (outage, not evidence)")
        except httpx2.ConnectError:
            return _Probe("unreachable", f"{method} {url}: connection to {host} failed")
        except httpx2.HTTPError as exc:
            return _Probe("unchecked", f"{method} {url} failed: {type(exc).__name__}")
        return _classify(method, url, status)
