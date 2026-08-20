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
from .auth import (
    SESSION_PATH,
    build_client,
    check_auth,
    clear_session,
    load_session,
    login,
    save_session,
    with_session,
)
from .cases import ACTIVITY_PAGE, Case, fetch_cases, filter_cases, find_case
from .config import CONFIG_PATH, Config, load_config
from .create import (
    CONTACT_PAGE,
    PLATFORMS,
    CaseDraft,
    Identity,
    fetch_category_tree,
    prepare,
    resolve_platform,
    submit,
)


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
    cached = load_session()
    client = build_client(cfg, cached)
    try:
        state = check_auth(client)
        source = "cache" if cached else "none"
        if not state.authenticated and cfg.has_credentials:
            state = login(client, cfg)
            source = "login"
            if state.authenticated:
                save_session(client)
        payload = {
            "authenticated": state.authenticated,
            "auth_source": source,
            "username": cfg.username or "(not configured)",
            "base_url": cfg.base_url,
            "session_cache": str(SESSION_PATH),
        }
        print(json.dumps(payload, indent=2)) if args.json else _print_fields(payload)
        return 0 if state.authenticated else 1
    finally:
        client.close()


def _cmd_auth_logout(cfg: Config, args: argparse.Namespace) -> int:
    removed = clear_session()
    print("cached session discarded" if removed else "no cached session")
    return 0


def _cmd_cases_list(cfg: Config, args: argparse.Namespace) -> int:
    cases = filter_cases(
        with_session(cfg, ACTIVITY_PAGE, fetch_cases),
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


def _cmd_cases_get(cfg: Config, args: argparse.Namespace) -> int:
    case = find_case(with_session(cfg, ACTIVITY_PAGE, fetch_cases), args.case_number)
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


def _cmd_categories_list(cfg: Config, args: argparse.Namespace) -> int:
    """Show the support categories the portal's own dropdown offers."""
    ad_unit = resolve_platform(args.platform)

    def run(session: object) -> list[tuple[object, list[object]]]:
        identity = Identity.fetch(session)  # type: ignore[arg-type]
        return fetch_category_tree(  # type: ignore[arg-type,return-value]
            session,  # type: ignore[arg-type]
            ad_unit=ad_unit,
            partner_channel=identity.user_type,
        )

    tree = with_session(cfg, CONTACT_PAGE, run)  # type: ignore[arg-type]

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "category": l1.label,  # type: ignore[attr-defined]
                        "issues": [c.label for c in kids],  # type: ignore[attr-defined]
                    }
                    for l1, kids in tree
                ],
                indent=2,
            )
        )
        return 0

    print(f"support categories for {ad_unit}:\n")
    for l1, kids in tree:
        print(f"  {l1.label}")  # type: ignore[attr-defined]
        for child in kids:
            fields = len(child.form_fields)  # type: ignore[attr-defined]
            extra = f"  ({fields} form fields)" if fields else ""
            print(f"     - {child.label}{extra}")  # type: ignore[attr-defined]
    return 0


def _cmd_case_create(cfg: Config, args: argparse.Namespace) -> int:
    description = Path(args.description_file).read_text()
    draft = CaseDraft(
        subject=args.subject,
        description=description,
        advertisers=args.advertisers or "",
        category=args.category,
        issue=args.issue,
        platform=args.platform,
    )

    def run(session: object) -> dict[str, object]:
        payload = prepare(session, draft)  # type: ignore[arg-type]
        if not args.submit:
            return {"dry_run": payload}
        return {"filed": submit(session, payload)}  # type: ignore[arg-type]

    result = with_session(cfg, CONTACT_PAGE, run)  # type: ignore[arg-type]

    if "dry_run" in result:
        payload = result["dry_run"]
        print(json.dumps(payload, indent=2) if args.json else _describe_payload(payload))
        # flush first so the notice lands after the payload, not interleaved
        sys.stdout.flush()
        print("\nnothing was filed. Re-run with --submit to file this case.", file=sys.stderr)
        return 0

    filed = result["filed"]
    print(json.dumps(filed, indent=2) if args.json else _describe_payload(filed))
    return 0


def _describe_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    interesting = {k: v for k, v in payload.items() if v not in ("", [], False, None)}
    width = max((len(k) for k in interesting), default=0)
    lines = []
    for key, value in interesting.items():
        text = str(value)
        if len(text) > 70:
            text = text[:67] + "..."
        lines.append(f"{key:<{width}}  {text}")
    return "\n".join(lines)


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
    auth.add_parser("logout", help="discard the cached session")

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

    categories = sub.add_parser(
        "categories", help="list the portal's support categories"
    ).add_subparsers(dest="categories_command", required=True)
    cat_list = categories.add_parser("list", help="show categories and their issues")
    cat_list.add_argument(
        "--platform", default="display", choices=sorted(PLATFORMS), help="default: display"
    )

    case = sub.add_parser("case", help="file a support case").add_subparsers(
        dest="case_command", required=True
    )
    create = case.add_parser(
        "create",
        help="file a case (prints the payload unless --submit is given)",
    )
    create.add_argument("--subject", required=True)
    create.add_argument(
        "--description-file",
        required=True,
        help="file holding the case body; keeps long payloads out of the shell",
    )
    create.add_argument("--advertisers", help="advertiser/profile ids affected")
    create.add_argument("--category", default="API", help="level-1 category (default: API)")
    create.add_argument(
        "--issue",
        default="Endpoint-specific problem",
        help="level-2 issue (default: Endpoint-specific problem)",
    )
    create.add_argument(
        "--platform",
        default="display",
        choices=sorted(PLATFORMS),
        help="which ad platform the case is about (default: display)",
    )
    create.add_argument(
        "--submit",
        action="store_true",
        help="actually file the case; without it the payload is only printed",
    )
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
        ("auth", "logout"): _cmd_auth_logout,
        ("cases", "list"): _cmd_cases_list,
        ("cases", "get"): _cmd_cases_get,
        ("case", "create"): _cmd_case_create,
        ("categories", "list"): _cmd_categories_list,
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
