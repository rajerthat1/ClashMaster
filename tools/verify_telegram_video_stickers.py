#!/usr/bin/env python3
"""Verify the imported Telegram sticker sets against the local manifest.

Compares each set's sticker count and emoji order with
``artifacts/telegram/video-stickers/manifest.json`` and reports the public
``t.me/addstickers`` link.

Usage:
    python tools/verify_telegram_video_stickers.py --token-file ~/.config/clashmaster/telegram_bot_token
    python tools/verify_telegram_video_stickers.py --token-file ... --only-set 3
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "artifacts" / "telegram" / "video-stickers" / "manifest.json"


def read_token(args: argparse.Namespace) -> str:
    if args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    else:
        token = os.environ.get(args.token_env, "").strip()
    if not token:
        raise SystemExit(f"Telegram bot token not found. Set ${args.token_env} or use --token-file.")
    return token


def call(token: str, method: str, fields: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(fields or {}).encode("utf-8")
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=120) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--token-file", type=Path, default=None)
    parser.add_argument("--only-set", action="append", default=[], help="set number to check (repeatable)")
    args = parser.parse_args()

    token = read_token(args)
    bot = call(token, "getMe")["result"]["username"]
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    wanted = {int(value) for value in args.only_set}

    problems = 0
    for set_info in manifest["sets"]:
        if wanted and int(set_info["index"]) not in wanted:
            continue
        name = f"{set_info['name']}_by_{bot}"
        try:
            result = call(token, "getStickerSet", {"name": name})["result"]
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"{set_info['index']}: MISSING ({name}): {exc}")
            problems += 1
            continue
        stickers = result.get("stickers") or []
        got = [sticker.get("emoji") for sticker in stickers]
        want = [sticker["emoji"] for sticker in set_info["stickers"]]
        unique = len({sticker["file_unique_id"] for sticker in stickers})
        ok = got == want and len(stickers) == int(set_info["count"]) and unique == len(stickers)
        if not ok:
            problems += 1
            print(
                f"{set_info['index']}: MISMATCH {name} "
                f"count={len(stickers)}/{set_info['count']} unique={unique} order_match={got == want}"
            )
            for index, (actual, expected) in enumerate(zip(got, want)):
                if actual != expected:
                    print(f"  first difference at position {index + 1}: {actual} != {expected}")
                    break
            continue
        print(f"{set_info['index']}: OK {len(stickers)} stickers  https://t.me/addstickers/{name}")

    print("\nall sets verified" if not problems else f"\n{problems} set(s) need attention")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
