"""Build and inspect a crforge opponent league.

The league file is the native ``v4-ppo-policy-league.v1`` JSON, so the native
trainer and the crforge producer read the same pool::

    python tools/crforge/league.py create --anchor checkpoints/General/checkpoint-step-00000460.pt \
        --output runs/crforge-league.json
    python tools/crforge/league.py add runs/crforge-league.json \
        .resource/RoyaleFish/models/B-general-v275.pt
    python tools/crforge/league.py add runs/crforge-league.json \
        .resource/RoyaleFish/models/A-hog26-v738.pt \
        --deck-id 523b6428074d4689904add37912722f47182bc6a1679dd89c5d50a19292263d3
    python tools/crforge/league.py show runs/crforge-league.json

Then train against it with::

    python tools/crforge/self_play.py --opponent-league runs/crforge-league.json ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from crforge_bridge.league import CrforgeOpponentLeague, OpponentDeck, add_checkpoint, create_league


def _add_deck_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--deck-id", default=None, help="a deck id from --deck-pool")
    parser.add_argument("--deck-json", type=Path, default=None, help="a deck-pool deck object in a JSON file")
    parser.add_argument("--deck-pool", type=Path, default=Path("runs/deck-pool.json"))


def _resolve_deck(args: argparse.Namespace) -> OpponentDeck | None:
    if args.deck_id is not None and args.deck_json is not None:
        raise SystemExit("pass --deck-id or --deck-json, not both")
    if args.deck_json is not None:
        raw = json.loads(args.deck_json.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise SystemExit(f"{args.deck_json} is not a deck object")
        return OpponentDeck.from_mapping(raw)
    if args.deck_id is not None:
        return OpponentDeck.from_pool(args.deck_pool, args.deck_id)
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create an empty league around an anchor checkpoint")
    create.add_argument("--anchor", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--anchor-id", default=None, help="override the checkpoint_id read from the payload")

    add = commands.add_parser("add", help="add a checkpoint as a frozen opponent")
    add.add_argument("league", type=Path)
    add.add_argument("checkpoint", type=Path)
    add.add_argument("--update-step", type=int, default=None)
    _add_deck_arguments(add)

    deck = commands.add_parser("deck", help="attach or replace one entry's deck")
    deck.add_argument("league", type=Path)
    deck.add_argument("checkpoint_id")
    _add_deck_arguments(deck)

    show = commands.add_parser("show", help="print the league and its PFSP weights")
    show.add_argument("league", type=Path)

    sample = commands.add_parser("sample", help="draw one PFSP sample")
    sample.add_argument("league", type=Path)
    sample.add_argument("--seed", type=int, default=0)

    record = commands.add_parser("record", help="record one game result for an entry")
    record.add_argument("league", type=Path)
    record.add_argument("checkpoint_id")
    record.add_argument("result", type=float, choices=(-1.0, 0.0, 1.0))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "create":
        if args.output.exists():
            raise SystemExit(f"{args.output} already exists; remove it or choose another path")
        league = create_league(args.output, anchor=args.anchor, anchor_id=args.anchor_id)
        print(json.dumps(league.summary(), indent=2))
        return 0
    if args.command == "add":
        league = add_checkpoint(
            args.league, args.checkpoint, update_step=args.update_step, deck=_resolve_deck(args)
        )
        print(json.dumps(league.summary(), indent=2))
        return 0
    league = CrforgeOpponentLeague.load(args.league, seed=getattr(args, "seed", 0))
    if args.command == "deck":
        deck = _resolve_deck(args)
        if deck is None:
            raise SystemExit("pass --deck-id or --deck-json")
        league.set_deck(args.checkpoint_id, deck)
        league.save()
        print(json.dumps(league.summary(), indent=2))
        return 0
    if args.command == "show":
        summary = league.summary()
        summary["entries"] = [
            {**entry, "pfsp_weight": round(item.pfsp_weight, 6), "games": item.games}
            for entry, item in zip(summary["entries"], league.entries, strict=True)
        ]
        print(json.dumps(summary, indent=2))
        return 0
    if args.command == "sample":
        print(json.dumps(league.sample().as_dict(), indent=2))
        return 0
    if args.command == "record":
        league.record(args.checkpoint_id, args.result)
        league.save()
        print(json.dumps(league.summary(), indent=2))
        return 0
    raise SystemExit(f"unknown command {args.command}")


if __name__ == "__main__":
    sys.exit(main())
