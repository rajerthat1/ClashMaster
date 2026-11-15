"""Selectable competitive-data releases for V4 checkpoints.

A V4 checkpoint is bound to the exact catalog (and, for 16.x, observation
schema) it was trained on.  This repo used to hard-pin a single 15.535 release.
The 16.635 line needs the 16.635 catalogs **next to** the old ones, not instead
of them: a paired evaluation can hold a 15.535 policy and a 16.635 policy in the
same match, each with its own tensorizer.

Design:

* the default release is ``None`` — the module-level constants and the pinned
  ``native_runner/data/competitive`` manifest, exactly as before;
* a named release is a directory plus a JSON descriptor under
  ``native_runner/releases/``.  Its compiled files are verified against the
  descriptor's own manifest SHA-256, so adopting a new release never disables
  integrity checking;
* the active release is a :class:`contextvars.ContextVar`, so two threads (or
  two policy sessions) can read different catalogs in one process.

Releases are selected explicitly at model-load time, never by an environment
variable at import time: module-level compatibility facts (such as the default
form evidence) are built once against the default release.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterator, Mapping

PACKAGE_ROOT = Path(__file__).resolve().parent
RELEASE_DIR = PACKAGE_ROOT / "releases"


@dataclass(frozen=True, slots=True)
class ReleaseIdentity:
    """Pin values a release uses in place of the module-level constants."""

    source_release: str
    source_card_count: int
    policy_card_count: int
    ability_vocab_size: int
    child_archetype_count: int
    card_runtime_feature_dim: int
    card_specs_hash: str | None = None
    ability_specs_hash: str | None = None
    policy_card_ids: frozenset[int] = frozenset()
    excluded_card_ids: frozenset[int] = frozenset()
    form_evidence_sha256: str | None = None
    mechanic_readiness_report_id: str | None = None
    static_catalog_id: str | None = None
    case_scope_id: str | None = None
    catalog_source_files_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class CompetitiveRelease:
    name: str
    data_root: Path
    manifest_sha256: str
    identity: ReleaseIdentity
    description: str = ""

    @property
    def manifest_path(self) -> Path:
        return self.data_root / "manifest.json"


_ACTIVE: ContextVar[CompetitiveRelease | None] = ContextVar("competitive_release", default=None)


def active_release() -> CompetitiveRelease | None:
    return _ACTIVE.get()


@contextmanager
def use_release(release: CompetitiveRelease | None) -> Iterator[CompetitiveRelease | None]:
    token = _ACTIVE.set(release)
    try:
        yield release
    finally:
        _ACTIVE.reset(token)


def _resolve_data_root(relative: str) -> Path:
    path = Path(relative)
    return path if path.is_absolute() else (PACKAGE_ROOT.parent / path)


def load_release(name_or_path: str | Path) -> CompetitiveRelease:
    """Load and integrity-check one release descriptor."""

    candidate = Path(name_or_path)
    if candidate.suffix != ".json":
        candidate = RELEASE_DIR / f"{name_or_path}.json"
        if not candidate.is_file():
            candidate = RELEASE_DIR / f"{name_or_path}.release.json"
    if not candidate.is_file():
        raise FileNotFoundError(f"unknown competitive release: {name_or_path}")
    raw = json.loads(candidate.read_text(encoding="utf-8"))
    identity = raw.get("identity") or {}
    release = CompetitiveRelease(
        name=str(raw["name"]),
        data_root=_resolve_data_root(str(raw["data_root"])),
        manifest_sha256=str(raw["manifest_sha256"]),
        identity=ReleaseIdentity(
            source_release=str(identity["source_release"]),
            source_card_count=int(identity["source_card_count"]),
            policy_card_count=int(identity["policy_card_count"]),
            ability_vocab_size=int(identity["ability_vocab_size"]),
            child_archetype_count=int(identity["child_archetype_count"]),
            card_runtime_feature_dim=int(identity["card_runtime_feature_dim"]),
            card_specs_hash=identity.get("card_specs_hash"),
            ability_specs_hash=identity.get("ability_specs_hash"),
            policy_card_ids=frozenset(int(v) for v in identity.get("policy_card_ids", ())),
            excluded_card_ids=frozenset(int(v) for v in identity.get("excluded_card_ids", ())),
            form_evidence_sha256=identity.get("form_evidence_sha256"),
            mechanic_readiness_report_id=identity.get("mechanic_readiness_report_id"),
            static_catalog_id=identity.get("static_catalog_id"),
            case_scope_id=identity.get("case_scope_id"),
            catalog_source_files_sha256=identity.get("catalog_source_files_sha256"),
        ),
        description=str(raw.get("description", "")),
    )
    if not release.data_root.is_dir():
        raise FileNotFoundError(f"release data root not found: {release.data_root}")
    import hashlib

    manifest = release.manifest_path
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != release.manifest_sha256:
        raise ValueError(f"release manifest SHA-256 mismatch: {manifest}")
    return release


def available_release_names() -> tuple[str, ...]:
    if not RELEASE_DIR.is_dir():
        return ()
    return tuple(sorted(path.stem.replace(".release", "") for path in RELEASE_DIR.glob("*.json")))


def current_identity() -> ReleaseIdentity:
    """Identity of the active release, or the legacy 15.535 constants."""

    release = active_release()
    if release is not None:
        return release.identity
    from . import semantic_subset as legacy
    from . import normal_form_evidence
    from . import runtime_scope
    from .training.v4.catalog import NORMAL_MODE_POLICY_CARD_COUNT
    from .training.v4.config import CARD_RUNTIME_FEATURE_NAMES, ModelConfigV4

    defaults = ModelConfigV4()
    return ReleaseIdentity(
        source_release=legacy.NORMAL_MODE_POLICY_SOURCE_RELEASE,
        source_card_count=runtime_scope.NORMAL_MODE_SOURCE_CARD_COUNT,
        policy_card_count=NORMAL_MODE_POLICY_CARD_COUNT,
        ability_vocab_size=defaults.ability_vocab_size,
        child_archetype_count=defaults.child_archetype_count,
        card_runtime_feature_dim=len(CARD_RUNTIME_FEATURE_NAMES),
        card_specs_hash=legacy.NORMAL_MODE_POLICY_CARD_SPECS_HASH,
        ability_specs_hash=legacy.NORMAL_MODE_POLICY_ABILITY_SPECS_HASH,
        policy_card_ids=legacy.NORMAL_MODE_POLICY_CARD_IDS,
        excluded_card_ids=legacy.NORMAL_MODE_POLICY_EXCLUDED_CARD_IDS,
        form_evidence_sha256=normal_form_evidence.NORMAL_MODE_POLICY_FORM_EVIDENCE_SHA256,
        mechanic_readiness_report_id=legacy.NORMAL_MODE_POLICY_MECHANICS_READINESS_REPORT_ID,
        static_catalog_id=legacy.NORMAL_MODE_POLICY_STATIC_CATALOG_ID,
        case_scope_id=legacy.NORMAL_MODE_POLICY_CASE_SCOPE_ID,
    )


def identity_value(name: str, legacy: Any) -> Any:
    """Return the active release's value for ``name``, else ``legacy``."""

    release = active_release()
    if release is None:
        return legacy
    return getattr(release.identity, name)


def release_manifest() -> Mapping[str, Any] | None:
    release = active_release()
    if release is None:
        return None
    return json.loads(release.manifest_path.read_text(encoding="utf-8"))
