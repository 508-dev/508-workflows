"""CLI entrypoint for EspoCRM search, REPL, and batch updates."""

from __future__ import annotations

import argparse
import code
import json
import os
import sys
from collections.abc import Sequence
from pprint import pprint
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from five08.clients.espo import EspoClient
from five08.crm_contacts import FROM_LOCATION, BatchUpdateResult, EspoContactRepository


class CRMCLISettings(BaseSettings):
    espo_base_url: str
    espo_api_key: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crmctl",
        description="EspoCRM search, REPL, and batch update helper.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    repl_parser = subparsers.add_parser("repl", help="Open an interactive Python REPL.")
    repl_parser.set_defaults(handler=_handle_repl)

    search_parser = subparsers.add_parser("search", help="Search contacts.")
    _add_search_arguments(search_parser)
    search_parser.add_argument(
        "--limit",
        type=_limit_argument,
        default=100,
        help="Maximum contacts to return (default: 100). Use 0 for all.",
    )
    search_parser.set_defaults(handler=_handle_search)

    batch_parser = subparsers.add_parser(
        "batch-update",
        help="Preview or apply updates to matching contacts.",
    )
    _add_search_arguments(batch_parser)
    batch_parser.add_argument(
        "--limit",
        type=_limit_argument,
        default=100,
        help="Maximum contacts to scan (default: 100). Use 0 for all.",
    )
    batch_parser.add_argument(
        "--set",
        dest="assignments",
        action="append",
        type=_parse_assignment,
        required=True,
        help="Update assignment in field=value form. Use timezone=@location to infer timezone.",
    )
    batch_parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes. Without this flag, crmctl only previews updates.",
    )
    batch_parser.set_defaults(handler=_handle_batch_update)

    return parser


def _add_search_arguments(parser: argparse.ArgumentParser) -> None:
    timezone_group = parser.add_mutually_exclusive_group()
    timezone_group.add_argument(
        "--timezone",
        help="Exact timezone match, e.g. UTC-05:00.",
    )
    timezone_group.add_argument(
        "--timezone-empty",
        action="store_true",
        help="Match contacts with an empty timezone.",
    )
    parser.add_argument(
        "--location-present",
        action="store_true",
        help="Require city, state, or country to be present.",
    )
    parser.add_argument(
        "--member-type",
        action="append",
        help="Repeatable member type filter, e.g. Member or Prospect.",
    )
    parser.add_argument(
        "--seniority",
        action="append",
        help="Repeatable seniority filter, e.g. senior or staff.",
    )
    role_group = parser.add_mutually_exclusive_group()
    role_group.add_argument(
        "--role",
        action="append",
        help="Repeatable role filter. Matches any requested role.",
    )
    role_group.add_argument(
        "--roles-empty",
        action="store_true",
        help="Match contacts with empty roles.",
    )
    parser.add_argument(
        "--phone-country-code",
        help="Phone prefix to validate, e.g. +1.",
    )
    phone_prefix_group = parser.add_mutually_exclusive_group()
    phone_prefix_group.add_argument(
        "--phone-missing-country-code",
        action="store_true",
        help="Require phone numbers to be present and not start with --phone-country-code.",
    )
    phone_prefix_group.add_argument(
        "--phone-has-country-code",
        action="store_true",
        help="Require phone numbers to be present and start with --phone-country-code.",
    )


def _load_repository() -> EspoContactRepository:
    settings = CRMCLISettings()  # type: ignore[call-arg]
    return EspoContactRepository(
        EspoClient(settings.espo_base_url, settings.espo_api_key)
    )


def _criteria_from_args(args: argparse.Namespace) -> dict[str, Any]:
    criteria: dict[str, Any] = {}
    if args.timezone:
        criteria["timezone"] = args.timezone
    if args.timezone_empty:
        criteria["timezone_empty"] = True
    if args.location_present:
        criteria["location_present"] = True
    if args.member_type:
        criteria["member_type"] = args.member_type
    if args.seniority:
        criteria["seniority"] = args.seniority
    if args.role:
        criteria["role"] = args.role
    if args.roles_empty:
        criteria["roles_empty"] = True
    if args.phone_country_code:
        criteria["phone_country_code"] = args.phone_country_code
    if args.phone_missing_country_code:
        criteria["phone_missing_country_code"] = True
    if args.phone_has_country_code:
        criteria["phone_missing_country_code"] = False
    return criteria


def _limit_argument(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("limit must be 0 or greater")
    return parsed


def _limit_value(raw_limit: int) -> int | None:
    if raw_limit < 0:
        raise ValueError("limit must be 0 or greater")
    if raw_limit == 0:
        return None
    return raw_limit


def _parse_assignment(raw_assignment: str) -> tuple[str, Any]:
    if "=" not in raw_assignment:
        raise argparse.ArgumentTypeError(
            f"Invalid assignment {raw_assignment!r}; expected field=value"
        )
    field_name, raw_value = raw_assignment.split("=", 1)
    field_name = field_name.strip()
    if not field_name:
        raise argparse.ArgumentTypeError(
            f"Invalid assignment {raw_assignment!r}; expected field=value"
        )

    value_text = raw_value.strip()
    lowered = value_text.lower()
    if value_text == "@location":
        value: Any = FROM_LOCATION
    elif lowered == "null":
        value = None
    elif lowered == "true":
        value = True
    elif lowered == "false":
        value = False
    elif value_text.startswith("[") or value_text.startswith("{"):
        try:
            value = json.loads(value_text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Invalid JSON value for assignment {raw_assignment!r}"
            ) from exc
    else:
        value = value_text
    return field_name, value


def _parse_assignments(raw_assignments: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    assignments: dict[str, Any] = {}
    for field_name, value in raw_assignments:
        assignments[field_name] = value
    return assignments


def _validate_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> argparse.Namespace:
    if getattr(args, "command", None) not in {"search", "batch-update"}:
        return args

    if getattr(args, "phone_country_code", None) is None and (
        getattr(args, "phone_missing_country_code", False)
        or getattr(args, "phone_has_country_code", False)
    ):
        parser.error(
            "--phone-country-code is required with "
            "--phone-missing-country-code or --phone-has-country-code"
        )

    return args


def _render_contacts(contacts: list[Any]) -> int:
    payload = {
        "count": len(contacts),
        "contacts": [contact.to_dict() for contact in contacts],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _render_batch_result(result: BatchUpdateResult) -> int:
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0


def _handle_repl(_args: argparse.Namespace) -> int:
    repo = _load_repository()

    def search(**criteria: Any) -> list[Any]:
        return repo.search(**criteria)

    def get(contact_id: str) -> Any:
        return repo.get(contact_id)

    def batch_update(
        *,
        search: dict[str, Any] | None = None,
        updates: dict[str, Any],
        limit: int | None = 100,
        apply: bool = False,
    ) -> BatchUpdateResult:
        return repo.batch_update(
            search=search,
            updates=updates,
            limit=limit,
            apply=apply,
        )

    banner = """
EspoCRM REPL

Available objects:
  repo
  search(**criteria)
  get(contact_id)
  batch_update(search={...}, updates={...}, apply=False)
  FROM_LOCATION
  pprint

Examples:
  contacts = search(timezone_empty=True, location_present=True, member_type=["Member"])
  contact = contacts[0]
  contact.timezone = contact.infer_timezone()
  contact.save()
  batch_update(
      search={"timezone_empty": True, "location_present": True},
      updates={"timezone": FROM_LOCATION},
      apply=False,
  )
""".strip()

    code.interact(
        banner=banner,
        local={
            "FROM_LOCATION": FROM_LOCATION,
            "batch_update": batch_update,
            "get": get,
            "os": os,
            "pprint": pprint,
            "repo": repo,
            "search": search,
        },
    )
    return 0


def _handle_search(args: argparse.Namespace) -> int:
    repo = _load_repository()
    contacts = repo.search(
        limit=_limit_value(args.limit),
        **_criteria_from_args(args),
    )
    return _render_contacts(contacts)


def _handle_batch_update(args: argparse.Namespace) -> int:
    repo = _load_repository()
    result = repo.batch_update(
        search=_criteria_from_args(args),
        updates=_parse_assignments(args.assignments),
        limit=_limit_value(args.limit),
        apply=args.apply,
    )
    return _render_batch_result(result)


def run(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    args = _validate_args(parser, args)
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
