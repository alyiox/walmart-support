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
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import httpx

from .attachments import Upload, upload_file
from .aura import AuraError, AuraSession, SessionExpired
from .auth import (
    AuthState,
    build_client,
    check_auth,
    clear_session,
    load_session,
    login,
    save_session,
    session_path,
    with_session,
)
from .cases import (
    ACTIVITY_PAGE,
    COMMENT_MAX_CHARS,
    Case,
    TooManyCandidates,
    close_case,
    deep_filter,
    fetch_case_detail,
    fetch_cases,
    filter_cases,
    post_comment,
    pushdown_limit,
    rendered_length,
)
from .config import CONFIG_PATH, Config, load_config
from .create import (
    CONTACT_PAGE,
    CaseDraft,
    Category,
    Identity,
    Submission,
    fetch_category_tree,
    prepare,
    submit,
)
from .portals import PORTALS, PortalError


def _version() -> str:
    """Report the installed version.

    Read from package metadata rather than a constant, so a ``uvx`` run
    reports the build it actually resolved.
    """
    try:
        return version("walmart-support")
    except PackageNotFoundError:  # pragma: no cover - running from a source tree
        return "unknown"


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
        flag = f" [{case.attachments}]" if case.attachments else ""
        print(f"{case.case_number}  {day}  {case.status:<{status_width}}  {subject}{flag}")
    print(f"\n{len(cases)} case(s)")


def _parse_since(value: str) -> date:
    """Accept an ISO date or a day offset such as ``7d``."""
    if value.endswith("d") and value[:-1].isdigit():
        return date.today() - timedelta(days=int(value[:-1]))
    return datetime.strptime(value, "%Y-%m-%d").date()


def _cmd_auth_check(cfg: Config, args: argparse.Namespace) -> int:
    cache = session_path(cfg.base_url)
    cached = load_session(cache)
    client = build_client(cfg, cached)
    try:
        state = check_auth(client)
        source = "cache" if cached else "none"
        if not state.authenticated and cfg.has_credentials:
            source = "login"
            try:
                state = login(client, cfg)
            except SessionExpired as exc:
                # This is the command that diagnoses a broken login, so report
                # why it broke instead of failing like any other command.
                state = AuthState(authenticated=False, error=str(exc))
            if state.authenticated:
                save_session(client, cache)
        payload: dict[str, object] = {
            "authenticated": state.authenticated,
            "auth_source": source,
            "portal": cfg.portal.key,
            "username": cfg.username or "(not configured)",
            "base_url": cfg.base_url,
            "session_cache": str(cache),
            "error": state.error,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            _print_fields({k: v for k, v in payload.items() if k != "error" or v})
        return 0 if state.authenticated else 1
    finally:
        client.close()


def _cmd_auth_logout(cfg: Config, args: argparse.Namespace) -> int:
    removed = clear_session(session_path(cfg.base_url))
    label = cfg.portal.label
    print(f"{label}: cached session discarded" if removed else f"{label}: no cached session")
    return 0


def _cmd_cases_list(cfg: Config, args: argparse.Namespace) -> int:
    since = _parse_since(args.since) if args.since else None
    filtered = bool(args.status or since or args.query)

    def run(session: AuraSession) -> list[Case]:
        cases = fetch_cases(session, pushdown_limit(args.limit, filtered=filtered))
        narrowed = filter_cases(
            cases,
            status=args.status,
            since=since,
            # a deep search must not pre-filter on the abbreviated text
            query=None if args.deep else args.query,
        )
        if args.deep and args.query:
            if args.limit:
                narrowed = narrowed[: args.limit]
            return deep_filter(session, narrowed, args.query)
        return narrowed

    cases = with_session(cfg, ACTIVITY_PAGE, run)
    if args.limit:
        cases = cases[: args.limit]
    if args.json:
        print(json.dumps([c.as_dict() for c in cases], indent=2))
    else:
        _print_table(cases)
    return 0


def _cmd_cases_get(cfg: Config, args: argparse.Namespace) -> int:
    detail = with_session(cfg, ACTIVITY_PAGE, lambda s: fetch_case_detail(s, args.case_number))
    if args.json:
        print(json.dumps({"portal": cfg.portal.key} | detail.as_dict(), indent=2))
        return 0

    payload = detail.as_dict()
    description = str(payload.pop("description", ""))
    payload.pop("comments", None)
    extras = payload.pop("additional_fields", {}) or {}
    _print_fields({**payload, **{f"field: {k}": v for k, v in extras.items()}})
    if description:
        print("\ndescription\n")
        print(description)
    if detail.comments:
        print(f"\n{len(detail.comments)} reply/replies — see: cases replies {detail.case_number}")
    return 0


def _cmd_cases_replies(cfg: Config, args: argparse.Namespace) -> int:
    detail = with_session(cfg, ACTIVITY_PAGE, lambda s: fetch_case_detail(s, args.case_number))
    comments = detail.comments
    if args.from_support:
        comments = [c for c in comments if not c.from_advertiser]
    if args.latest:
        comments = comments[-args.latest :]

    if args.json:
        print(json.dumps([c.as_dict() for c in comments], indent=2))
        return 0

    if not comments:
        print("no replies on this case")
        return 0
    print(f"case {detail.case_number} — {detail.status}\n")
    for comment in comments:
        marker = "us" if comment.from_advertiser else cfg.portal.support_label
        print(f"--- [{comment.created_date[:19]}] {marker} · {comment.author}")
        print(comment.body or "(empty)")
        print()
    return 0


def _cmd_cases_attach(cfg: Config, args: argparse.Namespace) -> int:
    paths = [Path(p) for p in args.files]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        print(f"no such file: {', '.join(missing)}", file=sys.stderr)
        return 2

    def run(session: AuraSession) -> tuple[int, list[Upload]]:
        detail = fetch_case_detail(session, args.case_number)
        uploads = [upload_file(session, detail.case_id, path) for path in paths]
        after = fetch_case_detail(session, args.case_number).attachments
        return after, uploads

    after, uploads = with_session(cfg, ACTIVITY_PAGE, run)
    if args.json:
        payload = {
            "portal": cfg.portal.key,
            "attachments": after,
            "uploaded": [vars(u) for u in uploads],
        }
        print(json.dumps(payload, indent=2))
        return 0
    for upload in uploads:
        print(f"{upload.file_name}  {upload.byte_size} bytes  {upload.content_type}")
        print(f"   {upload.content_version_id}")
    print(f"\n{cfg.portal.label} case {args.case_number} now reports {after} attachment(s)")
    return 0


def _cmd_cases_reply(cfg: Config, args: argparse.Namespace) -> int:
    message = Path(args.message_file).read_text() if args.message_file else args.message
    if not message.strip():
        print("refusing to post an empty reply", file=sys.stderr)
        return 2
    rendered = rendered_length(message)
    if rendered > COMMENT_MAX_CHARS:
        print(
            f"reply renders to {rendered} characters; the portal caps a comment at "
            f"{COMMENT_MAX_CHARS} and would post nothing",
            file=sys.stderr,
        )
        return 2

    def run(session: AuraSession) -> tuple[str, int]:
        detail = fetch_case_detail(session, args.case_number)
        comments = post_comment(session, detail.case_id, message)
        return detail.status, len(comments)

    status, count = with_session(cfg, ACTIVITY_PAGE, run)
    if args.json:
        payload = {
            "portal": cfg.portal.key,
            "case": args.case_number,
            "status": status,
            "comments": count,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"posted to {cfg.portal.label} case {args.case_number} ({status}); "
            f"{count} message(s) on the thread"
        )
    return 0


def _cmd_cases_close(cfg: Config, args: argparse.Namespace) -> int:
    portal = cfg.portal
    if not portal.can_close:
        # Firing it anyway would answer SUCCESS and change nothing, which reads
        # as a closed case to anyone who does not re-read the status.
        print(
            f"closing a case is not supported for {portal.label}. {portal.close_hint}",
            file=sys.stderr,
        )
        return 2

    def run(session: AuraSession) -> tuple[str, str]:
        detail = fetch_case_detail(session, args.case_number)
        return detail.status, close_case(session, detail.case_id)

    before, after = with_session(cfg, ACTIVITY_PAGE, run)
    # The action reports SUCCESS whether or not the status moved, so compare
    # rather than trusting the call: a silent no-op must not read as a close.
    closed = bool(after) and after != before
    if args.json:
        payload = {
            "portal": portal.key,
            "case": args.case_number,
            "was": before,
            "now": after,
            "closed": closed,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"{portal.label} case {args.case_number}: {before} -> {after or '(not reported)'}")
    if not closed:
        print(
            f"the portal accepted the request but left case {args.case_number} on "
            f"{before!r}; it is NOT closed",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_categories_list(cfg: Config, args: argparse.Namespace) -> int:
    """Show the support categories the portal's own dropdown offers."""
    ad_unit = cfg.portal.resolve_ad_unit(args.platform)

    def run(session: AuraSession) -> list[tuple[Category, list[Category]]]:
        identity = Identity.fetch(session)
        return fetch_category_tree(session, ad_unit=ad_unit, partner_channel=identity.user_type)

    tree = with_session(cfg, CONTACT_PAGE, run)

    if args.json:
        payload = [{"category": l1.label, "issues": [c.label for c in kids]} for l1, kids in tree]
        print(json.dumps(payload, indent=2))
        return 0

    print(f"support categories for {ad_unit}:\n")
    for l1, kids in tree:
        print(f"  {l1.label}")
        for child in kids:
            extra = f"  ({len(child.form_fields)} form fields)" if child.form_fields else ""
            print(f"     - {child.label}{extra}")
    return 0


def _cmd_cases_create(cfg: Config, args: argparse.Namespace) -> int:
    portal = cfg.portal
    draft = CaseDraft(
        subject=args.subject,
        description=Path(args.description_file).read_text(),
        advertisers=args.advertisers or "",
        category=args.category,
        issue=args.issue,
        ad_unit=portal.resolve_ad_unit(args.platform),
    )

    def run(session: AuraSession) -> tuple[Submission, dict[str, Any] | None]:
        submission = prepare(session, draft, portal=portal)
        return submission, submit(session, submission) if args.submit else None

    submission, filed = with_session(cfg, CONTACT_PAGE, run)

    # Where the advertiser ids can ride depends on the action: a declared form
    # field under openCase, a parameter of their own under saveApiCase. On a
    # portal offering neither for this category they never reach the wire, so
    # say so rather than dropping them quietly — and say it on --submit too,
    # where it actually costs something.
    if draft.advertisers and not submission.carries_advertisers:
        print(
            f"warning: {portal.label} has nowhere to put the advertiser ids for this "
            "category, so --advertisers was dropped; put the ids in the description instead",
            file=sys.stderr,
        )

    # The payload names the case but not its destination, and the two portals
    # file through different Apex methods, so the portal and the action are the
    # halves a reviewer cannot recover from the fields.
    if filed is None:
        if args.json:
            print(
                json.dumps(
                    {
                        "portal": portal.key,
                        "action": submission.action,
                        "dry_run": submission.fields,
                    },
                    indent=2,
                )
            )
        else:
            print(_describe_payload(submission.fields))
            print(f"\nwould file at {portal.label} through {submission.action}.")
        # flush first so the notice lands after the payload, not interleaved
        sys.stdout.flush()
        print("\nnothing was filed. Re-run with --submit to file this case.", file=sys.stderr)
        return 0

    if args.json:
        print(
            json.dumps(
                {"portal": portal.key, "action": submission.action, "filed": filed}, indent=2
            )
        )
        return 0
    print(_describe_payload(filed))
    print(f"\nfiled at {portal.label} through {submission.action}.")
    return 0


def _describe_payload(payload: dict[str, Any]) -> str:
    interesting = {k: v for k, v in payload.items() if v not in ("", [], False, None)}
    width = max((len(k) for k in interesting), default=0)
    lines = []
    for key, value in interesting.items():
        text = str(value)
        if len(text) > 70:
            text = text[:67] + "..."
        lines.append(f"{key:<{width}}  {text}")
    return "\n".join(lines)


_EXAMPLES = """examples:
  walmart-support --portal walmart auth check
  walmart-support --portal walmart cases list --status "need info"
  walmart-support --portal walmart cases get 10000001
  walmart-support --portal walmart cases replies 10000001 --from-support
  walmart-support --portal walmart cases list --query "adGroups/list" --since 30d --deep
  walmart-support --portal walmart cases reply 10000001 --message-file answer.txt
  walmart-support --portal walmart cases attach 10000001 ./har.json
  walmart-support --portal walmart cases create --subject ... --description-file body.txt
  walmart-support --portal walmart categories list --platform sponsored-search

  walmart-support --portal samsclub cases list
  walmart-support --portal samsclub cases get 00010002

notes:
  every command takes --json for machine-readable output
  cases create files nothing unless --submit is given
  --portal is required on every command
  filing works on both portals; --platform is Walmart only
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="walmart-support",
        description="Read and file Walmart Connect and Sam's Club advertising support cases.",
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"walmart-support {_version()}",
        help="report the installed version",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--config", default=None, help=f"config file (default: {CONFIG_PATH})")
    parser.add_argument(
        "--portal",
        required=True,
        metavar="NAME",
        help=f"portal to act on ({', '.join(PORTALS)})",
    )
    sub = parser.add_subparsers(required=True)

    auth = sub.add_parser("auth", help="authentication helpers").add_subparsers(required=True)
    auth.add_parser(
        "check", help="report whether the portal session is authenticated"
    ).set_defaults(handler=_cmd_auth_check)
    auth.add_parser("logout", help="discard the cached session").set_defaults(
        handler=_cmd_auth_logout
    )

    cases = sub.add_parser("cases", help="read and file support cases").add_subparsers(
        required=True
    )
    listing = cases.add_parser("list", help="list cases, newest first")
    listing.set_defaults(handler=_cmd_cases_list)
    listing.add_argument(
        "--status",
        help="match status by words: 'need info' also matches 'Needs Info - Internal'",
    )
    listing.add_argument("--since", help="ISO date or day offset such as 30d")
    listing.add_argument(
        "--query",
        help="match the abbreviated subject/description the list returns; add --deep for full text",
    )
    listing.add_argument("--limit", type=int, default=0, help="show at most N cases")
    listing.add_argument(
        "--deep",
        action="store_true",
        help="match --query against full case text and replies (one request per case)",
    )

    detail = cases.add_parser("get", help="show one case in full")
    detail.set_defaults(handler=_cmd_cases_get)
    detail.add_argument("case_number")

    attach = cases.add_parser("attach", help="upload files to an existing case")
    attach.set_defaults(handler=_cmd_cases_attach)
    attach.add_argument("case_number")
    attach.add_argument("files", nargs="+", help="one or more files to upload")

    close = cases.add_parser("close", help="close a case")
    close.set_defaults(handler=_cmd_cases_close)
    close.add_argument("case_number")

    reply = cases.add_parser("reply", help="post a reply on an existing case")
    reply.set_defaults(handler=_cmd_cases_reply)
    reply.add_argument("case_number")
    message = reply.add_mutually_exclusive_group(required=True)
    message.add_argument("--message", help="reply text")
    message.add_argument("--message-file", help="file holding the reply text")

    replies = cases.add_parser("replies", help="show a case's conversation")
    replies.set_defaults(handler=_cmd_cases_replies)
    replies.add_argument("case_number")
    replies.add_argument("--from-support", action="store_true", help="only messages from support")
    replies.add_argument("--latest", type=int, default=0, help="show only the last N messages")

    create = cases.add_parser(
        "create",
        help="file a case (prints the payload unless --submit is given)",
    )
    create.set_defaults(handler=_cmd_cases_create)
    create.add_argument("--subject", required=True)
    create.add_argument(
        "--description-file",
        required=True,
        help="file holding the case body; keeps long payloads out of the shell",
    )
    create.add_argument("--advertisers", help="advertiser/profile ids affected")
    create.add_argument(
        "--category",
        default="API",
        help="level-1 category, per 'categories list' (default: API)",
    )
    create.add_argument(
        "--issue",
        default="Endpoint-specific problem",
        help="level-2 issue, per 'categories list' (default: Endpoint-specific problem)",
    )
    create.add_argument(
        "--platform",
        metavar="NAME",
        default=None,
        help="which ad platform the case is about; Walmart only",
    )
    create.add_argument(
        "--submit",
        action="store_true",
        help="actually file the case; without it the payload is only printed",
    )

    categories = sub.add_parser(
        "categories", help="list the portal's support categories"
    ).add_subparsers(required=True)
    cat_list = categories.add_parser("list", help="show categories and their issues")
    cat_list.set_defaults(handler=_cmd_categories_list)
    cat_list.add_argument(
        "--platform",
        default=None,
        metavar="NAME",
        help="ad platform to list for; Walmart only, and defaults to that portal's own",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = load_config(Path(args.config) if args.config else CONFIG_PATH, portal=args.portal)
    except PortalError as exc:
        # A ValueError, so it must be caught ahead of the clause below or a bad
        # --portal reads as a broken config file.
        print(f"{exc}", file=sys.stderr)
        return 2
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    handler: Callable[[Config, argparse.Namespace], int] = args.handler
    try:
        return handler(cfg, args)
    except BrokenPipeError:
        # Piping into head/less closes stdout early; exit quietly instead of
        # letting the interpreter report the failed flush at shutdown.
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except (PortalError, TooManyCandidates) as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except AuraError as exc:
        print(f"portal error: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"network error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
