"""Session manifest handling: (de)serialize a Session to/from disk.

Supports JSON (`.json`) and YAML (`.yaml`/`.yml`) by file extension. Loading
runs schema validation: contract-version support is checked first (clear error
on mismatch), then the full `Session.validate()` unless explicitly skipped.

This is the persistence boundary only — no synchronization logic.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .core.session import Session
from .core.types import (
    ValidationError,
    contract_version_supported,
    require_key,
)


def session_to_json(session: Session, *, indent: int = 2) -> str:
    return json.dumps(session.to_dict(), indent=indent, sort_keys=False)


def session_to_yaml(session: Session) -> str:
    return yaml.safe_dump(session.to_dict(), sort_keys=False, allow_unicode=True)


def save_session(session: Session, path: str | Path, *, validate: bool = True) -> Path:
    """Validate (optional) and write a Session manifest. Format from extension."""
    if validate:
        session.validate()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    suffix = p.suffix.lower()
    if suffix == ".json":
        p.write_text(session_to_json(session), encoding="utf-8")
    elif suffix in (".yaml", ".yml"):
        p.write_text(session_to_yaml(session), encoding="utf-8")
    else:
        raise ValidationError(f"unsupported manifest extension {p.suffix!r}; use .json/.yaml/.yml")
    return p


def _read_mapping(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
    elif suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text)
    else:
        raise ValidationError(f"unsupported manifest extension {path.suffix!r}; use .json/.yaml/.yml")
    if not isinstance(data, dict):
        raise ValidationError(f"manifest {path} must contain a mapping at the top level")
    return data


def load_session(path: str | Path, *, validate: bool = True) -> Session:
    """Read a Session manifest from disk.

    Contract-version support is checked *before* full parsing so an unsupported
    manifest fails with a precise message rather than a downstream parse error.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"manifest not found: {p}")
    data = _read_mapping(p)
    meta = require_key(data, "metadata", "session")
    cv = meta.get("contract_version") if isinstance(meta, dict) else None
    if cv is not None and not contract_version_supported(cv):
        raise ValidationError(
            f"manifest {p} declares contract_version {cv!r}, which this build does not support")
    session = Session.from_dict(data)
    if validate:
        session.validate()
    return session
