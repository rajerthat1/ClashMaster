"""Prepare a deck pool for a run: optional Tower Troop filter plus a focus list.

The upstream PPO recipe ships a curated 20-deck focus list next to a 7,695-deck
usage-filtered pool.  The local 252,238-replay corpus produces a 7,089-deck
pool at ``--minimum-uses 5`` and no curated focus list, so this tool derives a
deterministic one from observed usage instead of rescanning the parquet corpus.

``--exclude-tower-troop`` drops identities that need native Tower Troop runtime
telemetry.  The 16.635.1 probe does not install that ring yet, so Dagger Duchess
(159000002) and Royal Chef (159000004) matches fail closed during reset.

    python -m native_runner.training.v4.select_focus_decks \
        --deck-pool runs/deck-pool.json --top 20 \
        --exclude-tower-troop 159000002 --exclude-tower-troop 159000004 \
        --output-pool runs/deck-pool-a51.json \
        --output-list runs/focus-decks-20.json
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

from .deck_pool import DeckPoolManifestV4, mark_focus_decks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deck-pool", type=Path, required=True)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--exclude-tower-troop", type=int, action="append", default=[])
    parser.add_argument("--output-pool", type=Path, required=True)
    parser.add_argument("--output-list", type=Path)
    args = parser.parse_args()

    if args.top <= 0:
        raise ValueError("--top must be positive")
    manifest = DeckPoolManifestV4.load(args.deck_pool)
    excluded = frozenset(args.exclude_tower_troop)

    kept = manifest.decks
    if excluded:
        selected = tuple(item for item in kept if item.tower_troop_id not in excluded)
        if not selected:
            raise ValueError("tower-troop filter removed every deck")
        metadata = {
            **dict(manifest.metadata),
            "excluded_tower_troop_ids": sorted(excluded),
            "source_pool_size": len(kept),
        }
        kept = selected
        manifest = DeckPoolManifestV4(decks=kept, minimum_uses=manifest.minimum_uses, metadata=metadata)

    # Re-derive focus ranks from the surviving identities.
    manifest = DeckPoolManifestV4(
        decks=tuple(replace(item, focus_rank=None) for item in manifest.decks),
        minimum_uses=manifest.minimum_uses,
        metadata=manifest.metadata,
    )
    if len(manifest.decks) < args.top:
        raise ValueError("deck pool holds fewer decks than the requested focus count")

    ordered = sorted(manifest.decks, key=lambda item: (-item.uses, item.deck_id))[: args.top]
    identities = tuple((item.card_keys, item.tower_key) for item in ordered)
    focused = mark_focus_decks(manifest, identities)
    args.output_pool.parent.mkdir(parents=True, exist_ok=True)
    focused.save(args.output_pool)

    payload = {
        "decks": [
            {"rank": rank, "card_keys": list(item.card_keys), "tower_key": item.tower_key, "uses": item.uses}
            for rank, item in enumerate(ordered, start=1)
        ]
    }
    if args.output_list is not None:
        args.output_list.parent.mkdir(parents=True, exist_ok=True)
        args.output_list.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "deck_pool": str(args.output_pool.resolve()),
                "focus_decks": len(focused.focus_decks),
                "pool_size": len(focused.decks),
                "minimum_uses": focused.minimum_uses,
                "top_focus_uses": ordered[0].uses if ordered else 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
