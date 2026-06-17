"""Payload-reference URI grammar (CONTRACT.md §6).

A `Sample.payload_ref` is a URI or an opaque handle. WHEN it is a URI, it must
conform to this grammar:

    <scheme>://<location>[#<fragment>]

Supported schemes in v0.3: file, mcap, parquet, memory. The set is extensible
via `register_scheme`. This module PARSES and VALIDATES only — it never resolves
or opens anything (no I/O, no resolvers; that is deferred).
"""
from __future__ import annotations

from dataclasses import dataclass

from .types import ValidationError, require, require_nonempty_str

# Extensible scheme registry. Resolution is intentionally NOT implemented here.
_ALLOWED_SCHEMES: set[str] = {"file", "mcap", "parquet", "memory"}

_SEP = "://"


def allowed_schemes() -> set[str]:
    """A copy of the currently-registered schemes."""
    return set(_ALLOWED_SCHEMES)


def register_scheme(scheme: str) -> None:
    """Register an additional payload-URI scheme (extensibility hook)."""
    require_nonempty_str(scheme, "scheme")
    require(scheme.isidentifier() or scheme.replace("+", "").replace("-", "").isalnum(),
            f"scheme {scheme!r} must be alphanumeric (with optional + or -)")
    _ALLOWED_SCHEMES.add(scheme)


@dataclass(frozen=True)
class PayloadURI:
    scheme: str
    location: str
    fragment: str | None = None

    def __str__(self) -> str:
        base = f"{self.scheme}{_SEP}{self.location}"
        return f"{base}#{self.fragment}" if self.fragment else base


def is_payload_uri(value: str) -> bool:
    """True if `value` looks like a URI of a registered scheme (cheap check)."""
    if not isinstance(value, str) or _SEP not in value:
        return False
    return value.split(_SEP, 1)[0] in _ALLOWED_SCHEMES


def parse_payload_uri(value: str) -> PayloadURI:
    """Parse and validate a payload URI. Raises ValidationError on a malformed
    URI or unregistered scheme."""
    require_nonempty_str(value, "payload_uri")
    require(_SEP in value, f"payload_uri {value!r} must be of the form scheme://location")
    scheme, rest = value.split(_SEP, 1)
    require(scheme in _ALLOWED_SCHEMES,
            f"unsupported payload-URI scheme {scheme!r} "
            f"(allowed: {sorted(_ALLOWED_SCHEMES)})")
    if "#" in rest:
        location, fragment = rest.split("#", 1)
    else:
        location, fragment = rest, None
    require_nonempty_str(location, "payload_uri.location")
    return PayloadURI(scheme=scheme, location=location, fragment=fragment or None)


def validate_payload_uri(value: str) -> None:
    parse_payload_uri(value)


def build_payload_uri(scheme: str, location: str, fragment: str | None = None) -> str:
    """Construct a conformant payload URI (validated)."""
    require(scheme in _ALLOWED_SCHEMES,
            f"unsupported payload-URI scheme {scheme!r}")
    require_nonempty_str(location, "location")
    uri = f"{scheme}{_SEP}{location}"
    if fragment:
        uri += f"#{fragment}"
    return str(parse_payload_uri(uri))
