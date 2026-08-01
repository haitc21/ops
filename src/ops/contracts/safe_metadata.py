"""Shared secret-free metadata bounds for contracts and API projections."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

_FORBIDDEN_NORMALIZED_KEYS = frozenset(
    {
        "password",
        "token",
        "authorization",
        "userdata",
        "privatekey",
        "cacertpem",
        "rawcatalog",
        "rawresponse",
        "credential",
        "signedurl",
    }
)
_FORBIDDEN_SUBSTRINGS = (
    "password",
    "token",
    "authorization",
    "privatekey",
    "cacertpem",
    "userdata",
    "rawresponse",
    "rawcatalog",
    "credential",
    "signedurl",
)

# Conservative shared tree shape for generic metadata, attachments, and capability extras.
# Worst-case serialized form stays within 64 KiB before the explicit runtime byte check.
MAX_TREE_MAP_ENTRIES = 4
MAX_TREE_LIST_ENTRIES = 4
MAX_TREE_DEPTH = 4
MAX_TREE_STRING_LENGTH = 128
MAX_ROOT_MAP_ENTRIES = 128

MAX_ATTACHMENT_OBJECTS = 32
MAX_ATTACHMENT_SERIALIZED_BYTES = 64 * 1024
MAX_ATTACHMENT_KEY_LENGTH = 32
MAX_ATTACHMENT_STRING_LENGTH = 128
_MAX_METADATA_SERIALIZED_BYTES = 64 * 1024

_MAX_STRING_LIST_ITEM_LENGTH = 255
_MAX_CATALOG_TAG_COUNT = 64
_MAX_CATALOG_PROJECT_ID_COUNT = 256
MAX_SAFE_PROJECT_ID_LENGTH = 255

MAX_CAPABILITY_EXTRA_STRING_LENGTH = 2048
MAX_CAPABILITY_VERSION_STRING_LENGTH = 64
MAX_CAPABILITY_REASON_STRING_LENGTH = 256
MAX_CAPABILITY_SCHEMA_VERSION_LENGTH = 16

PROVIDER_TIMESTAMP_MAX_LENGTH = 64
_PROVIDER_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T"
    r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d"
    r"(?:\.\d{1,9})?(?:Z|[+-](?:0[0-9]|1[0-4]):[0-5]\d)$"
)

# Backward-compatible aliases used by inventory validation imports.
_MAX_METADATA_ENTRIES = MAX_ROOT_MAP_ENTRIES
_MAX_METADATA_KEY_LENGTH = 255
_MAX_METADATA_DEPTH = MAX_TREE_DEPTH
MAX_ATTACHMENT_MAP_ENTRIES = MAX_TREE_MAP_ENTRIES
MAX_ATTACHMENT_LIST_ENTRIES = MAX_TREE_LIST_ENTRIES
MAX_ATTACHMENT_DEPTH = MAX_TREE_DEPTH

_HTTP_USERINFO_PATTERN = re.compile(r"https?://[^/:@]+:[^/@]+@", re.IGNORECASE)
_HTTP_QUERY_SECRET_PATTERN = re.compile(
    r"https?://.*(?:token=|signature=|sig=|signed_url=|signedurl=)",
    re.IGNORECASE,
)
_PASSWORD_ASSIGNMENT_PATTERN = re.compile(r"(?<![a-z0-9])password\s*=", re.IGNORECASE)
_TOKEN_ASSIGNMENT_PATTERN = re.compile(r"(?<![a-z0-9])token\s*=", re.IGNORECASE)
_AUTHORIZATION_SCHEME_PATTERN = re.compile(
    r"(?<![a-z0-9])authorization\s*[:\s]+\s*(?:bearer|basic|token)\b",
    re.IGNORECASE,
)
_STANDALONE_BASIC_SCHEME_PATTERN = re.compile(
    r"(?<![a-zA-Z0-9])Basic\s+[A-Za-z0-9._\-+/=]{8,}",
    re.IGNORECASE,
)
_STANDALONE_BEARER_TOKEN_SCHEME_PATTERN = re.compile(
    r"(?<![a-zA-Z0-9])(?:Bearer|Token)\s+(?=[A-Za-z0-9._\-+/=]*[0-9=+/=-])[A-Za-z0-9._\-+/=]{8,}",
    re.IGNORECASE,
)
_STANDALONE_LETTER_ONLY_BEARER_TOKEN_PATTERN = re.compile(
    r"(?<![a-zA-Z0-9])(?:Bearer|Token)\s+[A-Za-z]{8,}(?:\s*(?:[,.;:])?\s*)$",
    re.IGNORECASE,
)
_VOLUME_ATTACHMENT_RESOURCE_FIELDS = frozenset({"device", "boot_index", "delete_on_termination"})

ALLOWED_DISK_FORMATS = frozenset(
    {
        "ami",
        "aki",
        "ari",
        "iso",
        "qcow2",
        "raw",
        "vhd",
        "vhdx",
        "vmdk",
        "vdi",
        "ploop",
        "ova",
        "qed",
    }
)
DISK_FORMAT_MAX_LENGTH = 32


def _ecma_char_class(character: str) -> str:
    if character.isalpha():
        lower = character.lower()
        upper = character.upper()
        if lower == upper:
            return re.escape(character)
        return f"[{upper}{lower}]"
    return re.escape(character)


def ecma262_ci_substring_pattern(substring: str) -> str:
    """ECMA-262 pattern matching substring with arbitrary non-alphanumeric separators."""
    parts = [_ecma_char_class(character) for character in substring]
    return r".*" + r"[^a-zA-Z0-9]*".join(parts) + r".*"


def ecma262_secret_key_pattern() -> str:
    """Single ECMA-262 pattern rejecting normalized secret-key substrings."""
    alternatives = "|".join(
        f"(?:{ecma262_ci_substring_pattern(substring)})" for substring in _FORBIDDEN_SUBSTRINGS
    )
    return f"(?:{alternatives})"


def ecma262_pem_private_key_pattern() -> str:
    return r".*[Bb][Ee][Gg][Ii][Nn].*[Pp][Rr][Ii][Vv][Aa][Tt][Ee].*[Kk][Ee][Yy].*"


def ecma262_ci_literal_pattern(substring: str) -> str:
    return r".*" + "".join(_ecma_char_class(character) for character in substring) + r".*"


def ecma262_authorization_scheme_pattern() -> str:
    return (
        r".*[Aa][Uu][Tt][Hh][Oo][Rr][Ii][Zz][Aa][Tt][Ii][Oo][Nn]"
        r"[^a-zA-Z0-9]*[:\s][^a-zA-Z0-9]*"
        r"(?:[Bb][Ee][Aa][Rr][Ee][Rr]|[Bb][Aa][Ss][Ii][Cc]|[Tt][Oo][Kk][Ee][Nn])\b.*"
    )


def ecma262_standalone_auth_scheme_pattern() -> str:
    return (
        r".*(?:"
        r"[Bb][Aa][Ss][Ii][Cc][^a-zA-Z0-9]+[A-Za-z0-9._+/=-]{8,}|"
        r"(?:[Bb][Ee][Aa][Rr][Ee][Rr]|[Tt][Oo][Kk][Ee][Nn])"
        r"[^a-zA-Z0-9]+[A-Za-z0-9._+/=]*[0-9=+/=-][A-Za-z0-9._+/=]{7,}|"
        r"(?:[Bb][Ee][Aa][Rr][Ee][Rr]|[Tt][Oo][Kk][Ee][Nn])"
        r"[^a-zA-Z0-9]+[A-Za-z]{8,}(?:\s*(?:[,.;:])?\s*$)"
        r").*"
    )


def ecma262_secret_value_patterns() -> tuple[str, ...]:
    """ECMA-262 portable patterns for secret-bearing string values."""
    return (
        ecma262_pem_private_key_pattern(),
        ecma262_ci_literal_pattern("x-amz-signature"),
        ecma262_ci_literal_pattern("x-goog-signature"),
        ecma262_ci_literal_pattern("signedurl"),
        ecma262_ci_literal_pattern("signed_url"),
        ecma262_ci_literal_pattern("credential="),
        ecma262_ci_literal_pattern("awsaccesskeyid="),
        ecma262_ci_literal_pattern("password="),
        ecma262_ci_literal_pattern("token="),
        ecma262_authorization_scheme_pattern(),
        ecma262_standalone_auth_scheme_pattern(),
        r".*[Hh][Tt][Tt][Pp][Ss]?://[^/:@]+:[^/@]+@.*",
        r".*[Hh][Tt][Tt][Pp][Ss]?://.*(?:[Tt][Oo][Kk][Ee][Nn]=|[Ss][Ii][Gg][Nn][Aa][Tt][Uu][Rr][Ee]=|[Ss][Ii][Gg]=|[Ss][Ii][Gg][Nn][Ee][Dd]_[Uu][Rr][Ll]=|[Ss][Ii][Gg][Nn][Ee][Dd][Uu][Rr][Ll]=).*",
    )


def secret_key_pattern() -> str:
    """JSON Schema propertyNames pattern source (matches forbidden keys)."""
    return ecma262_secret_key_pattern()


_SECRET_KEY_PATTERN = re.compile(ecma262_secret_key_pattern())


def normalize_secret_key(key: str) -> str:
    """Lowercase and strip non-alphanumeric separators for secret-key comparison."""
    return re.sub(r"[^a-z0-9]", "", key.lower())


def is_secret_key(key: str) -> bool:
    normalized = normalize_secret_key(key)
    if normalized in _FORBIDDEN_NORMALIZED_KEYS:
        return True
    return any(substring in normalized for substring in _FORBIDDEN_SUBSTRINGS)


def is_secret_value(value: str) -> bool:
    lowered = value.lower()
    if "begin" in lowered and "private key" in lowered:
        return True
    if _HTTP_USERINFO_PATTERN.search(value):
        return True
    if "signedurl" in lowered or "signed_url" in lowered:
        return True
    if "x-amz-signature" in lowered or "x-goog-signature" in lowered:
        return True
    if "credential=" in lowered or "awsaccesskeyid=" in lowered:
        return True
    if _HTTP_QUERY_SECRET_PATTERN.search(value):
        return True
    if _PASSWORD_ASSIGNMENT_PATTERN.search(value):
        return True
    if _TOKEN_ASSIGNMENT_PATTERN.search(value):
        return True
    if _AUTHORIZATION_SCHEME_PATTERN.search(value):
        return True
    if _STANDALONE_BASIC_SCHEME_PATTERN.search(value):
        return True
    if _STANDALONE_BEARER_TOKEN_SCHEME_PATTERN.search(value):
        return True
    if _STANDALONE_LETTER_ONLY_BEARER_TOKEN_PATTERN.search(value):
        return True
    return False


def validate_serialized_size(value: object, *, label: str) -> None:
    serialized = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    if len(serialized) > _MAX_METADATA_SERIALIZED_BYTES:
        raise ValueError(f"{label} exceed maximum serialized size")


def validate_safe_catalog_string(value: object, *, label: str, max_length: int) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be a non-empty string")
    if len(value) > max_length:
        raise ValueError(f"{label} exceeds maximum length")
    if is_secret_value(value):
        raise ValueError("forbidden secret-bearing metadata value")
    return value


def validate_safe_project_id(value: object, *, label: str = "project id") -> str:
    return validate_safe_catalog_string(
        value,
        label=label,
        max_length=MAX_SAFE_PROJECT_ID_LENGTH,
    )


def validate_provider_timestamp(value: object, *, label: str) -> str:
    text = validate_safe_catalog_string(
        value,
        label=label,
        max_length=PROVIDER_TIMESTAMP_MAX_LENGTH,
    )
    if not _PROVIDER_TIMESTAMP_PATTERN.fullmatch(text):
        raise ValueError(f"{label} must be an ISO-8601 timestamp")
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is not None:
        offset = parsed.utcoffset()
        if offset is not None and abs(offset) > timedelta(hours=14):
            raise ValueError(f"{label} timezone offset is invalid")
    return text


def validate_volume_attachment_resource(resource: object) -> dict[str, Any]:
    if resource is None:
        return {}
    if not isinstance(resource, dict):
        raise ValueError("volume attachment resource must be an object")
    extra = set(resource) - _VOLUME_ATTACHMENT_RESOURCE_FIELDS
    if extra:
        raise ValueError("volume attachment resource has unsupported fields")
    validated: dict[str, Any] = {}
    device = resource.get("device")
    if device is not None:
        validated["device"] = validate_safe_catalog_string(
            device,
            label="device",
            max_length=255,
        )
    boot_index = resource.get("boot_index")
    if boot_index is not None:
        if type(boot_index) is not int:
            raise ValueError("boot_index must be an integer")
        validated["boot_index"] = boot_index
    delete_on_termination = resource.get("delete_on_termination")
    if delete_on_termination is not None:
        if type(delete_on_termination) is not bool:
            raise ValueError("delete_on_termination must be a boolean")
        validated["delete_on_termination"] = delete_on_termination
    return validated


def _validate_tree_scalar(
    value: object,
    *,
    max_string_length: int,
    allow_null: bool,
) -> None:
    if value is None:
        if allow_null:
            return
        raise ValueError("metadata contains unsupported scalar type")
    if isinstance(value, bool | int | float):
        return
    if isinstance(value, str):
        if len(value) > max_string_length:
            raise ValueError("metadata string exceeds maximum length")
        if is_secret_value(value):
            raise ValueError("forbidden secret-bearing metadata value")
        return
    raise ValueError("metadata contains unsupported scalar type")


def validate_conservative_tree(
    value: object,
    *,
    depth: int = 0,
    parent_secret: bool = False,
    root_map: bool = False,
    allow_null: bool = False,
    max_string_length: int = MAX_TREE_STRING_LENGTH,
    max_key_length: int = _MAX_METADATA_KEY_LENGTH,
    shallow_map_depth: int = 0,
) -> None:
    if parent_secret:
        raise ValueError("forbidden secret-bearing metadata context")
    if depth > MAX_TREE_DEPTH:
        raise ValueError("metadata nesting exceeds maximum depth")
    if isinstance(value, dict):
        if depth <= shallow_map_depth:
            max_map_entries = MAX_ROOT_MAP_ENTRIES
        else:
            max_map_entries = MAX_TREE_MAP_ENTRIES
        if len(value) > max_map_entries:
            raise ValueError("metadata map exceeds maximum entries")
        for child_key, child in value.items():
            key_text = str(child_key)
            if len(key_text) > max_key_length:
                raise ValueError("metadata key exceeds maximum length")
            if is_secret_key(key_text):
                raise ValueError(f"forbidden inventory metadata key: {key_text}")
            validate_conservative_tree(
                child,
                depth=depth + 1,
                parent_secret=parent_secret,
                allow_null=allow_null,
                max_string_length=max_string_length,
                max_key_length=max_key_length,
                shallow_map_depth=shallow_map_depth,
            )
    elif isinstance(value, list):
        if len(value) > MAX_TREE_LIST_ENTRIES:
            raise ValueError("metadata list exceeds maximum entries")
        for child in value:
            if isinstance(child, dict | list):
                raise ValueError("metadata list entry is invalid")
            _validate_tree_scalar(
                child,
                max_string_length=max_string_length,
                allow_null=allow_null,
            )
    else:
        _validate_tree_scalar(
            value,
            max_string_length=max_string_length,
            allow_null=allow_null,
        )


def validate_metadata_tree(
    value: object,
    *,
    depth: int = 0,
    key: str | None = None,
    parent_secret: bool = False,
) -> None:
    del key
    validate_conservative_tree(
        value,
        depth=depth,
        parent_secret=parent_secret,
        allow_null=False,
        shallow_map_depth=0,
    )


_SERVICE_CAPABILITY_KNOWN_KEYS = frozenset({"available", "min_version", "max_version", "reason"})
_FEATURE_CAPABILITY_KNOWN_KEYS = frozenset({"supported", "reason"})
_CAPABILITY_ROOT_KNOWN_KEYS = frozenset({"schema_version", "services", "features"})


def _validate_capability_bounded_extra_value(value: object) -> None:
    """Validate one capability extra field with its own depth budget (ExtraDepth1..4)."""
    if isinstance(value, dict):
        validate_conservative_tree(
            value,
            depth=0,
            allow_null=True,
            max_string_length=MAX_CAPABILITY_EXTRA_STRING_LENGTH,
            shallow_map_depth=0,
        )
        return
    if isinstance(value, list):
        if len(value) > MAX_TREE_LIST_ENTRIES:
            raise ValueError("metadata list exceeds maximum entries")
        for child in value:
            if isinstance(child, dict | list):
                raise ValueError("metadata list entry is invalid")
            _validate_tree_scalar(
                child,
                max_string_length=MAX_CAPABILITY_EXTRA_STRING_LENGTH,
                allow_null=True,
            )
        return
    _validate_tree_scalar(
        value,
        max_string_length=MAX_CAPABILITY_EXTRA_STRING_LENGTH,
        allow_null=True,
    )


def _validate_capability_object_map(
    value: object,
    *,
    label: str,
    known_keys: frozenset[str],
    validate_unknown_values: bool = True,
) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    if len(value) > MAX_ROOT_MAP_ENTRIES:
        raise ValueError(f"{label} exceed maximum entries")
    for key, child in value.items():
        key_text = str(key)
        if len(key_text) > _MAX_METADATA_KEY_LENGTH:
            raise ValueError("metadata key exceeds maximum length")
        if is_secret_key(key_text):
            raise ValueError(f"forbidden inventory metadata key: {key_text}")
        if key in known_keys:
            continue
        if validate_unknown_values:
            _validate_capability_bounded_extra_value(child)


def validate_capability_extra_tree(value: object, *, depth: int = 0) -> None:
    """Validate capability document extra fields with schema-aligned depth accounting."""
    del depth
    if not isinstance(value, dict):
        raise ValueError("capability document must be an object")
    if len(value) > MAX_ROOT_MAP_ENTRIES:
        raise ValueError("metadata map exceeds maximum entries")
    for key in value:
        key_text = str(key)
        if is_secret_key(key_text):
            raise ValueError(f"forbidden inventory metadata key: {key_text}")
    services = value.get("services")
    if services is not None:
        _validate_capability_object_map(
            services,
            label="services",
            known_keys=frozenset(),
            validate_unknown_values=False,
        )
        for service in services.values():
            if isinstance(service, dict):
                _validate_capability_object_map(
                    service,
                    label="service capability",
                    known_keys=_SERVICE_CAPABILITY_KNOWN_KEYS,
                )
    features = value.get("features")
    if features is not None:
        _validate_capability_object_map(
            features,
            label="features",
            known_keys=frozenset(),
            validate_unknown_values=False,
        )
        for feature in features.values():
            if isinstance(feature, dict):
                _validate_capability_object_map(
                    feature,
                    label="feature capability",
                    known_keys=_FEATURE_CAPABILITY_KNOWN_KEYS,
                )
    for key, child in value.items():
        if key in _CAPABILITY_ROOT_KNOWN_KEYS:
            continue
        _validate_capability_bounded_extra_value(child)


def validate_disk_format(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value:
        raise ValueError("disk_format must be a non-empty string")
    if value != value.lower():
        raise ValueError("disk_format must be lowercase")
    if len(value) > DISK_FORMAT_MAX_LENGTH:
        raise ValueError("disk_format exceeds maximum length")
    if not re.fullmatch(r"[a-z0-9._-]+", value):
        raise ValueError("disk_format contains invalid characters")
    if value not in ALLOWED_DISK_FORMATS:
        raise ValueError("disk_format is not allow-listed")
    return value


def validate_attachment_tree(
    value: object,
    *,
    depth: int = 0,
    parent_secret: bool = False,
) -> None:
    validate_conservative_tree(
        value,
        depth=depth,
        parent_secret=parent_secret,
        allow_null=False,
        max_string_length=MAX_ATTACHMENT_STRING_LENGTH,
        max_key_length=MAX_ATTACHMENT_KEY_LENGTH,
        shallow_map_depth=-1,
    )


def validate_bounded_string_list(
    value: object,
    *,
    max_items: int,
    max_item_length: int = _MAX_STRING_LIST_ITEM_LENGTH,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("metadata list is invalid")
    if len(value) > max_items:
        raise ValueError("metadata list exceeds maximum length")
    bounded: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError("metadata list entry is invalid")
        if len(item) > max_item_length:
            raise ValueError("metadata list entry exceeds maximum length")
        if is_secret_value(item):
            raise ValueError("forbidden secret-bearing metadata value")
        bounded.append(item)
    return bounded


def assert_safe_tree(value: object, *, key_prefix: str = "forbidden validation field") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if is_secret_key(key_text):
                raise ValueError(f"{key_prefix}: {key}")
            assert_safe_tree(child, key_prefix=key_prefix)
    elif isinstance(value, list):
        for child in value:
            assert_safe_tree(child, key_prefix=key_prefix)
    elif isinstance(value, str) and is_secret_value(value):
        raise ValueError("forbidden secret-bearing validation value")
