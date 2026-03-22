"""Reusable EspoCRM contact search/update helpers for CLI and REPL workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Protocol

from five08.crm_normalization import (
    normalize_city,
    normalize_country,
    normalize_roles,
    normalize_seniority,
    normalize_state,
    normalize_timezone,
)
from five08.resume_extractor import (
    _infer_timezone_from_location as infer_timezone_from_location_helper,
)

FIELD_ALIASES: Final[dict[str, str]] = {
    "city": "addressCity",
    "country": "addressCountry",
    "discord_user_id": "cDiscordUserID",
    "discord_username": "cDiscordUsername",
    "email": "emailAddress",
    "email_508": "c508Email",
    "member_type": "type",
    "phone": "phoneNumber",
    "roles": "cRoles",
    "seniority": "cSeniority",
    "state": "addressState",
    "timezone": "cTimezone",
}
DEFAULT_SELECT_FIELDS: Final[tuple[str, ...]] = (
    "id",
    "name",
    "emailAddress",
    "c508Email",
    "phoneNumber",
    "type",
    "cTimezone",
    "addressCity",
    "addressState",
    "addressCountry",
    "cSeniority",
    "cRoles",
    "cDiscordUsername",
    "cDiscordUserID",
    "modifiedAt",
)
LOCATION_FIELDS: Final[tuple[str, ...]] = (
    "addressCity",
    "addressState",
    "addressCountry",
)
SEARCH_CRITERIA_KEYS: Final[set[str]] = {
    "location_present",
    "member_type",
    "member_types",
    "phone_country_code",
    "phone_missing_country_code",
    "role",
    "roles",
    "roles_empty",
    "seniority",
    "timezone",
    "timezone_empty",
}


class _InferFromLocation:
    def __repr__(self) -> str:
        return "FROM_LOCATION"


FROM_LOCATION: Final = _InferFromLocation()


class ContactAPIClient(Protocol):
    def get_contact(self, contact_id: str) -> dict[str, Any]: ...

    def update_contact(
        self, contact_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]: ...

    def list_contacts(self, params: dict[str, Any]) -> dict[str, Any]: ...


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set)):
        return not [item for item in value if not _is_blank(item)]
    if isinstance(value, dict):
        return not value
    return False


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                values.append(text)
        return values
    text = str(value).strip()
    return [text] if text else []


def _normalize_member_types(value: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_string_list(value)))


def _normalize_seniorities(value: Any) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in _string_list(value):
        parsed = normalize_seniority(raw, empty_as_unknown=True)
        if not parsed or parsed in seen:
            continue
        seen.add(parsed)
        normalized.append(parsed)
    return tuple(normalized)


def _coerce_bool(value: Any, field_name: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise TypeError(f"{field_name} must be a bool or None")


def _resolve_field_name(field_name: str) -> str:
    return FIELD_ALIASES.get(field_name, field_name)


def _field_values_equal(left: Any, right: Any) -> bool:
    if _is_blank(left) and _is_blank(right):
        return True
    return left == right


def _best_effort_timezone_value(value: Any) -> str | None:
    normalized = normalize_timezone(value)
    if normalized is not None:
        return normalized
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build_or_equals(attribute: str, values: tuple[str, ...]) -> dict[str, Any]:
    if len(values) == 1:
        return {"type": "equals", "attribute": attribute, "value": values[0]}
    return {
        "type": "or",
        "value": [
            {"type": "equals", "attribute": attribute, "value": value}
            for value in values
        ],
    }


@dataclass(slots=True)
class SearchCriteria:
    timezone: str | None = None
    timezone_empty: bool | None = None
    location_present: bool | None = None
    member_types: tuple[str, ...] = ()
    seniorities: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    roles_empty: bool | None = None
    phone_country_code: str | None = None
    phone_missing_country_code: bool | None = None

    @classmethod
    def from_mapping(
        cls, raw_criteria: dict[str, Any] | None = None
    ) -> "SearchCriteria":
        criteria = raw_criteria or {}
        unknown = sorted(set(criteria) - SEARCH_CRITERIA_KEYS)
        if unknown:
            joined = ", ".join(unknown)
            raise ValueError(f"Unsupported search criteria: {joined}")

        timezone_raw = criteria.get("timezone")
        timezone = None
        if timezone_raw is not None:
            timezone_text = str(timezone_raw).strip()
            if timezone_text:
                timezone = normalize_timezone(timezone_text) or timezone_text

        member_types = _normalize_member_types(
            criteria.get("member_types", criteria.get("member_type"))
        )
        seniorities = _normalize_seniorities(criteria.get("seniority"))
        roles = tuple(normalize_roles(criteria.get("roles", criteria.get("role"))))

        phone_country_code = criteria.get("phone_country_code")
        if phone_country_code is not None:
            phone_country_code = str(phone_country_code).strip()
            if not phone_country_code:
                phone_country_code = None

        parsed = cls(
            timezone=timezone,
            timezone_empty=_coerce_bool(
                criteria.get("timezone_empty"), "timezone_empty"
            ),
            location_present=_coerce_bool(
                criteria.get("location_present"),
                "location_present",
            ),
            member_types=member_types,
            seniorities=seniorities,
            roles=roles,
            roles_empty=_coerce_bool(criteria.get("roles_empty"), "roles_empty"),
            phone_country_code=phone_country_code,
            phone_missing_country_code=_coerce_bool(
                criteria.get("phone_missing_country_code"),
                "phone_missing_country_code",
            ),
        )
        parsed.validate()
        return parsed

    def validate(self) -> None:
        if self.timezone and self.timezone_empty is True:
            raise ValueError("timezone and timezone_empty=True cannot be combined")
        if self.roles and self.roles_empty is True:
            raise ValueError("roles and roles_empty=True cannot be combined")
        if self.phone_missing_country_code is not None and not self.phone_country_code:
            raise ValueError(
                "phone_country_code is required when phone_missing_country_code is set"
            )

    def to_remote_filters(self) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = []
        if self.timezone:
            filters.append(
                {"type": "equals", "attribute": "cTimezone", "value": self.timezone}
            )

        if self.roles_empty is True:
            filters.append({"type": "arrayIsEmpty", "attribute": "cRoles"})

        if self.member_types:
            filters.append(_build_or_equals("type", self.member_types))

        if self.seniorities:
            filters.append(_build_or_equals("cSeniority", self.seniorities))

        return filters

    def matches(self, contact: dict[str, Any]) -> bool:
        contact_timezone = _best_effort_timezone_value(contact.get("cTimezone"))
        criteria_timezone = _best_effort_timezone_value(self.timezone)
        if self.timezone is not None and contact_timezone != criteria_timezone:
            return False

        if self.timezone_empty is not None:
            has_timezone = not _is_blank(contact.get("cTimezone"))
            if has_timezone == self.timezone_empty:
                return False

        if self.location_present is not None:
            has_location = any(
                not _is_blank(contact.get(field)) for field in LOCATION_FIELDS
            )
            if has_location != self.location_present:
                return False

        if self.member_types:
            member_type = str(contact.get("type") or "").strip()
            if member_type not in self.member_types:
                return False

        if self.seniorities:
            seniority = normalize_seniority(
                contact.get("cSeniority"),
                empty_as_unknown=True,
            )
            if seniority not in self.seniorities:
                return False

        contact_roles = set(normalize_roles(contact.get("cRoles")))
        if self.roles_empty is not None:
            roles_blank = not contact_roles
            if roles_blank != self.roles_empty:
                return False

        if self.roles and not contact_roles.intersection(self.roles):
            return False

        if self.phone_country_code is not None:
            phone_number = str(contact.get("phoneNumber") or "").strip()
            if not phone_number:
                return False
            has_prefix = phone_number.startswith(self.phone_country_code)
            if self.phone_missing_country_code is True and has_prefix:
                return False
            if self.phone_missing_country_code is False and not has_prefix:
                return False

        return True


@dataclass(slots=True)
class ContactUpdatePreview:
    contact_id: str
    name: str
    updates: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.contact_id,
            "name": self.name,
            "updates": self.updates,
        }


@dataclass(slots=True)
class BatchUpdateResult:
    previews: list[ContactUpdatePreview]
    applied: bool

    @property
    def count(self) -> int:
        return len(self.previews)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "count": self.count,
            "changes": [preview.to_dict() for preview in self.previews],
        }


class Contact:
    """Mutable contact wrapper that tracks pending EspoCRM updates."""

    def __init__(
        self, repository: "EspoContactRepository", raw: dict[str, Any]
    ) -> None:
        object.__setattr__(self, "_repository", repository)
        object.__setattr__(self, "_raw", dict(raw))
        object.__setattr__(self, "_pending", {})

    def __repr__(self) -> str:
        payload = self.to_dict()
        return (
            "Contact("
            f"id={payload.get('id')!r}, "
            f"name={payload.get('name')!r}, "
            f"member_type={payload.get('type')!r})"
        )

    def __getattr__(self, name: str) -> Any:
        field_name = _resolve_field_name(name)
        merged = self.to_dict()
        if field_name in merged:
            return merged[field_name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_") or hasattr(type(self), name):
            object.__setattr__(self, name, value)
            return
        self.set(**{name: value})

    @property
    def id(self) -> str:
        return str(self._raw.get("id") or "")

    @property
    def pending_updates(self) -> dict[str, Any]:
        return dict(self._pending)

    def to_dict(self) -> dict[str, Any]:
        merged = dict(self._raw)
        merged.update(self._pending)
        return merged

    def preview_updates(self, **updates: Any) -> dict[str, Any]:
        prepared = self._repository.prepare_contact_updates(self.to_dict(), updates)
        changed: dict[str, Any] = {}
        for field_name, value in prepared.items():
            if not _field_values_equal(self._raw.get(field_name), value):
                changed[field_name] = value
        return changed

    def set(self, **updates: Any) -> "Contact":
        changed = self.preview_updates(**updates)
        for field_name in updates:
            raw_field_name = _resolve_field_name(field_name)
            if raw_field_name not in changed:
                self._pending.pop(raw_field_name, None)
                continue
            self._pending[raw_field_name] = changed[raw_field_name]
        return self

    def infer_timezone(self) -> str | None:
        return self._repository.infer_timezone(self.to_dict())

    def apply_timezone_from_location(self) -> str | None:
        inferred = self.infer_timezone()
        if inferred is None:
            return None
        self.set(timezone=inferred)
        return inferred

    def save(self) -> "Contact":
        if not self._pending:
            return self
        updated = self._repository.client.update_contact(self.id, dict(self._pending))
        if updated:
            self._raw.update(updated)
        else:
            self._raw.update(self._pending)
        self._pending.clear()
        return self

    def refresh(self) -> "Contact":
        self._raw = self._repository.client.get_contact(self.id)
        self._pending.clear()
        return self


class EspoContactRepository:
    """Search and update contacts with a Python-friendly API."""

    def __init__(self, client: ContactAPIClient, *, page_size: int = 100) -> None:
        self.client = client
        self.page_size = page_size

    def get(self, contact_id: str) -> Contact:
        return Contact(self, self.client.get_contact(contact_id))

    def search(
        self,
        *,
        limit: int | None = 100,
        select: str | list[str] | tuple[str, ...] | None = None,
        order_by: str = "modifiedAt",
        order: str = "desc",
        **criteria: Any,
    ) -> list[Contact]:
        if limit is not None and limit <= 0:
            raise ValueError("limit must be greater than 0")

        parsed_criteria = SearchCriteria.from_mapping(criteria)
        select_fields = self._select_string(select)
        remote_filters = parsed_criteria.to_remote_filters()

        contacts: list[Contact] = []
        offset = 0

        while True:
            remaining = None if limit is None else limit - len(contacts)
            if remaining is not None and remaining <= 0:
                break

            page_size = (
                self.page_size if remaining is None else min(self.page_size, remaining)
            )
            params: dict[str, Any] = {
                "maxSize": page_size,
                "offset": offset,
                "orderBy": order_by,
                "order": order,
                "select": select_fields,
            }
            if remote_filters:
                params["where"] = remote_filters

            response = self.client.list_contacts(params)
            raw_items = response.get("list")
            items = raw_items if isinstance(raw_items, list) else []

            for item in items:
                if not isinstance(item, dict):
                    continue
                if not parsed_criteria.matches(item):
                    continue
                contacts.append(Contact(self, item))
                if limit is not None and len(contacts) >= limit:
                    return contacts

            total = response.get("total")
            offset += len(items)
            if not items:
                break
            if isinstance(total, int) and offset >= total:
                break
            if len(items) < page_size:
                break

        return contacts

    def batch_update(
        self,
        *,
        search: dict[str, Any] | None = None,
        updates: dict[str, Any],
        limit: int | None = 100,
        apply: bool = False,
    ) -> BatchUpdateResult:
        previews: list[ContactUpdatePreview] = []
        for contact in self.search(limit=limit, **(search or {})):
            changed = contact.preview_updates(**updates)
            if not changed:
                continue

            preview = ContactUpdatePreview(
                contact_id=contact.id,
                name=str(contact.to_dict().get("name") or ""),
                updates=changed,
            )
            previews.append(preview)

            if apply:
                contact.set(**updates)
                contact.save()

        return BatchUpdateResult(previews=previews, applied=apply)

    def infer_timezone(self, values: dict[str, Any]) -> str | None:
        city = normalize_city(values.get("addressCity"))
        state = normalize_state(values.get("addressState"))
        country = normalize_country(values.get("addressCountry"))
        return infer_timezone_from_location_helper(
            city=city,
            state=state,
            country=country,
        )

    def prepare_contact_updates(
        self,
        current_values: dict[str, Any],
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        pending_timezone_value: Any | None = None

        for field_name, value in updates.items():
            raw_field_name = _resolve_field_name(field_name)
            if raw_field_name == "cTimezone" and (
                value is FROM_LOCATION or value == "@location"
            ):
                pending_timezone_value = value
                continue
            normalized[raw_field_name] = self._normalize_update_value(
                raw_field_name, value
            )

        if pending_timezone_value is not None:
            timezone_context = dict(current_values)
            timezone_context.update(normalized)
            inferred_timezone = self.infer_timezone(timezone_context)
            if inferred_timezone is not None:
                normalized["cTimezone"] = inferred_timezone

        return normalized

    def _normalize_update_value(self, field_name: str, value: Any) -> Any:
        if value is None:
            return None

        if field_name == "addressCity":
            return self._normalize_string_field(value, normalize_city)
        if field_name == "addressState":
            return self._normalize_string_field(value, normalize_state)
        if field_name == "addressCountry":
            return self._normalize_string_field(value, normalize_country)
        if field_name == "cTimezone":
            return self._normalize_string_field(value, normalize_timezone)
        if field_name == "cRoles":
            return normalize_roles(value)
        if field_name == "cSeniority":
            if isinstance(value, str) and not value.strip():
                return None
            return normalize_seniority(value, empty_as_unknown=True)
        if field_name == "type":
            return self._normalize_plain_string(value)
        if field_name == "phoneNumber":
            return self._normalize_plain_string(value)
        return value

    @staticmethod
    def _normalize_string_field(value: Any, normalizer: Any) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return normalizer(value)

    @staticmethod
    def _normalize_plain_string(value: Any) -> str | None:
        text = str(value).strip()
        return text or None

    @staticmethod
    def _select_string(select: str | list[str] | tuple[str, ...] | None) -> str:
        if select is None:
            return ",".join(DEFAULT_SELECT_FIELDS)
        if isinstance(select, str):
            return select
        return ",".join(select)
