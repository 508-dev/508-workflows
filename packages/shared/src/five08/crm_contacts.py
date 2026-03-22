"""Reusable EspoCRM contact search/update helpers for CLI and REPL workflows."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Final, Protocol

from five08.crm_normalization import (
    infer_timezone_from_location,
    normalize_city,
    normalize_country,
    normalize_roles,
    normalize_seniority,
    normalize_state,
    normalize_timezone,
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
    "location",
    "location_present",
    "member_type",
    "member_types",
    "phone_country_code",
    "phone_country_code_match",
    "phone_missing_country_code",
    "role",
    "roles",
    "roles_empty",
    "seniority",
    "timezone",
    "timezone_empty",
}
FILTER_OPERATOR_ALIASES: Final[dict[str, str]] = {
    "contains": "contains",
    "not_contains": "notContains",
    "notcontains": "notContains",
    "starts_with": "startsWith",
    "startswith": "startsWith",
    "ends_with": "endsWith",
    "endswith": "endsWith",
    "like": "like",
    "not_like": "notLike",
    "notlike": "notLike",
    "in": "in",
    "not_in": "notIn",
    "notin": "notIn",
    "is_true": "isTrue",
    "istrue": "isTrue",
    "is_false": "isFalse",
    "isfalse": "isFalse",
    "is_null": "isNull",
    "isnull": "isNull",
    "is_not_null": "isNotNull",
    "isnotnull": "isNotNull",
    "greater_than": "greaterThan",
    "greaterthan": "greaterThan",
    "gt": "greaterThan",
    "less_than": "lessThan",
    "lessthan": "lessThan",
    "lt": "lessThan",
    "greater_than_or_equals": "greaterThanOrEquals",
    "greaterthanorequals": "greaterThanOrEquals",
    "gte": "greaterThanOrEquals",
    "less_than_or_equals": "lessThanOrEquals",
    "lessthanorequals": "lessThanOrEquals",
    "lte": "lessThanOrEquals",
    "equals": "equals",
    "eq": "equals",
    "not_equals": "notEquals",
    "notequals": "notEquals",
    "neq": "notEquals",
}
COMPOUND_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "location": LOCATION_FIELDS,
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


def infer_timezone_from_location_helper(
    *, city: str | None, state: str | None, country: str | None
) -> str | None:
    return infer_timezone_from_location(
        city=city,
        state=state,
        country=country,
    )


def _presence_keyword_to_bool(
    value: Any,
    *,
    field_name: str,
    allow_other: bool = False,
) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if not text:
        return None
    if text == "present":
        return True
    if text == "empty":
        return False
    if allow_other:
        return None
    raise ValueError(f"{field_name} must be 'present' or 'empty'")


def _normalize_filter_operator(operator: str) -> str:
    normalized = operator.strip().replace("-", "_").casefold()
    mapped = FILTER_OPERATOR_ALIASES.get(normalized)
    if mapped is None:
        raise ValueError(f"Unsupported filter operator: {operator}")
    return mapped


def _normalize_list_value(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [item for item in value]
    return [value]


def _like_pattern_to_regex(pattern: str) -> re.Pattern[str]:
    parts: list[str] = []
    for character in pattern:
        if character == "%":
            parts.append(".*")
        elif character == "_":
            parts.append(".")
        else:
            parts.append(re.escape(character))
    return re.compile(f"^{''.join(parts)}$", flags=re.IGNORECASE)


def _normalize_scalar_for_field(field_name: str, value: Any) -> Any:
    raw_field_name = _resolve_field_name(field_name)
    if raw_field_name == "cTimezone":
        return _best_effort_timezone_value(value)
    if raw_field_name == "cSeniority":
        return normalize_seniority(value, empty_as_unknown=True)
    if raw_field_name == "cRoles":
        return normalize_roles(value)
    if isinstance(value, str):
        text = value.strip()
        return text
    return value


def _compare_values(left: Any, right: Any) -> int:
    if isinstance(left, bool) and isinstance(right, bool):
        return (left > right) - (left < right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return (left > right) - (left < right)

    left_text = "" if left is None else str(left).casefold()
    right_text = "" if right is None else str(right).casefold()
    return (left_text > right_text) - (left_text < right_text)


@dataclass(slots=True)
class FilterExpression:
    field: str
    operator: str
    value: Any = None
    _compiled_like_regex: re.Pattern[str] | None = None

    def __post_init__(self) -> None:
        if self.operator in {"like", "notLike"} and self.value is not None:
            object.__setattr__(
                self,
                "_compiled_like_regex",
                _like_pattern_to_regex(str(self.value)),
            )

    @classmethod
    def from_key_value(cls, key: str, value: Any) -> "FilterExpression":
        field, raw_operator = key.rsplit("__", 1) if "__" in key else (key, "equals")
        return cls(
            field=field,
            operator=_normalize_filter_operator(raw_operator),
            value=value,
        )

    def matches(self, contact: dict[str, Any]) -> bool:
        if self.field in COMPOUND_FIELDS:
            return self._matches_compound(contact)
        actual = _normalize_scalar_for_field(
            self.field,
            contact.get(_resolve_field_name(self.field)),
        )
        return self._matches_value(actual)

    def _matches_compound(self, contact: dict[str, Any]) -> bool:
        values = [contact.get(component) for component in COMPOUND_FIELDS[self.field]]
        if self.operator == "isNull":
            return all(_is_blank(item) for item in values)
        if self.operator == "isNotNull":
            return any(not _is_blank(item) for item in values)
        if self.operator == "contains":
            expected = str(self.value).strip().casefold()
            return any(expected in str(item).casefold() for item in values if item)
        if self.operator == "notContains":
            expected = str(self.value).strip().casefold()
            return all(expected not in str(item).casefold() for item in values if item)
        raise ValueError(
            f"Operator {self.operator} is not supported for compound field {self.field}"
        )

    def _matches_value(self, actual: Any) -> bool:
        operator = self.operator
        if operator == "isNull":
            return _is_blank(actual)
        if operator == "isNotNull":
            return not _is_blank(actual)
        if operator == "isTrue":
            return bool(actual) is True
        if operator == "isFalse":
            return bool(actual) is False

        expected = _normalize_scalar_for_field(self.field, self.value)
        if operator == "equals":
            return self._equals(actual, expected)
        if operator == "notEquals":
            return not self._equals(actual, expected)
        if operator == "contains":
            return self._contains(actual, expected)
        if operator == "notContains":
            return not self._contains(actual, expected)
        if operator == "startsWith":
            return self._starts_with(actual, expected)
        if operator == "endsWith":
            return self._ends_with(actual, expected)
        if operator == "like":
            return self._like(actual, expected, self._compiled_like_regex)
        if operator == "notLike":
            return not self._like(actual, expected, self._compiled_like_regex)
        if operator == "in":
            return self._in(actual, expected)
        if operator == "notIn":
            return not self._in(actual, expected)
        if operator == "greaterThan":
            return _compare_values(actual, expected) > 0
        if operator == "lessThan":
            return _compare_values(actual, expected) < 0
        if operator == "greaterThanOrEquals":
            return _compare_values(actual, expected) >= 0
        if operator == "lessThanOrEquals":
            return _compare_values(actual, expected) <= 0
        raise ValueError(f"Unsupported filter operator: {operator}")

    @staticmethod
    def _equals(actual: Any, expected: Any) -> bool:
        if isinstance(actual, list):
            if isinstance(expected, list):
                return actual == expected
            return expected in actual
        if isinstance(expected, list):
            return actual in expected
        if isinstance(actual, str) and isinstance(expected, str):
            return actual.casefold() == expected.casefold()
        return actual == expected

    @staticmethod
    def _contains(actual: Any, expected: Any) -> bool:
        if expected is None:
            return False
        if isinstance(actual, list):
            expected_text = str(expected).casefold()
            return any(expected_text in str(item).casefold() for item in actual)
        return str(expected).casefold() in str(actual).casefold()

    @staticmethod
    def _starts_with(actual: Any, expected: Any) -> bool:
        if isinstance(actual, list):
            expected_text = str(expected).casefold()
            return any(
                str(item).casefold().startswith(expected_text) for item in actual
            )
        return str(actual).casefold().startswith(str(expected).casefold())

    @staticmethod
    def _ends_with(actual: Any, expected: Any) -> bool:
        if isinstance(actual, list):
            expected_text = str(expected).casefold()
            return any(str(item).casefold().endswith(expected_text) for item in actual)
        return str(actual).casefold().endswith(str(expected).casefold())

    @staticmethod
    def _like(
        actual: Any,
        expected: Any,
        compiled_regex: re.Pattern[str] | None = None,
    ) -> bool:
        if expected is None:
            return False
        regex = compiled_regex or _like_pattern_to_regex(str(expected))
        if isinstance(actual, list):
            return any(regex.match(str(item)) for item in actual)
        return regex.match(str(actual)) is not None

    @staticmethod
    def _in(actual: Any, expected: Any) -> bool:
        expected_list = _normalize_list_value(expected)
        normalized_expected = [
            item.casefold() if isinstance(item, str) else item for item in expected_list
        ]
        if isinstance(actual, list):
            actual_values = [
                item.casefold() if isinstance(item, str) else item for item in actual
            ]
            return any(item in normalized_expected for item in actual_values)
        actual_value = actual.casefold() if isinstance(actual, str) else actual
        return actual_value in normalized_expected


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
    filters: tuple[FilterExpression, ...] = ()

    @classmethod
    def from_mapping(
        cls, raw_criteria: dict[str, Any] | None = None
    ) -> "SearchCriteria":
        criteria = raw_criteria or {}
        generic_filters: list[FilterExpression] = []
        for key, value in criteria.items():
            if key in SEARCH_CRITERIA_KEYS:
                continue
            generic_filters.append(FilterExpression.from_key_value(key, value))

        timezone_raw = criteria.get("timezone")
        timezone = None
        timezone_empty_from_value = None
        if timezone_raw is not None:
            timezone_text = str(timezone_raw).strip()
            if timezone_text:
                timezone_presence = _presence_keyword_to_bool(
                    timezone_text,
                    field_name="timezone",
                    allow_other=True,
                )
                if timezone_presence is None:
                    timezone = normalize_timezone(timezone_text) or timezone_text
                else:
                    timezone_empty_from_value = not timezone_presence

        location_present_from_value = _presence_keyword_to_bool(
            criteria.get("location"),
            field_name="location",
        )

        member_types = _normalize_member_types(
            criteria.get("member_types", criteria.get("member_type"))
        )
        seniorities = _normalize_seniorities(criteria.get("seniority"))
        roles_raw = criteria.get("roles", criteria.get("role"))
        roles: tuple[str, ...] = ()
        roles_empty_from_value = None
        if roles_raw is not None:
            roles_presence = _presence_keyword_to_bool(
                roles_raw,
                field_name="roles",
                allow_other=True,
            )
            if roles_presence is None:
                roles = tuple(normalize_roles(roles_raw))
            else:
                roles_empty_from_value = not roles_presence

        phone_country_code = criteria.get("phone_country_code")
        if phone_country_code is not None:
            phone_country_code = str(phone_country_code).strip()
            if not phone_country_code:
                phone_country_code = None

        phone_missing_country_code = _coerce_bool(
            criteria.get("phone_missing_country_code"),
            "phone_missing_country_code",
        )
        phone_country_code_match = criteria.get("phone_country_code_match")
        if phone_country_code_match is not None:
            phone_match_text = str(phone_country_code_match).strip().casefold()
            if phone_match_text == "present":
                phone_missing_country_code = False
            elif phone_match_text == "missing":
                phone_missing_country_code = True
            else:
                raise ValueError(
                    "phone_country_code_match must be 'present' or 'missing'"
                )

        parsed = cls(
            timezone=timezone,
            timezone_empty=(
                timezone_empty_from_value
                if timezone_empty_from_value is not None
                else _coerce_bool(criteria.get("timezone_empty"), "timezone_empty")
            ),
            location_present=(
                location_present_from_value
                if location_present_from_value is not None
                else _coerce_bool(criteria.get("location_present"), "location_present")
            ),
            member_types=member_types,
            seniorities=seniorities,
            roles=roles,
            roles_empty=(
                roles_empty_from_value
                if roles_empty_from_value is not None
                else _coerce_bool(criteria.get("roles_empty"), "roles_empty")
            ),
            phone_country_code=phone_country_code,
            phone_missing_country_code=phone_missing_country_code,
            filters=tuple(generic_filters),
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

        if self.timezone is not None:
            timezone_value = _best_effort_timezone_value(self.timezone)
            if timezone_value is not None:
                filters.append(
                    {
                        "attribute": "cTimezone",
                        "type": "equals",
                        "value": timezone_value,
                    }
                )

        if self.timezone_empty is True:
            filters.append({"attribute": "cTimezone", "type": "isNull"})
        elif self.timezone_empty is False:
            filters.append({"attribute": "cTimezone", "type": "isNotNull"})

        if self.member_types:
            if len(self.member_types) == 1:
                filters.append(
                    {
                        "attribute": "type",
                        "type": "equals",
                        "value": self.member_types[0],
                    }
                )

        if self.seniorities:
            if len(self.seniorities) == 1:
                filters.append(
                    {
                        "attribute": "cSeniority",
                        "type": "equals",
                        "value": self.seniorities[0],
                    }
                )

        if self.roles_empty is True:
            filters.append({"attribute": "cRoles", "type": "isNull"})
        elif self.roles_empty is False:
            filters.append({"attribute": "cRoles", "type": "isNotNull"})

        unary_operators = {"isNull", "isNotNull", "isTrue", "isFalse"}
        unsupported_remote_operators = {"in", "notIn"}
        for expression in self.filters:
            if expression.field in COMPOUND_FIELDS:
                continue
            if expression.field == "phone":
                continue
            if expression.operator in unsupported_remote_operators:
                continue

            filter_value = _normalize_scalar_for_field(
                expression.field, expression.value
            )
            filter_dict: dict[str, Any] = {
                "attribute": _resolve_field_name(expression.field),
                "type": expression.operator,
            }
            if expression.operator not in unary_operators:
                filter_dict["value"] = filter_value
            filters.append(filter_dict)

        return filters

    def required_fields(self) -> set[str]:
        fields = {"id"}
        if self.timezone is not None or self.timezone_empty is not None:
            fields.add("cTimezone")
        if self.location_present is not None:
            fields.update(LOCATION_FIELDS)
        if self.member_types:
            fields.add("type")
        if self.seniorities:
            fields.add("cSeniority")
        if self.roles or self.roles_empty is not None:
            fields.add("cRoles")
        if self.phone_country_code is not None:
            fields.add("phoneNumber")
        for expression in self.filters:
            if expression.field in COMPOUND_FIELDS:
                fields.update(COMPOUND_FIELDS[expression.field])
                continue
            fields.add(_resolve_field_name(expression.field))
        return fields

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

        if self.roles or self.roles_empty is not None:
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
            if self.phone_missing_country_code in {False, None} and not has_prefix:
                return False

        for expression in self.filters:
            if not expression.matches(contact):
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
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        if hasattr(type(self), name):
            raise AttributeError(
                f"Cannot set attribute {name!r} on Contact; it is defined on the class"
            )
        self.set(**{name: value})

    @property
    def id(self) -> str:
        contact_id = str(self._raw.get("id") or "").strip()
        if not contact_id:
            raise ValueError("Contact payload is missing required id")
        return contact_id

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
        if limit == 0:
            limit = None
        elif limit is not None and limit < 0:
            raise ValueError("limit must be greater than or equal to 0")

        parsed_criteria = SearchCriteria.from_mapping(criteria)
        select_fields = self._select_string(
            select,
            required_fields=parsed_criteria.required_fields(),
        )
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
        where: dict[str, Any] | None = None,
        update: dict[str, Any] | None = None,
        search: dict[str, Any] | None = None,
        updates: dict[str, Any] | None = None,
        limit: int | None = 100,
        apply: bool = False,
    ) -> BatchUpdateResult:
        effective_where = where if where is not None else search
        effective_update = update if update is not None else updates
        if effective_update is None:
            raise ValueError("batch_update requires update or updates")

        previews: list[ContactUpdatePreview] = []
        for contact in self.search(limit=limit, **(effective_where or {})):
            changed = contact.preview_updates(**effective_update)
            if not changed:
                continue

            preview = ContactUpdatePreview(
                contact_id=contact.id,
                name=str(contact.to_dict().get("name") or ""),
                updates=changed,
            )
            previews.append(preview)

            if apply:
                contact.set(**effective_update)
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
            return self._normalize_location_update(
                value,
                normalizer=normalize_city,
                label="city",
            )
        if field_name == "addressState":
            return self._normalize_location_update(
                value,
                normalizer=normalize_state,
                label="state",
            )
        if field_name == "addressCountry":
            return self._normalize_location_update(
                value,
                normalizer=normalize_country,
                label="country",
            )
        if field_name == "cTimezone":
            return self._normalize_timezone_update(value)
        if field_name == "cRoles":
            return self._normalize_roles_update(value)
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
    def _normalize_location_update(
        value: Any, *, normalizer: Any, label: str
    ) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            normalized = normalizer(text)
            if normalized is None:
                raise ValueError(f"Invalid {label} value: {value!r}")
            return normalized
        normalized = normalizer(value)
        if normalized is None:
            raise ValueError(f"Invalid {label} value: {value!r}")
        return normalized

    @staticmethod
    def _normalize_plain_string(value: Any) -> str | None:
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalize_timezone_update(value: Any) -> str | None:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            normalized = normalize_timezone(text)
            if normalized is None:
                raise ValueError(f"Invalid timezone value: {value!r}")
            return normalized
        normalized = normalize_timezone(value)
        if normalized is None:
            raise ValueError(f"Invalid timezone value: {value!r}")
        return normalized

    @staticmethod
    def _normalize_roles_update(value: Any) -> list[str]:
        if isinstance(value, str):
            return normalize_roles(value)
        if isinstance(value, (list, tuple, set)):
            if not all(isinstance(item, str) for item in value):
                raise ValueError(f"Invalid roles value: {value!r}")
            return normalize_roles(value)
        raise ValueError(f"Invalid roles value: {value!r}")

    @staticmethod
    def _select_string(
        select: str | list[str] | tuple[str, ...] | None,
        *,
        required_fields: set[str] | None = None,
    ) -> str:
        required = required_fields or set()
        if select is None:
            fields = list(DEFAULT_SELECT_FIELDS)
        elif isinstance(select, str):
            fields = [field.strip() for field in select.split(",") if field.strip()]
        else:
            fields = [field.strip() for field in select if field.strip()]

        seen = set(fields)
        for field in sorted(required):
            if field in seen:
                continue
            fields.append(field)
            seen.add(field)
        return ",".join(fields)
