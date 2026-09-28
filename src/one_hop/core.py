"""Check that every old URL reaches its new URL in exactly one redirect.

A migration map is only right when each old URL answers with one permanent
redirect straight to its new URL, and that new URL answers 200 itself. A chain
(old -> /tmp -> new) still "works" in a browser, so nobody notices it, but each
extra hop costs crawl budget and can drop signals. This module follows every
hop by hand and says exactly which rule broke and how.
"""

from __future__ import annotations

import csv
import http.client
import io
import ssl
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

__all__ = ["Rule", "Hop", "Result", "Checker", "read_rules", "normalise"]

USER_AGENT = "one-hop/0.1 (+https://synapsereality.io/open-source/one-hop/)"
PERMANENT = (301, 308)
TEMPORARY = (302, 303, 307)
GONE = (404, 410)

# Result kinds, in the order a report lists them.
OK = "ok"
KINDS = ("error", "loop", "not_found", "no_redirect", "temporary", "wrong_target",
         "chain", "bad_status", "redirected", OK)


@dataclass(frozen=True)
class Rule:
    old: str
    new: str  # "" means "this URL must answer 200 itself, with no redirect"
    line: int = 0


@dataclass(frozen=True)
class Hop:
    url: str
    status: int
    location: str | None = None


@dataclass
class Result:
    rule: Rule
    kind: str
    detail: str
    hops: list[Hop] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.kind == OK

    def as_dict(self) -> dict:
        return {
            "old": self.rule.old,
            "new": self.rule.new,
            "line": self.rule.line,
            "ok": self.ok,
            "kind": self.kind,
            "detail": self.detail,
            "hops": [{"url": h.url, "status": h.status, "location": h.location} for h in self.hops],
        }


_OLD_NAMES = ("old", "source", "from", "old_url", "source_url")
_NEW_NAMES = ("new", "destination", "to", "target", "new_url", "destination_url")


def read_rules(text: str) -> list[Rule]:
    """Parse a redirect map. Two columns, old then new.

    A header row is optional. When there is one, the columns can be called
    old/new, source/destination or from/to, in any order, and other columns are
    ignored. Blank lines and lines starting with `#` are skipped.
    """
    rows = [
        (i, r) for i, r in enumerate(csv.reader(io.StringIO(text)), start=1)
        if r and any(c.strip() for c in r) and not r[0].lstrip().startswith("#")
    ]
    if not rows:
        return []
    old_i, new_i = 0, 1
    head = [c.strip().lower() for c in rows[0][1]]
    if any(h in _OLD_NAMES for h in head):
        old_i = next(i for i, h in enumerate(head) if h in _OLD_NAMES)
        new_i = next((i for i, h in enumerate(head) if h in _NEW_NAMES), None)
        if new_i is None:
            raise ValueError("the header names an old-URL column but no new-URL column")
        rows = rows[1:]
    rules = []
    for line, r in rows:
        old = r[old_i].strip() if len(r) > old_i else ""
        new = r[new_i].strip() if len(r) > new_i else ""
        if not old:
            raise ValueError(f"line {line}: no old URL")
        rules.append(Rule(old=old, new=new, line=line))
    return rules


def normalise(url: str) -> str:
    """One spelling per URL: lower-case scheme and host, no default port, `/` for an empty path.

    The path, query and trailing slash are kept as they are, because the server
    treats `/a` and `/a/` as two URLs and a redirect between them is a real hop.
    """
    p = urlsplit(url)
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    port = p.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    return urlunsplit((scheme, host, p.path or "/", p.query, ""))


def _origin(url: str) -> str:
    p = urlsplit(normalise(url))
    return f"{p.scheme}://{p.netloc}"


class Checker:
    """Checks rules against one site. Thread-safe: every request opens its own connection."""

    def __init__(
        self,
        base: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
        max_hops: int = 10,
        allow_temporary: bool = False,
        path_only: bool = False,
        method: str = "HEAD",
        insecure: bool = False,
    ) -> None:
        if not urlsplit(base).scheme or not urlsplit(base).netloc:
            raise ValueError(f"base must be an absolute URL such as https://example.com, got {base!r}")
        self.base = base
        self.origin = _origin(base)
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.max_hops = max_hops
        self.allow_temporary = allow_temporary
        self.path_only = path_only
        self.method = method.upper()
        self._ssl = ssl._create_unverified_context() if insecure else ssl.create_default_context()  # noqa: S323

    # ------------------------------------------------------------------ http ---
    def request(self, url: str, method: str | None = None) -> Hop:
        """One request. Redirects are never followed here: every hop has to be seen."""
        method = (method or self.method).upper()
        p = urlsplit(url)
        if p.scheme == "https":
            conn = http.client.HTTPSConnection(p.hostname, p.port, timeout=self.timeout, context=self._ssl)
        elif p.scheme == "http":
            conn = http.client.HTTPConnection(p.hostname, p.port, timeout=self.timeout)
        else:
            raise ValueError(f"unsupported URL scheme in {url!r}")
        path = (p.path or "/") + (f"?{p.query}" if p.query else "")
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        # Extra headers usually carry credentials for a staging host. They go to
        # that host only, never to a host a redirect points at.
        if _origin(url) == self.origin:
            headers.update(self.headers)
        try:
            conn.request(method, path, headers=headers)
            r = conn.getresponse()
            status, location = r.status, r.getheader("Location")
        finally:
            conn.close()
        # Some servers refuse HEAD. Ask again with GET rather than report their 405.
        if method == "HEAD" and status in (405, 501):
            return self.request(url, "GET")
        return Hop(url=url, status=status, location=location)

    def follow(self, start: str) -> tuple[list[Hop], str]:
        """Follow redirects by hand from `start`. Returns the hops and an end state:
        "final" (a non-redirect answer), "loop", "too_many" or "no_location"."""
        hops: list[Hop] = []
        seen: set[str] = set()
        url = start
        while True:
            key = normalise(url)
            if key in seen:
                return hops, "loop"
            seen.add(key)
            if len(hops) > self.max_hops:
                return hops, "too_many"
            hop = self.request(url)
            hops.append(hop)
            if not (300 <= hop.status < 400):
                return hops, "final"
            if not hop.location:
                return hops, "no_location"
            url = urljoin(url, hop.location)

    # ----------------------------------------------------------------- check ---
    def _same_target(self, got: str, want: str) -> bool:
        if self.path_only:
            g, w = urlsplit(normalise(got)), urlsplit(normalise(want))
            return (g.path, g.query) == (w.path, w.query)
        return normalise(got) == normalise(want)

    def check(self, rule: Rule) -> Result:
        src = urljoin(self.base, rule.old)
        want = urljoin(self.base, rule.new) if rule.new else ""
        if want and normalise(want) == normalise(src):
            want = ""
        try:
            hops, end = self.follow(src)
        except Exception as e:  # noqa: BLE001 - any network failure is a result, not a crash
            return Result(rule, "error", f"request failed: {type(e).__name__}: {e}")
        first, last = hops[0], hops[-1]
        chain = " -> ".join(f"{h.status} {h.url}" for h in hops)

        if end == "loop":
            return Result(rule, "loop", f"redirect loop: {chain} -> {hops[-1].location}", hops)
        if end == "too_many":
            return Result(rule, "chain", f"more than {self.max_hops} redirects: {chain}", hops)
        if end == "no_location":
            return Result(rule, "bad_status", f"{last.status} with no Location header at {last.url}", hops)

        if not want:
            if first.status == 200:
                return Result(rule, OK, "200", hops)
            if first.status in GONE:
                return Result(rule, "not_found", f"expected 200, got {first.status}", hops)
            if 300 <= first.status < 400:
                return Result(rule, "redirected", f"expected 200 with no redirect: {chain}", hops)
            return Result(rule, "bad_status", f"expected 200, got {first.status}", hops)

        if first.status in GONE:
            return Result(rule, "not_found", f"old URL returned {first.status}, no redirect", hops)
        if first.status == 200:
            return Result(rule, "no_redirect", "old URL returned 200, no redirect", hops)
        if first.status in TEMPORARY and not self.allow_temporary:
            return Result(rule, "temporary", f"{first.status} is a temporary redirect, want 301 or 308", hops)
        if first.status not in PERMANENT + TEMPORARY:
            return Result(rule, "bad_status", f"expected 301 or 308, got {first.status}", hops)

        target = urljoin(first.url, first.location or "")
        if not self._same_target(target, want):
            if len(hops) > 2 and self._same_target(last.url, want):
                return Result(rule, "chain", f"reaches the new URL after {len(hops) - 1} redirects: {chain}",
                              hops)
            return Result(rule, "wrong_target", f"redirects to {target}, want {want} ({chain})", hops)
        if len(hops) > 2:
            return Result(rule, "chain", f"{len(hops) - 1} redirects before a final answer: {chain}", hops)
        if last.status in GONE:
            return Result(rule, "not_found", f"new URL returned {last.status}", hops)
        if last.status != 200:
            return Result(rule, "bad_status", f"new URL returned {last.status}", hops)
        return Result(rule, OK, f"{first.status} -> 200", hops)

    def check_all(self, rules: list[Rule], workers: int = 8) -> list[Result]:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            return list(pool.map(self.check, rules))
