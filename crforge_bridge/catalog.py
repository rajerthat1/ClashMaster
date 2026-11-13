"""FirstLight card identity <-> crforge row name.

The catalog is generated once from ``native_runner.card_specs`` and a crforge
``Vocabulary`` (see ``native_runner/data/competitive/crforge_catalog.json``).
It is a checked-in generated file: a version bump in either project rebuilds it
and the diff is reviewed, rather than guessing a name at run time.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

DEFAULT_CATALOG = (
    Path(__file__).resolve().parents[1] / "native_runner" / "data" / "competitive" / "crforge_catalog.json"
)

# Native form codes, from the client's packed card item.
FORM_BASE = 0
FORM_EVOLVED = 1
FORM_HERO = 2

# crforge deck slot flags (Schema.SLOT_*).
SLOT_EVOLUTION = 1
SLOT_HERO = 2

# Tower troop support card ids -> crforge tower selection row names.
TOWER_SELECTIONS: dict[int, str] = {
    159_000_000: "King_PrincessTowers",
    159_000_001: "King_CannonTowers",
    159_000_002: "King_KnifeTowers",
    159_000_004: "King_ChefTowers",
}


class CatalogError(ValueError):
    """A card the bridge cannot name in crforge, or a malformed catalog."""


@dataclass(frozen=True)
class Catalog:
    cards: dict[int, str]
    forms: dict[int, dict[str, str]]
    reverse: dict[str, int]
    data_versions: tuple[str, ...]
    content_sha: dict[str, str]

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CATALOG) -> "Catalog":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise CatalogError(f"unsupported crforge catalog: {path}")
        cards = {int(key): str(value) for key, value in raw.get("cards", {}).items()}
        forms = {
            int(key): {str(k): str(v) for k, v in value.items()}
            for key, value in raw.get("forms", {}).items()
        }
        reverse = {str(key): int(value) for key, value in raw.get("reverse", {}).items()}
        if not cards or not reverse:
            raise CatalogError("the crforge catalog is empty")
        return cls(
            cards=cards,
            forms=forms,
            reverse=reverse,
            data_versions=tuple(str(item) for item in raw.get("data_versions", ())),
            content_sha={str(k): str(v) for k, v in raw.get("content_sha", {}).items()},
        )

    def name(self, card_id: int) -> str:
        try:
            return self.cards[int(card_id)]
        except KeyError as error:
            raise CatalogError(f"FirstLight card {card_id} has no crforge row name") from error

    def card_id(self, name: str) -> int | None:
        return self.reverse.get(name)

    def form_name(self, card_id: int, form_code: int) -> str:
        """The crforge row a card id is played as in a form, or the base row."""

        base = self.name(card_id)
        if form_code == FORM_BASE:
            return base
        key = "evolution" if form_code == FORM_EVOLVED else "hero" if form_code == FORM_HERO else None
        if key is None:
            raise CatalogError(f"card {card_id} has unknown native form code {form_code}")
        variant = self.forms.get(int(card_id), {}).get(key)
        if variant is None:
            raise CatalogError(f"card {card_id} has no {key} form in the crforge catalog")
        return variant

    def deck_entry(self, card_id: int) -> dict[str, Any]:
        """The catalog facts for one deck slot, including its available forms."""

        return {
            "name": self.name(card_id),
            "forms": dict(self.forms.get(int(card_id), {})),
        }


def pack_card_parameter(
    *,
    deck_slot: int,
    level: int,
    cost: int,
    form_code: int = FORM_BASE,
    slot_flags: int = 0,
    option: int = 0,
    count: int = 0,
    cosmetic: int = 0,
) -> int:
    """Build the client's packed card-selection word.

    The layout is the one crforge checks in a replay's ``sel.pd`` (see
    ``ReplayScenario``): form in bits 0..3, option+1 in 4..6, count+1 in 7..9,
    level in 10..16, cosmetic in 17..18, slot flags in 19..21, deck index+1 in
    22..27, cost from bit 28.
    """

    if not 0 <= deck_slot < 8:
        raise ValueError(f"deck_slot must be 0..7, got {deck_slot}")
    if not 1 <= level <= 127:
        raise ValueError(f"level must be 1..127, got {level}")
    if not 0 <= cost <= 15:
        raise ValueError(f"cost must be 0..15, got {cost}")
    if not 0 <= form_code <= 15:
        raise ValueError(f"form_code must be 0..15, got {form_code}")
    if not 0 <= slot_flags <= 7:
        raise ValueError(f"slot_flags must be 0..7, got {slot_flags}")
    if not 0 <= option <= 6 or not 0 <= count <= 6 or not 0 <= cosmetic <= 3:
        raise ValueError("option, count or cosmetic is out of its packed range")
    return (
        (form_code & 0xF)
        | (((option + 1) & 0x7) << 4)
        | (((count + 1) & 0x7) << 7)
        | ((level & 0x7F) << 10)
        | ((cosmetic & 0x3) << 17)
        | ((slot_flags & 0x7) << 19)
        | (((deck_slot + 1) & 0x3F) << 22)
        | ((cost & 0xF) << 28)
    )


def unpack_card_parameter(value: int) -> dict[str, int]:
    """Read the fields the bridge and FirstLight interpret from a packed word."""

    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("card_parameter must be a uint32")
    return {
        "form_code": value & 0xF,
        "option": ((value >> 4) & 0x7) - 1,
        "count": ((value >> 7) & 0x7) - 1,
        "level": (value >> 10) & 0x7F,
        "cosmetic": (value >> 17) & 0x3,
        "slot_flags": (value >> 19) & 0x7,
        "deck_slot": ((value >> 22) & 0x3F) - 1,
        "cost": value >> 28,
    }


def slot_flags_for_availability(availability: int) -> int:
    """FirstLight's native ``el`` availability mask to crforge slot flags.

    ``el`` is 1 evolution, 2 hero, 3 both. crforge's slot flags use the same
    bit assignment, so this is deliberately an identity with a bounds check.
    """

    if availability not in (0, 1, 2, 3):
        raise ValueError(f"form availability must be 0..3, got {availability}")
    return availability
