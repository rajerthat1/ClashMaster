from __future__ import annotations

import json
from pathlib import Path

from crforge_bridge.catalog import Catalog
from native_runner.card_specs import build_card_catalog
from native_runner.match_factory import MatchConfig, ROYAL_GIANT_DECK, STANDARD_DECK
from native_runner.semantic_subset import SEMANTIC_BASELINE_DECK

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "competitive" / "crforge_catalog.json"
FORM_KEYS = {"evolution", "hero"}


def test_every_deck_card_has_a_crforge_row_name() -> None:
    catalog = Catalog.load()
    decks = {
        "STANDARD_DECK": STANDARD_DECK,
        "ROYAL_GIANT_DECK": ROYAL_GIANT_DECK,
        "SEMANTIC_BASELINE_DECK": SEMANTIC_BASELINE_DECK,
        "MatchConfig.deck0": MatchConfig().deck0,
        "MatchConfig.deck1": MatchConfig().deck1,
    }
    missing = {
        name: [card_id for card_id in deck if card_id not in catalog.cards]
        for name, deck in decks.items()
    }
    assert not {name: cards for name, cards in missing.items() if cards}, (
        "deck cards missing from crforge_catalog.json: " + json.dumps(missing, sort_keys=True)
    )


def test_the_generated_catalog_covers_the_whole_card_catalog() -> None:
    catalog = Catalog.load()
    specs = build_card_catalog().specs
    missing = sorted(spec.card_id for spec in specs if spec.card_id not in catalog.cards)
    assert not missing, f"card specs missing from crforge_catalog.json: {missing}"
    assert len(catalog.cards) == len(specs)


def test_catalog_forms_are_named_and_unique() -> None:
    catalog = Catalog.load()
    seen: set[str] = set()
    for card_id, forms in catalog.forms.items():
        assert set(forms).issubset(FORM_KEYS), f"card {card_id} has an unknown form key"
        for form_name in forms.values():
            assert form_name and form_name != catalog.name(card_id)
            assert form_name not in seen, f"duplicate form row name {form_name!r}"
            seen.add(form_name)

    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["unmapped"] == []
    assert raw["client_versions"]
    assert raw["data_versions"]
    assert set(raw["content_sha"]) == set(raw["data_versions"])
