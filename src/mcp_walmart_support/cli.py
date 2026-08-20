"""Command line entry point.

Reading and filing support cases from a shell, so the flow does not depend on
driving a browser. Case creation lands once its Aura payload is captured.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from .aura import AuraError
from .auth import authenticated_session, build_client, check_auth, login
from .cases import ACTIVITY_PAGE, Case, fetch_cases, filter_cases, find_case
from .config import CONFIG_PATH, Config, load_config


def _print_fields(payload: dict[str, object]) -> None:
    width = max(len(k) for k in payload)
    for key, value in payload.items():
        print(f"{key.replace('_', ' '):<{width}}  {value}")


def _print_table(cases: Sequence[Case]) -> None:
    if not cases:
        print("no matching cases")
        return
    status_width = max(len(c.status) for c in cases)
    for case in cases:
        day = case.created_date[:10]
        subject = case.subject if len(case.subject) <= 62 else case.subject[:59] + "..."
        flag = f" [{case.attachment_number}]" if case.attachment_number else ""
        print(f"{case.case_number}  {day}  {case.status:<{status_width}}  {subject}{flag}")
    print(f"\n{len(cases)} case(s)")


def _parse_since(value: str) -> date:
    """Accept an ISO date or a day offset such as ``7d``."""
    if value.endswith("d") and value[:-1].isdigit():
        return date.today() - timedelta(days=int(value[:-1]))
    return datetime.strptime(value, "%Y-%m-%d").date()


def _cmd_auth_check(cfg: Config, args: argparse.Namespace) -> int:
    client = build_client(cfg)
    try:
        state = check_auth(client)
        source = "cookie" if cfg.has_cookie else "none"
        if not state.authenticated and cfg.has_credentials:
            state = login(client, cfg)
            source = "login"
        payload = {
            "authenticated": state.authenticated,
            "auth_source": source,
            "username": cfg.username or "(not configured)",
            "base_url": cfg.base_url,
        }
        print(json.dumps(payload, indent=2)) if args.json else _print_fields(payload)
        return 0 if state.authenticated else 1
    finally:
        client.close()


def _cmd_cases_list(cfg: Config, args: argparse.Namespace) -> int:
    client, session = authenticated_session(cfg, ACTIVITY_PAGE)
    try:
        cases = filter_cases(
            fetch_cases(session),
            status=args.status,
            since=_parse_since(args.since) if args.since else None,
            query=args.query,
        )
        if args.limit:
            cases = cases[: args.limit]
        if args.json:
            print(json.dumps([c.as_dict() for c in cases], indent=2))
        else:
            _print_table(cases)
        return 0
    finally:
        client.close()


def _cmd_cases_get(cfg: Config, args: argparse.Namespace) -> int:
    client, session = authenticated_session(cfg, ACTIVITY_PAGE)
    try:
        case = find_case(fetch_cases(session), args.case_number)
        if case is None:
            print(f"case {args.case_number} not found", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(case.as_dict(), indent=2))
        else:
            payload = case.as_dict()
            description = str(payload.pop("description", ""))
            _print_fields(payload)
            if description:
                print("\ndescription\n")
                print(description)
        return 0
    finally:
        client.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="walmart-case",
        description="Read and file Walmart Connect advertising support cases.",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--config", default=None, help=f"config file (default: {CONFIG_PATH})")
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="authentication helpers").add_subparsers(
        dest="auth_command", required=True
    )
    auth.add_parser("check", help="report whether the portal session is authenticated")

    cases = sub.add_parser("cases", help="read support cases").add_subparsers(
        dest="cases_command", required=True
    )
    listing = cases.add_parser("list", help="list cases, newest first")
    listing.add_argument("--status", help="substring match on status, e.g. 'need info'")
    listing.add_argument("--since", help="ISO date or day offset such as 30d")
    listing.add_argument("--query", help="substring match on subject or description")
    listing.add_argument("--limit", type=int, default=0, help="show at most N cases")

    detail = cases.add_parser("get", help="show one case")
    detail.add_argument("case_number")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = load_config(Path(args.config) if args.config else CONFIG_PATH)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    handlers = {
        ("auth", "check"): _cmd_auth_check,
        ("cases", "list"): _cmd_cases_list,
        ("cases", "get"): _cmd_cases_get,
    }
    key = (str(args.command), str(getattr(args, f"{args.command}_command", "")))
    handler = handlers.get(key)
    if handler is None:
        parser.error(f"unhandled command: {' '.join(str(k) for k in key if k)}")

    try:
        return handler(cfg, args)
    except BrokenPipeError:
        # Piping into head/less closes stdout early; exit quietly instead of
        # letting the interpreter report the failed flush at shutdown.
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except AuraError as exc:
        print(f"portal error: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"network error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
