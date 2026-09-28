"""Command line entry point. Exit 0 when every rule passes, 1 when any fails, 2 on bad input."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from collections import Counter
from pathlib import Path

from . import __version__
from .core import KINDS, Checker, read_rules

EPILOG = """\
examples:
  one-hop redirects.csv --base https://example.com
  one-hop redirects.csv --base https://staging.example.com --auth user:pass
  one-hop redirects.csv --base https://example.com --json report.json

The CSV has two columns, old URL then new URL. Leave the new URL empty for a
page that must answer 200 with no redirect. Docs:
https://synapsereality.io/open-source/one-hop/
"""


def _parse_header(raw: str) -> tuple[str, str]:
    name, sep, value = raw.partition(":")
    if not sep or not name.strip():
        raise argparse.ArgumentTypeError(f"expected 'Name: value', got {raw!r}")
    return name.strip(), value.strip()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="one-hop",
        description="Check that every old URL redirects to its new URL in one hop, and the new URL answers 200.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("csv", nargs="+", help="redirect map(s): old,new per line; '-' reads stdin")
    ap.add_argument("--base", required=True, help="site to test, e.g. https://example.com")
    ap.add_argument("--workers", type=int, default=8, help="parallel requests (default 8)")
    ap.add_argument("--timeout", type=float, default=20.0, help="seconds per request (default 20)")
    ap.add_argument("--max-hops", type=int, default=10, help="stop following a chain after this many hops")
    ap.add_argument("--limit", type=int, default=0, help="check only the first N rules")
    ap.add_argument("--auth", help="user:password for HTTP basic auth, sent to the base host only "
                    "(or set ONE_HOP_AUTH, which keeps it out of the process list)")
    ap.add_argument("--header", action="append", type=_parse_header, default=[],
                    help="extra request header 'Name: value' (base host only; repeatable)")
    ap.add_argument("--method", choices=("HEAD", "GET"), default="HEAD",
                    help="HEAD by default; falls back to GET on a 405 or 501")
    ap.add_argument("--allow-temporary", action="store_true", help="accept 302, 303 and 307 as the one hop")
    ap.add_argument("--path-only", action="store_true",
                    help="compare only path and query of the target, not scheme and host")
    ap.add_argument("--insecure", action="store_true", help="do not verify TLS certificates")
    ap.add_argument("--json", metavar="FILE", help="write every result as JSON ('-' for stdout)")
    ap.add_argument("--show", type=int, default=50, help="print at most N failures (default 50)")
    ap.add_argument("--version", action="version", version=f"one-hop {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rules = []
    try:
        for src in args.csv:
            text = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8-sig")
            rules += read_rules(text)
    except (OSError, ValueError) as e:
        print(f"one-hop: {e}", file=sys.stderr)
        return 2
    if args.limit:
        rules = rules[: args.limit]
    if not rules:
        print("one-hop: no rules found in the input", file=sys.stderr)
        return 2

    headers = dict(args.header)
    auth = args.auth or os.environ.get("ONE_HOP_AUTH")
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    try:
        checker = Checker(args.base, headers=headers, timeout=args.timeout, max_hops=args.max_hops,
                          allow_temporary=args.allow_temporary, path_only=args.path_only,
                          method=args.method, insecure=args.insecure)
    except ValueError as e:
        print(f"one-hop: {e}", file=sys.stderr)
        return 2

    quiet = args.json == "-"
    log = sys.stderr if quiet else sys.stdout
    print(f"checking {len(rules)} rules against {args.base}", file=log)
    results = checker.check_all(rules, workers=args.workers)
    bad = [r for r in results if not r.ok]
    bad.sort(key=lambda r: (KINDS.index(r.kind), r.rule.line))

    counts = Counter(r.kind for r in results)
    print(f"{len(results) - len(bad)}/{len(results)} OK", file=log)
    for kind in KINDS:
        if kind != "ok" and counts[kind]:
            print(f"  {kind}: {counts[kind]}", file=log)
    for r in bad[: args.show]:
        where = f"line {r.rule.line}: " if r.rule.line else ""
        print(f"FAIL [{r.kind}] {where}{r.rule.old}  {r.detail}", file=log)
    if len(bad) > args.show:
        print(f"... and {len(bad) - args.show} more (use --json for all)", file=log)

    if args.json:
        payload = json.dumps({"base": args.base, "total": len(results), "failed": len(bad),
                              "results": [r.as_dict() for r in results]}, indent=2)
        if quiet:
            print(payload)
        else:
            Path(args.json).write_text(payload + "\n", encoding="utf-8")
    return 1 if bad else 0
