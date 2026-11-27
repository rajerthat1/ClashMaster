#!/usr/bin/env python3
"""Bulk-import the prepared video stickers into Telegram via the Bot API.

This is optional: you can also upload the six sets manually with @Stickers.
The Bot API path creates the sets for your Telegram account and adds every
sticker without the one-by-one bot conversation.

Setup:
  1. Create a bot with @BotFather (/newbot) and copy its token.
  2. Put the token in an environment variable (recommended):
         export TELEGRAM_BOT_TOKEN='123456:ABC...'
     or in a local file and pass --token-file /path/to/token.
  3. Open the new bot in Telegram, press Start, and send /start.
  4. Run this script with your numeric user ID:
         python tools/telegram_import_video_stickers.py --user-id 123456789 --execute
     Or let it discover the ID from your /start message:
         python tools/telegram_import_video_stickers.py --username rajerthat --execute

Without --execute the script only prints the plan. The token is never printed.
Set names get the Bot API-required ``_by_<bot_username>`` suffix; titles come
from the manifest. The first sticker is used as the set thumbnail.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "artifacts" / "telegram" / "video-stickers" / "manifest.json"


def read_token(args: argparse.Namespace) -> str:
    if args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    else:
        token = os.environ.get(args.token_env, "").strip()
    if not token:
        raise SystemExit(
            f"Telegram bot token not found. Set ${args.token_env} or use --token-file."
        )
    return token


def api_call(token: str, method: str, fields: dict | None = None, files: dict | None = None,
             timeout: float = 180.0) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    fields = fields or {}
    if not files:
        data = urllib.parse.urlencode(fields).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
    else:
        boundary = f"----ClashMaster{uuid.uuid4().hex}"
        body = bytearray()
        for name, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        for name, (filename, payload, content_type) in files.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
            )
            body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
            body.extend(payload)
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            url,
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", "replace")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            raise RuntimeError(f"{method}: HTTP {exc.code}: {payload[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method}: network error: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        # Read timeouts and dropped connections surface outside URLError.
        raise RuntimeError(f"{method}: network error: {exc}") from exc
    if not data.get("ok"):
        description = data.get("description", "unknown error")
        parameters = data.get("parameters", {})
        raise RuntimeError(f"{method}: {description} {parameters}".strip())
    return data.get("result", {})


def retry_call(token: str, method: str, fields: dict, files: dict | None = None,
               attempts: int = 8) -> dict:
    for attempt in range(1, attempts + 1):
        try:
            return api_call(token, method, fields, files)
        except RuntimeError as exc:
            message = str(exc)
            lowered = message.lower()
            if "too many requests" in lowered or "retry after" in lowered:
                wait = min(30, 2 ** attempt)
                print(f"  rate limited, waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
            if "network error" in lowered or "timed out" in lowered or "connection" in lowered:
                wait = min(20, 2 ** attempt)
                print(f"  network error, retrying in {wait}s...", flush=True)
                time.sleep(wait)
                continue
            if "already exists" in lowered or "already occupied" in lowered:
                if method == "createNewStickerSet":
                    return {"already_exists": True}
            raise
    raise RuntimeError(f"{method}: too many retries")


def discover_user_id(token: str, username: str) -> int | None:
    updates = api_call(token, "getUpdates", {"limit": "100", "allowed_updates": '["message"]'})
    matches = []
    for update in updates:
        message = update.get("message") or update.get("edited_message") or {}
        sender = message.get("from") or {}
        if (sender.get("username") or "").lower() == username.lower():
            matches.append((message.get("date", 0), sender.get("id")))
    if not matches:
        return None
    matches.sort()
    return int(matches[-1][1])


def set_stickers(token: str, set_name: str) -> list[dict]:
    try:
        result = api_call(token, "getStickerSet", {"name": set_name})
    except RuntimeError:
        return []
    if isinstance(result, dict):
        return result.get("stickers") or []
    return []


def set_sticker_count(token: str, set_name: str) -> int | None:
    result = set_stickers(token, set_name)
    return len(result) if result else None


def trim_to(token: str, set_name: str, expected: int) -> None:
    """Delete trailing stickers until the set holds ``expected`` stickers.

    A timed-out addStickerToSet may still have been applied server-side, so a
    retry can insert a duplicate. Trimming keeps the uploaded order aligned
    with the manifest.
    """
    stickers = set_stickers(token, set_name)
    while len(stickers) > expected:
        print(f"  removing duplicate at position {len(stickers)}", flush=True)
        api_call(token, "deleteStickerFromSet", {"sticker": stickers[-1]["file_id"]})
        stickers = set_stickers(token, set_name)
        if not stickers:
            break


def input_sticker(file_name: str, emoji: str, human_name: str | None = None) -> dict:
    sticker = {
        "sticker": "attach://sticker",
        "format": "video",
        "emoji_list": [emoji or "😀"],
    }
    if human_name:
        sticker["keywords"] = [human_name[:64]]
    return sticker


def add_sticker(token: str, user_id: int, set_name: str, file_path: Path, emoji: str,
                human_name: str | None, delay: float, expected: int) -> None:
    sticker = json.dumps(input_sticker(file_path.name, emoji, human_name), ensure_ascii=False)
    fields = {"user_id": str(user_id), "name": set_name, "sticker": sticker}
    payload = file_path.read_bytes()
    files = {"sticker": (file_path.name, payload, "video/webm")}
    retried = False
    for attempt in range(1, 6):
        try:
            api_call(token, "addStickerToSet", fields, files)
            break
        except RuntimeError as exc:
            lowered = str(exc).lower()
            if "too many requests" in lowered or "retry after" in lowered:
                time.sleep(min(30, 2 ** attempt))
                retried = True
                continue
            if "sticks" in lowered and "much" in lowered:
                raise
            if "network" in lowered or "timed out" in lowered or "connection" in lowered:
                time.sleep(min(20, 2 ** attempt))
                retried = True
                continue
            if "stickerset_invalid" in lowered or "stickset_invalid" in lowered:
                # Freshly recreated sets can briefly reject writes.
                time.sleep(2 * attempt)
                retried = True
                continue
            raise
    else:
        raise RuntimeError(f"addStickerToSet: too many retries for {file_path.name}")
    # Only verify after an ambiguous failure; adds are otherwise fire-and-forget.
    if retried:
        trim_to(token, set_name, expected)
    time.sleep(delay)


def create_set(token: str, user_id: int, set_name: str, title: str, first: Path, emoji: str,
               human_name: str | None, delay: float) -> bool:
    stickers = json.dumps([input_sticker(first.name, emoji, human_name)], ensure_ascii=False)
    fields = {
        "user_id": str(user_id),
        "name": set_name,
        "title": title,
        "stickers": stickers,
        "sticker_type": "regular",
    }
    payload = first.read_bytes()
    files = {"sticker": (first.name, payload, "video/webm")}
    result = retry_call(token, "createNewStickerSet", fields, files)
    time.sleep(delay)
    # createNewStickerSet returns True on success, or the {"already_exists": True}
    # marker injected by retry_call when the set name is taken.
    return not (isinstance(result, dict) and result.get("already_exists"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--token-file", type=Path, default=None)
    parser.add_argument("--user-id", type=int, default=0, help="numeric Telegram user ID")
    parser.add_argument("--username", default="rajerthat", help="username used to discover the user ID")
    parser.add_argument("--only-set", action="append", default=[], help="set number to import (repeatable)")
    parser.add_argument("--delay", type=float, default=0.12, help="seconds between API calls")
    parser.add_argument(
        "--force-recreate",
        action="store_true",
        help="delete and rebuild the selected sets from scratch",
    )
    parser.add_argument("--execute", action="store_true", help="actually create/add stickers")
    args = parser.parse_args()

    if not args.manifest.exists():
        parser.error(f"manifest not found: {args.manifest}")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    sets = manifest.get("sets", [])
    if args.only_set:
        wanted = {int(value) for value in args.only_set}
        sets = [s for s in sets if int(s["index"]) in wanted]
    if not sets:
        parser.error("no sets selected")

    if not args.execute:
        print("DRY RUN — no Telegram changes will be made")
        for set_info in sets:
            print(
                f"{set_info['name']}: {set_info['count']} stickers, "
                f"title={set_info['title']!r}, icon={set_info.get('icon')}"
            )
        print(f"total: {sum(s['count'] for s in sets)} stickers")
        print("Re-run with --execute (and a bot token) to import.")
        return 0

    token = read_token(args)
    me = api_call(token, "getMe")
    bot_username = me.get("username") or ""
    if not bot_username:
        raise SystemExit("getMe did not return a bot username")
    print(f"bot: @{bot_username}")

    user_id = args.user_id
    if not user_id:
        print(f"looking for a /start message from @{args.username}...")
        user_id = discover_user_id(token, args.username)
        if not user_id:
            raise SystemExit(
                f"Could not find a message from @{args.username}. Open the bot in Telegram, "
                "press Start, then retry, or pass --user-id <numeric id>."
            )
        print(f"user id: {user_id}")
    else:
        print(f"user id: {user_id}")

    for set_info in sets:
        count = int(set_info["count"])
        directory = args.manifest.parent / set_info["directory"]
        sticker_files = [directory / sticker["file"] for sticker in set_info["stickers"]]
        missing = [str(path) for path in sticker_files if not path.exists()]
        if missing:
            raise SystemExit(f"{set_info['name']}: missing files: {missing[:3]}")
        set_name = f"{set_info['name']}_by_{bot_username}"
        print(f"\n{set_info['title']} -> {set_name} ({count} stickers)")
        if args.force_recreate:
            existing = set_stickers(token, set_name)
            if existing:
                print(f"  deleting existing set ({len(existing)} stickers)")
                try:
                    api_call(token, "deleteStickerSet", {"name": set_name})
                except RuntimeError as exc:
                    if "not found" not in str(exc).lower():
                        raise
                time.sleep(1)
        created = create_set(
            token,
            user_id,
            set_name,
            set_info["title"],
            sticker_files[0],
            set_info["stickers"][0].get("emoji", "😀"),
            set_info["stickers"][0].get("human_name"),
            args.delay,
        )
        if created:
            print("  createNewStickerSet accepted the set")
            time.sleep(1)
        # createNewStickerSet can succeed for an existing set name, so never trust
        # the return value alone: read the set and continue after what is there.
        already = set_sticker_count(token, set_name) or 0
        print(f"  set has {already} stickers, continuing from {already + 1}")
        for index, (sticker, path) in enumerate(zip(set_info["stickers"], sticker_files), start=1):
            if index <= already:
                continue
            try:
                add_sticker(
                    token,
                    user_id,
                    set_name,
                    path,
                    sticker.get("emoji", "😀"),
                    sticker.get("human_name"),
                    args.delay,
                    index,
                )
            except RuntimeError as exc:
                if "too much" in str(exc).lower() or "too_much" in str(exc).lower():
                    print(
                        f"  Telegram reports the set is full at {set_sticker_count(token, set_name)}: "
                        f"{exc}. Stopping this set; re-run with --force-recreate.",
                        flush=True,
                    )
                    break
                raise
            if index % 20 == 0 or index == count:
                print(f"  added {index}/{count}", flush=True)
        final = set_sticker_count(token, set_name) or 0
        if final < count:
            print(f"  INCOMPLETE: {final}/{count} — re-run this set to continue")
        print(f"  done: https://t.me/addstickers/{set_name}")

    print("\nAll selected sets imported. The first sticker is used as the set thumbnail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
