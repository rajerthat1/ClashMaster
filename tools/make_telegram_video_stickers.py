#!/usr/bin/env python3
"""Convert extracted Clash Royale emote animations into Telegram video stickers.

Telegram video sticker requirements (https://core.telegram.org/stickers):
  * 512x512, or one side exactly 512 and the other <= 512
  * VP9 .webm, alpha transparency, no audio
  * <= 3 seconds, <= 30 fps, <= 256 KB
  * max 120 stickers per set

The script reads ``artifacts/emotes/animations/*.webp`` and
``artifacts/emotes/index.json``, trims transparent padding, resamples to
30 fps, fits the art on a transparent canvas, and encodes one VP9 file per
emote.  It then splits the stickers into sequential sets and writes a manifest
plus per-set upload lists.

Usage:
    python tools/make_telegram_video_stickers.py
    python tools/make_telegram_video_stickers.py --limit 8 --workers 2
    python tools/make_telegram_video_stickers.py --max-per-set 120 --max-size-kb 250
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ANIMATIONS = ROOT / "artifacts" / "emotes" / "animations"
DEFAULT_INDEX = ROOT / "artifacts" / "emotes" / "index.json"
DEFAULT_ICONS = ROOT / "artifacts" / "emotes" / "icons"
DEFAULT_OUT = ROOT / "artifacts" / "telegram" / "video-stickers"

FPS = 30
MAX_DURATION_MS = 3000
SAFE_DURATION_MS = 2967  # leave margin under Telegram's 3 s limit
MARGIN = 8
DEFAULT_CRFS = (28, 30, 32, 34, 36, 38, 40)
DEFAULT_EMOJI = "😀"

# Normalised family name -> emoji. Unknown families fall back to DEFAULT_EMOJI.
FAMILY_EMOJI = {
    "": "😀",
    "a": "😀",
    "archer": "🏹",
    "archerqueen": "🏹",
    "axeman": "🪓",
    "babydragon": "🐉",
    "balloon": "🎈",
    "bandit": "🗡️",
    "barbarian": "🪓",
    "barbarian8bit": "🪓",
    "barbarianbarrel": "🛢️",
    "bat": "🦇",
    "bats": "🦇",
    "battlehealer": "✨",
    "battleram": "🐏",
    "berserker": "🪓",
    "blowdartgoblin": "🎯",
    "bomber": "💣",
    "bossbandit": "🗡️",
    "bowler": "🎳",
    "crl": "🏆",
    "cannon": "💣",
    "cannoneer": "💣",
    "chef": "👨‍🍳",
    "daggerduchess": "🗡️",
    "darkprince": "🗡️",
    "dartgoblin": "🎯",
    "dragon": "🐉",
    "electrodragon": "⚡",
    "electrogiant": "⚡",
    "electrowiz": "⚡",
    "elitebarbarians": "🪓",
    "elixirloong": "🐉",
    "executioner": "🪓",
    "firecracker": "🧨",
    "fisherman": "🎣",
    "furnace": "🔥",
    "generic": "😀",
    "ghost": "👻",
    "giant": "🦣",
    "giantbuffer": "🦣",
    "giantgoblin": "🦣",
    "goblin": "👺",
    "goblinbarrel": "🛢️",
    "goblinage": "🪤",
    "goblincage": "🪤",
    "goblindrill": "🪛",
    "goblingiant": "👺",
    "goblinqueen": "👺",
    "goblinstein": "👺",
    "goldenknight": "⚔️",
    "golem": "🪨",
    "guard": "🛡️",
    "hog": "🐗",
    "hogrider": "🐗",
    "hunter": "🏹",
    "icegolem": "❄️",
    "icewizard": "❄️",
    "inferno": "🔥",
    "king": "👑",
    "kingchess": "👑",
    "king8bit": "👑",
    "knight": "⚔️",
    "lavahound": "🌋",
    "littleprince": "👑",
    "log": "🪵",
    "lumberjack": "🪓",
    "magicarcher": "🏹",
    "megaknight": "⚔️",
    "megaminion": "🦅",
    "mightyminer": "⛏️",
    "miner": "⛏️",
    "minipekka": "🤖",
    "minion": "🦅",
    "miniongiant": "🦅",
    "minionhorde": "🦅",
    "monk": "🧘",
    "mortar": "💣",
    "motherwitch": "🧙‍♀️",
    "musketeer": "🎯",
    "nightwitch": "🌙",
    "pekka": "🤖",
    "phoenix": "🔥",
    "prince": "🐴",
    "princess": "🎀",
    "ragebarbarian": "😡",
    "ramrider": "🐏",
    "rascal": "🐿️",
    "rascals": "🐿️",
    "rocket": "🚀",
    "ronin": "⚔️",
    "royalghost": "👻",
    "royalhogs": "🐖",
    "royalgiant": "🦣",
    "royalrecruit": "🛡️",
    "royals": "👑",
    "skeleton": "💀",
    "skeletondragon": "💀",
    "skeletongiant": "💀",
    "skeletonking": "💀",
    "skeletons": "💀",
    "snowball": "❄️",
    "sparky": "⚡",
    "spirit": "🔥",
    "suspiciousbush": "🌳",
    "tesla": "⚡",
    "text": "🔤",
    "tombstone": "🪦",
    "valkyrie": "⚔️",
    "vine": "🌿",
    "wallbreakers": "💥",
    "witch": "🧙‍♀️",
    "wizard": "🧙",
    "xbow": "🏹",
    "edragon": "🐉",
    "egiant": "⚡",
    "egolem": "🪨",
    "ewizard": "⚡",
}


def emoji_for(family: str | None) -> str:
    key = (family or "").strip().lower().replace(" ", "")
    return FAMILY_EMOJI.get(key, DEFAULT_EMOJI)


def webp_durations(path: Path) -> list[int]:
    """Read per-frame durations from the ANMF chunks of an animated WebP."""
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError(f"{path}: not a WebP file")
    durations: list[int] = []
    pos = 12
    while pos + 8 <= len(data):
        fourcc = data[pos : pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        payload = data[pos + 8 : pos + 8 + size]
        if fourcc == b"ANMF" and len(payload) >= 15:
            durations.append(int.from_bytes(payload[12:15], "little"))
        pos += 8 + size + (size & 1)
    return durations


def load_frames(path: Path) -> tuple[list[Image.Image], list[int]]:
    image = Image.open(path)
    frames = []
    for i in range(getattr(image, "n_frames", 1)):
        image.seek(i)
        frames.append(image.convert("RGBA"))
    durations = webp_durations(path)
    if not durations:
        durations = [round(1000 / 60)] * len(frames)
    if len(durations) != len(frames):
        # Keep the animation usable even if the container metadata is odd.
        durations = (durations + [17] * len(frames))[: len(frames)]
    return frames, durations


def trim_transparent(frames: list[Image.Image], durations: list[int]):
    start = 0
    while start < len(frames) and frames[start].getchannel("A").getbbox() is None:
        start += 1
    end = len(frames)
    while end > start and frames[end - 1].getchannel("A").getbbox() is None:
        end -= 1
    return frames[start:end], durations[start:end], start, len(frames) - end


def union_bbox(frames: list[Image.Image]) -> tuple[int, int, int, int] | None:
    box = None
    for frame in frames:
        current = frame.getchannel("A").getbbox()
        if current is None:
            continue
        box = current if box is None else (
            min(box[0], current[0]),
            min(box[1], current[1]),
            max(box[2], current[2]),
            max(box[3], current[3]),
        )
    return box


def sample_order(durations: list[int], total_ms: int, out_frames: int) -> list[int]:
    order = []
    index = 0
    cumulative = 0
    for i in range(out_frames):
        t = i * total_ms / out_frames
        while index + 1 < len(durations) and cumulative + durations[index] <= t:
            cumulative += durations[index]
            index += 1
        order.append(index)
    return order


def ffmpeg_command(out: Path, width: int, height: int, crf: int, fps: int) -> list[str]:
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
        "-c:v", "libvpx-vp9",
        "-pix_fmt", "yuva420p",
        "-auto-alt-ref", "0",
        "-b:v", "0",
        "-crf", str(crf),
        "-row-mt", "1",
        "-cpu-used", "4",
        "-threads", "2",
        "-deadline", "good",
        "-an",
        "-f", "webm",
        str(out),
    ]


def encode(
    out: Path,
    frames: list[Image.Image],
    order: list[int],
    scale_size: tuple[int, int],
    canvas: tuple[int, int],
    crf: int,
    fps: int,
) -> tuple[int, bytes]:
    width, height = canvas
    nw, nh = scale_size
    x = (width - nw) // 2
    y = (height - nh) // 2
    process = subprocess.Popen(
        ffmpeg_command(out, width, height, crf, fps),
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    try:
        for index in order:
            frame = frames[index].resize((nw, nh), Image.Resampling.LANCZOS)
            layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            layer.alpha_composite(frame, (x, y))
            process.stdin.write(layer.tobytes())
        process.stdin.close()
    except BrokenPipeError:
        pass
    assert process.stderr is not None
    error = process.stderr.read()
    return process.wait(), error


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout.decode("utf-8", "replace"))


def validate(path: Path, max_size: int) -> tuple[bool, str, dict]:
    if not path.exists():
        return False, "missing", {}
    size = path.stat().st_size
    if size > max_size:
        return False, f"too large ({size} bytes)", {}
    try:
        info = probe(path)
    except subprocess.CalledProcessError as exc:
        return False, f"ffprobe failed: {exc}", {}
    streams = info.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if len(video) != 1:
        return False, "expected exactly one video stream", info
    if audio:
        return False, "audio stream present", info
    stream = video[0]
    if stream.get("codec_name") != "vp9":
        return False, f"codec is {stream.get('codec_name')}", info
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    if not ((width == 512 and height <= 512) or (height == 512 and width <= 512)):
        return False, f"bad dimensions {width}x{height}", info
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    if duration <= 0 or duration > 3.01:
        return False, f"bad duration {duration}", info
    rate = stream.get("r_frame_rate", "30/1")
    try:
        num, den = rate.split("/")
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 0
    if fps > 30.01:
        return False, f"fps is {fps}", info
    tags = stream.get("tags", {}) or {}
    if str(tags.get("alpha_mode", "")) != "1":
        return False, "alpha_mode tag missing", info
    return True, "ok", info


def convert_one(job: dict) -> dict:
    source = Path(job["source"])
    output = Path(job["output"])
    base = {
        "file": output.name,
        "emote": job["emote"],
        "human_name": job.get("human_name"),
        "family": job.get("family"),
        "emoji": job["emoji"],
        "source": str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source),
        "set": job["set"],
    }
    try:
        frames, durations = load_frames(source)
        frames, durations, trimmed_start, trimmed_end = trim_transparent(frames, durations)
        if not frames:
            return {**base, "status": "failed", "error": "no visible frames"}
        box = union_bbox(frames)
        if box is None:
            return {**base, "status": "failed", "error": "empty alpha bounds"}
        frames = [frame.crop(box) for frame in frames]
        crop_w, crop_h = frames[0].size
        total_ms = sum(durations)

        landscape = crop_w >= crop_h
        medium_canvas = (512, 384) if landscape else (384, 512)
        small_canvas = (512, 256) if landscape else (256, 512)
        plans = [
            {"canvas": (512, 512), "fps": 30, "crfs": job["crfs"]},
            {"canvas": medium_canvas, "fps": 30, "crfs": [28, 32, 36, 40]},
            {"canvas": small_canvas, "fps": 24, "crfs": [36, 40, 44]},
            {"canvas": small_canvas, "fps": 20, "crfs": [40, 44, 48]},
        ]

        attempts = []
        output.parent.mkdir(parents=True, exist_ok=True)
        for plan_index, plan in enumerate(plans):
            canvas = plan["canvas"]
            fps = plan["fps"]
            out_frames = min(
                int(SAFE_DURATION_MS * fps / 1000),
                max(1, round(total_ms * fps / 1000)),
            )
            order = sample_order(durations, total_ms, out_frames)
            scale = min((canvas[0] - 2 * MARGIN) / crop_w, (canvas[1] - 2 * MARGIN) / crop_h)
            scale_size = (max(1, round(crop_w * scale)), max(1, round(crop_h * scale)))
            for crf in plan["crfs"]:
                rc, error = encode(output, frames, order, scale_size, canvas, crf, fps)
                if rc != 0:
                    if output.exists():
                        output.unlink()
                    return {
                        **base,
                        "status": "failed",
                        "error": f"ffmpeg rc={rc}: {error.decode('utf-8', 'replace')[:300]}",
                        "attempts": attempts,
                    }
                size = output.stat().st_size
                ok, reason, _ = validate(output, job["max_size"])
                attempts.append(
                    {
                        "canvas": list(canvas),
                        "fps": fps,
                        "crf": crf,
                        "size": size,
                        "valid": ok,
                        "reason": reason,
                    }
                )
                if ok:
                    return {
                        **base,
                        "status": "ok",
                        "path": str(output.relative_to(ROOT)) if output.is_relative_to(ROOT) else str(output),
                        "canvas": list(canvas),
                        "content": list(scale_size),
                        "crf": crf,
                        "size": size,
                        "duration_ms": round(out_frames * 1000 / fps),
                        "frames": out_frames,
                        "fps": fps,
                        "trimmed_start": trimmed_start,
                        "trimmed_end": trimmed_end,
                        "source_frames": len(frames),
                        "source_duration_ms": total_ms,
                        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                        "attempts": attempts,
                    }
                if size > job["max_size"] * 2 and plan_index + 1 < len(plans):
                    break
        if output.exists():
            output.unlink()
        return {
            **base,
            "status": "failed",
            "error": "could not reach <= max size",
            "attempts": attempts,
        }
    except Exception as exc:  # noqa: BLE001 - keep the batch going
        if output.exists():
            output.unlink()
        return {**base, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def make_pack_icon(icon_path: Path | None, animation: Path | None, out: Path) -> bool:
    image = None
    if icon_path and icon_path.exists():
        image = Image.open(icon_path).convert("RGBA")
    elif animation and animation.exists():
        source = Image.open(animation)
        source.seek(0)
        image = source.convert("RGBA")
    if image is None:
        return False
    box = image.getchannel("A").getbbox()
    if box:
        image = image.crop(box)
    scale = min((512 - 2 * MARGIN) / image.width, (512 - 2 * MARGIN) / image.height)
    image = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((512 - image.width) // 2, (512 - image.height) // 2))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return True


def make_contact_sheet(items: list[dict], out: Path, cols: int = 24) -> bool:
    items = [item for item in items if item.get("icon_path") and Path(item["icon_path"]).exists()]
    if not items:
        return False
    opened = [(item, Image.open(item["icon_path"]).convert("RGBA")) for item in items]
    cell_w = max(image.width for _, image in opened) + 16
    cell_h = max(image.height for _, image in opened) + 28
    cell_w = max(cell_w, 120)
    rows = math.ceil(len(opened) / cols)
    sheet = Image.new("RGBA", (cols * cell_w, rows * cell_h), (245, 245, 245, 255))
    draw = ImageDraw.Draw(sheet)
    for i, (item, image) in enumerate(opened):
        x = (i % cols) * cell_w + (cell_w - image.width) // 2
        y = (i // cols) * cell_h + 4
        sheet.alpha_composite(image, (x, y))
        label = item.get("human_name") or item.get("emote") or ""
        label = (label[:24] + "…") if len(label) > 25 else label
        draw.text(((i % cols) * cell_w + 6, y + image.height + 6), label, fill=(20, 20, 20, 255))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--animations", type=Path, default=DEFAULT_ANIMATIONS)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--icons", type=Path, default=DEFAULT_ICONS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--max-per-set",
        type=int,
        default=120,
        help="maximum stickers per set; the last set takes the remainder (Telegram max 120)",
    )
    parser.add_argument("--max-size-kb", type=int, default=250, help="target max size (Telegram allows 256)")
    parser.add_argument("--workers", type=int, default=0, help="parallel conversions (0 = 6)")
    parser.add_argument("--limit", type=int, default=0, help="only convert the first N emotes (smoke test)")
    parser.add_argument("--crf-start", type=int, default=28)
    parser.add_argument("--overwrite", action="store_true", help="re-encode existing files")
    args = parser.parse_args()

    animations = args.animations.resolve()
    out = args.out.resolve()
    icons = args.icons.resolve()
    index_path = args.index.resolve()
    if not animations.exists() or not index_path.exists():
        parser.error("animations directory or index.json not found")

    index = json.loads(index_path.read_text(encoding="utf-8"))
    entries = [
        entry
        for entry in index.get("emotes", [])
        if entry.get("animation") and (animations / Path(entry["animation"]).name).exists()
    ]
    if args.limit:
        entries = entries[: args.limit]
    if not entries:
        parser.error("no emote animations found")

    max_per_set = max(1, args.max_per_set)
    sets: list[list[dict]] = [
        entries[i : i + max_per_set] for i in range(0, len(entries), max_per_set)
    ]
    crfs = [crf for crf in DEFAULT_CRFS if crf >= args.crf_start]
    max_size = args.max_size_kb * 1024

    jobs = []
    skipped = []
    for set_index, chunk in enumerate(sets, start=1):
        set_dir = out / f"set-{set_index:02d}"
        for entry in chunk:
            source = animations / Path(entry["animation"]).name
            target = set_dir / (source.stem + ".webm")
            icon = icons / Path(entry["icon"]).name if entry.get("icon") else None
            job = {
                "source": str(source),
                "output": str(target),
                "set": set_index,
                "emote": entry.get("name"),
                "human_name": entry.get("human_readable_name"),
                "family": entry.get("family"),
                "emoji": emoji_for(entry.get("family")),
                "max_size": max_size,
                "crfs": crfs,
                "icon": str(icon) if icon else None,
            }
            if target.exists() and not args.overwrite:
                ok, reason, _ = validate(target, max_size)
                if ok:
                    skipped.append(job)
                    continue
            jobs.append(job)

    print(f"prepared {len(entries)} emotes in {len(sets)} sets; {len(jobs)} to convert, {len(skipped)} already valid")
    workers = args.workers or min(6, os.cpu_count() or 1)
    started = time.time()
    results = []
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(convert_one, job): job for job in jobs}
        for future in as_completed(futures):
            done += 1
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                job = futures[future]
                result = {
                    "file": Path(job["output"]).name,
                    "emote": job["emote"],
                    "human_name": job.get("human_name"),
                    "family": job.get("family"),
                    "emoji": job["emoji"],
                    "set": job["set"],
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            results.append(result)
            if result["status"] != "ok":
                print(f"FAIL {result.get('emote')}: {result.get('error')}", flush=True)
            if done % 10 == 0 or done == len(jobs):
                print(f"[{done}/{len(jobs)}] converted in {time.time() - started:.0f}s", flush=True)

    # Build per-set outputs and the global manifest.
    by_set: dict[int, list[dict]] = {i: [] for i in range(1, len(sets) + 1)}
    for result in results:
        if result["status"] == "ok":
            by_set[result["set"]].append(result)
    for job in skipped:
        # Existing valid outputs still need metadata; re-read minimal ffprobe info.
        target = Path(job["output"])
        ok, _, info = validate(target, max_size)
        if not ok:
            continue
        stream = next(s for s in info["streams"] if s.get("codec_type") == "video")
        rate = stream.get("r_frame_rate", "30/1")
        try:
            num, den = rate.split("/")
            stream_fps = round(float(num) / float(den or 1))
        except (ValueError, ZeroDivisionError):
            stream_fps = FPS
        by_set[job["set"]].append(
            {
                "file": target.name,
                "emote": job["emote"],
                "human_name": job.get("human_name"),
                "family": job.get("family"),
                "emoji": job["emoji"],
                "source": str(Path(job["source"]).relative_to(ROOT)),
                "set": job["set"],
                "status": "ok",
                "path": str(target.relative_to(ROOT)),
                "canvas": [int(stream["width"]), int(stream["height"])],
                "crf": None,
                "size": target.stat().st_size,
                "duration_ms": round(float(info["format"]["duration"]) * 1000),
                "fps": stream_fps,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )

    manifest_sets = []
    all_ok = []
    for set_index, chunk in enumerate(sets, start=1):
        set_dir = out / f"set-{set_index:02d}"
        set_dir.mkdir(parents=True, exist_ok=True)
        stickers = sorted(by_set[set_index], key=lambda item: item["emote"] or "")
        # Keep the original index order for upload.txt.
        order = {entry.get("name"): i for i, entry in enumerate(chunk)}
        stickers.sort(key=lambda item: order.get(item.get("emote"), 10**9))
        upload_lines = [
            f"{sticker['file']}\t{sticker['emoji']}\t"
            f"{(sticker.get('human_name') or '').replace(chr(9), ' ').replace(chr(10), ' ')}"
            for sticker in stickers
        ]
        (set_dir / "upload.txt").write_text("\n".join(upload_lines) + ("\n" if upload_lines else ""), encoding="utf-8")
        icon_ok = False
        if chunk:
            first = chunk[0]
            icon_path = icons / Path(first["icon"]).name if first.get("icon") else None
            animation_path = animations / Path(first["animation"]).name
            icon_ok = make_pack_icon(icon_path, animation_path, set_dir / "icon.png")
        manifest_sets.append(
            {
                "index": set_index,
                "name": f"ClashRoyaleEmotes{set_index}",
                "title": f"Clash Royale Emotes {set_index}",
                "directory": f"set-{set_index:02d}",
                "icon": f"set-{set_index:02d}/icon.png" if icon_ok else None,
                "upload_list": f"set-{set_index:02d}/upload.txt",
                "count": len(stickers),
                "stickers": stickers,
            }
        )
        all_ok.extend(stickers)

    failed = [result for result in results if result["status"] != "ok"]
    manifest = {
        "source": str(animations.relative_to(ROOT)),
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "format": "telegram-video-sticker",
        "max_size_bytes": max_size,
        "fps": FPS,
        "max_duration_ms": MAX_DURATION_MS,
        "counts": {
            "requested": len(entries),
            "converted": len(all_ok),
            "failed": len(failed),
            "sets": len(manifest_sets),
        },
        "sets": manifest_sets,
        "failed": failed,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (out / "failed.json").write_text(json.dumps(failed, indent=1), encoding="utf-8")

    # Contact sheet uses the extracted icons; the icon path is recovered from the index.
    icon_by_name = {entry.get("name"): icons / Path(entry["icon"]).name for entry in entries if entry.get("icon")}
    for sticker in all_ok:
        sticker["icon_path"] = str(icon_by_name.get(sticker.get("emote")) or "")
    make_contact_sheet(all_ok, out / "contact-sheet.png")

    (out / "README.md").write_text(
        f"""# Telegram video stickers

Generated from `{animations}` by `tools/make_telegram_video_stickers.py`.

- {len(all_ok)} VP9 WEBM stickers in {len(manifest_sets)} sets
- 512x512, 512x384/384x512, or 512x256/256x512 transparent canvas
- <= 3 s, 30 fps (24 fps for compact fallbacks), no audio, <= {args.max_size_kb} KB each
  (Telegram's hard limit is 256 KB)
- `manifest.json` – sticker metadata, emoji tags, source mapping
- `failed.json` – conversions that could not meet the limits
- `set-XX/upload.txt` – upload order and emoji tag per sticker
- `set-XX/icon.png` – 512x512 pack icon
- `contact-sheet.png` – preview grid

## Upload

For each set:

```text
/newvideo
<set name>
<send the .webm files in upload.txt order>
<send the emoji shown next to each file>
/setpackicon
```

Telegram permits up to 120 stickers per set; emoji sets are handled separately.
`/addsticker` extends an existing set, `/cancel` aborts the current operation.
The `icon.png` files are previews/pack icons for the bot flow; when creating sets
through the Bot API, the first sticker is used as the thumbnail unless a video
thumbnail is supplied.

## Bulk import (optional)

`tools/telegram_import_video_stickers.py` creates all sets through the Bot API.
It needs a bot token in `TELEGRAM_BOT_TOKEN` (or `--token-file`) and the numeric
Telegram user ID (or `--username` after sending `/start` to the bot). Run it
without `--execute` first for a dry run.

These are original game assets. Keep this directory local/private.
""",
        encoding="utf-8",
    )

    print(
        f"done: {len(all_ok)} stickers, {len(failed)} failed, {len(manifest_sets)} sets; "
        f"output {out}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
