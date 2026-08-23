#!/usr/bin/env python3
"""Move legacy selected-and-assigned CRM contacts into assignedonboarder.

Run without --apply first. EspoCRM must expose `assignedonboarder` in the
`cOnboardingState` picklist before applying this migration.
"""

from __future__ import annotations

import argparse

from five08.clients.espo import EspoClient
from five08.worker.config import settings


def _has_onboarder(value: object) -> bool:
    normalized = str(value or "").strip().casefold()
    return bool(normalized and normalized not in {"none", "no discord"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write CRM updates")
    args = parser.parse_args()
    if not settings.espo_base_url or not settings.espo_api_key:
        parser.error("ESPO_BASE_URL and ESPO_API_KEY are required")

    client = EspoClient(settings.espo_base_url, settings.espo_api_key)
    offset = 0
    migrated = 0
    while True:
        response = client.request(
            "GET",
            "Contact",
            {
                "offset": offset,
                "maxSize": 200,
                "select": "id,name,cOnboardingState,cOnboarder",
            },
        )
        contacts = response.get("list", [])
        if not contacts:
            break
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            state = str(contact.get("cOnboardingState") or "").strip().casefold()
            if state != "selected" or not _has_onboarder(contact.get("cOnboarder")):
                continue
            contact_id = str(contact.get("id") or "").strip()
            if not contact_id:
                continue
            print(f"{contact_id} {contact.get('name') or 'CRM contact'}")
            if args.apply:
                client.request(
                    "PUT",
                    f"Contact/{contact_id}",
                    {"cOnboardingState": "assignedonboarder"},
                )
            migrated += 1
        offset += len(contacts)
        if len(contacts) < 200:
            break
    action = "Migrated" if args.apply else "Would migrate"
    print(f"{action} {migrated} contact(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
