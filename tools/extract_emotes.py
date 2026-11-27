#!/usr/bin/env python3
"""Extract Clash Royale emote art (icons, animations, audio) from local assets.

Reads the unencrypted/decoded asset tree that already ships with this workspace
(``.resource/cluster/decoded-16.635.1-pristine/assets``), renders every emote
export with the SC2 decoder in ``.resource/clash-royale-research/tools/art``
(and ``tools/sc1_render.py`` for legacy SC1 libraries) and writes
browsing-friendly PNG/WebP/OGG files plus an index.

Usage:
    python tools/extract_emotes.py
    python tools/extract_emotes.py --assets PATH --out PATH --scale 2 --workers 8
    python tools/extract_emotes.py --limit 8            # quick smoke test

Outputs (default ``artifacts/emotes``; ignored by git):
    icons/<Name>_<title>.png          static emote icon, straight alpha
    animations/<Name>_<title>.webp    full animation, transparent WebP
    audio/<Name>_<title>.ogg          matching emote sound (when present)
    index.json                        one record per emote + unsupported list
    emotes.csv                        decoded emotes.csv used for naming
    contact_sheet.png                 icon overview grid
    README.md                         what was extracted and known gaps

Legacy SC1 (``.sc`` v4) libraries are rendered by ``tools/sc1_render.py``.  Any
library that still cannot be parsed is listed under ``unsupported`` in
``index.json``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import struct
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_ASSETS = ROOT / ".resource" / "cluster" / "decoded-16.635.1-pristine" / "assets"
DEFAULT_OUT = ROOT / "artifacts" / "emotes"
ART_TOOLS = ROOT / ".resource" / "clash-royale-research" / "tools" / "art"
LZMA_TOOL = ROOT / ".resource" / "clash-royale-research" / "scripts" / "sc_lzma.py"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ART_TOOLS))
sys.path.insert(0, str(LZMA_TOOL.parent))


class Sc2Scene:
    """Adapter for the modern SC2 renderer in the research checkout."""

    icon_from_animation = False

    def __init__(self, path: Path):
        import render

        self._scene = render.Scene(path)
        self.exports = self._scene.sc.exports
        self.warnings = self._scene.warnings

    def frame_count(self, oid: int) -> int:
        return self._scene.frame_count(oid)

    def fps(self, oid: int) -> int:
        mc = self._scene.sc.movieclips.get(oid)
        return mc.framerate if mc and mc.framerate else 60

    def render_frames(self, oid: int, frames: list[int], scale: float):
        return self._scene.render_frames(oid, frames, scale)


class Sc1Scene:
    """Adapter for the legacy SC1 renderer in tools/sc1_render.py."""

    # SC1 icon exports in these files are generic placeholder shapes, so the
    # icon is taken from the most complete frame of the animation instead.
    icon_from_animation = True

    def __init__(self, path: Path):
        import sc1_render

        self._scene = sc1_render.Scene(path)
        self.exports = self._scene.exports
        self.warnings = self._scene.warnings

    def frame_count(self, oid: int) -> int:
        return self._scene.frame_count(oid)

    def fps(self, oid: int) -> int:
        return self._scene.fps(oid)

    def render_frames(self, oid: int, frames: list[int], scale: float):
        return self._scene.render_frames(oid, frames, scale)


def slug(text: str, limit: int = 80) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")
    return (text[:limit].rstrip("_")) or "emote"


def read_emotes_csv(path: Path) -> list[dict]:
    """Return data rows; transparently decode Supercell's 9-byte LZMA wrapper."""
    raw = path.read_bytes()
    if raw[:2] == b"]\x00":
        import sc_lzma

        raw = sc_lzma.decode(raw)
    rows = list(csv.DictReader(raw.decode("utf-8", "replace").splitlines()))
    return [r for r in rows if r.get("Name") and r["Name"] != "string"]


def as_bool(value: str) -> bool | None:
    if value == "True":
        return True
    if value == "False":
        return False
    return None


def display_name(row: dict, export_index: int) -> str:
    title = row.get("HumanReadableName") or ""
    if title:
        return f"{row['Name']}_{slug(title)}"
    return f"{row['Name']}_{slug(row.get('Family') or '')}_{export_index}"


def export_number(name: str) -> int:
    m = re.search(r"(\d+)$", name)
    return int(m.group(1)) if m else 10**6


def extract_one(job: dict) -> dict:
    """Render all emote/icon exports for one .sc library. Runs in a worker."""
    sc_rel = job["sc_rel"]
    sc_path = Path(job["assets"]) / sc_rel
    out = Path(job["out"])
    rows: list[dict] = job["rows"]
    result = {"sc_file": sc_rel, "emotes": [], "error": None, "warnings": []}

    raw = sc_path.read_bytes()
    legacy = raw[:2] == b"SC" and struct.unpack(">I", raw[2:6])[0] <= 4
    try:
        scene = Sc1Scene(sc_path) if legacy else Sc2Scene(sc_path)
    except Exception as exc:  # noqa: BLE001 - keep the batch going
        prefix = "unsupported-sc1" if legacy else type(exc).__name__
        result["error"] = f"{prefix}: {exc}"
        return result

    exports = scene.exports
    emote_exports = sorted((n for n in exports if n.lower().startswith("emote")), key=export_number)
    icon_exports = sorted((n for n in exports if n.lower().startswith("icon")), key=export_number)

    # Rows sharing a library are in export order. Extra exports (newer than the
    # CSV) are still extracted and get a generated name.
    by_index: dict[int, dict] = {}
    for i, row in enumerate(rows):
        by_index.setdefault(i, row)

    for i, emote_export in enumerate(emote_exports):
        row = by_index.get(i)
        if row is not None:
            name = display_name(row, i + 1)
        else:
            name = f"{Path(sc_rel).stem}_{emote_export}"
            row = {}

        oid = exports[emote_export]
        frame_count = scene.frame_count(oid)
        fps = scene.fps(oid)
        try:
            imgs, meta = scene.render_frames(oid, list(range(frame_count)), job["scale"])
        except Exception as exc:  # noqa: BLE001
            result["warnings"].append(f"{emote_export}: render failed: {type(exc).__name__}: {exc}")
            continue
        if not imgs:
            result["warnings"].append(f"{emote_export}: nothing to draw")
            continue

        icons_rel = None
        icon_source = None
        icon_export = icon_exports[i] if i < len(icon_exports) else None
        if getattr(scene, "icon_from_animation", False):
            # SC1 icon exports are often placeholder shapes; use a tight crop
            # of the frame with the most opaque pixels instead.
            try:
                import numpy as np

                best = max(imgs, key=lambda im: int(np.asarray(im.getchannel("A"), dtype=bool).sum()))
                bbox = best.getchannel("A").getbbox()
                icon_img = best.crop(bbox) if bbox else best
                icon_path = out / "icons" / f"{name}.png"
                icon_path.parent.mkdir(parents=True, exist_ok=True)
                icon_img.save(icon_path)
                icons_rel = str(icon_path.relative_to(out))
                icon_source = "animation"
            except Exception as exc:  # noqa: BLE001
                result["warnings"].append(f"animation icon failed: {type(exc).__name__}: {exc}")
        elif icon_export:
            try:
                icon_imgs, icon_meta = scene.render_frames(exports[icon_export], [0], job["scale"])
                if icon_imgs:
                    icon_path = out / "icons" / f"{name}.png"
                    icon_path.parent.mkdir(parents=True, exist_ok=True)
                    icon_imgs[0].save(icon_path)
                    icons_rel = str(icon_path.relative_to(out))
                    icon_source = "export"
            except Exception as exc:  # noqa: BLE001
                result["warnings"].append(f"{icon_export}: icon render failed: {type(exc).__name__}: {exc}")

        anim_path = out / "animations" / f"{name}.webp"
        anim_path.parent.mkdir(parents=True, exist_ok=True)
        duration = max(8, round(1000 / max(fps, 1)))
        imgs[0].save(
            anim_path,
            save_all=True,
            append_images=imgs[1:],
            duration=duration,
            loop=0,
            quality=job["quality"],
            method=4,
            minimize_size=True,
        )

        audio_rel = None
        sfx = row.get("SfxFile") or ""
        if sfx:
            src = Path(job["assets"]) / sfx
            if src.exists():
                audio_path = out / "audio" / f"{name}.ogg"
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                if not audio_path.exists():
                    shutil.copyfile(src, audio_path)
                audio_rel = str(audio_path.relative_to(out))

        result["emotes"].append(
            {
                "name": row.get("Name") or name,
                "human_readable_name": row.get("HumanReadableName") or None,
                "family": row.get("Family") or None,
                "available": as_bool(row.get("Available", "")),
                "default_owned": as_bool(row.get("DefaultOwned", "")),
                "available_for_offer": as_bool(row.get("AvailableForOffer", "")),
                "exclusive": as_bool(row.get("Exclusive", "")),
                "use_crystal_background": as_bool(row.get("UseCrystalBackground", "")),
                "date_available": row.get("DateAvailable") or None,
                "sc_file": sc_rel,
                "export": emote_export,
                "icon_export": icon_export,
                "frames": len(imgs),
                "fps": fps,
                "width": meta["width"],
                "height": meta["height"],
                "anchor": meta["anchor"],
                "icon": icons_rel,
                "icon_source": icon_source,
                "animation": str(anim_path.relative_to(out)),
                "audio": audio_rel,
            }
        )

    # Icons whose emote row/export was not consumed (e.g. one extra icon).
    for i, icon_export in enumerate(icon_exports):
        if i < len(emote_exports):
            continue
        row = by_index.get(i)
        name = display_name(row, i + 1) if row else f"{Path(sc_rel).stem}_{icon_export}"
        try:
            icon_imgs, _ = scene.render_frames(exports[icon_export], [0], job["scale"])
            if icon_imgs:
                icon_path = out / "icons" / f"{name}.png"
                icon_path.parent.mkdir(parents=True, exist_ok=True)
                icon_imgs[0].save(icon_path)
                result["emotes"].append(
                    {
                        "name": (row or {}).get("Name") or name,
                        "human_readable_name": (row or {}).get("HumanReadableName") or None,
                        "family": (row or {}).get("Family") or None,
                        "sc_file": sc_rel,
                        "export": None,
                        "icon_export": icon_export,
                        "icon": str(icon_path.relative_to(out)),
                        "animation": None,
                        "audio": None,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            result["warnings"].append(f"{icon_export}: icon render failed: {type(exc).__name__}: {exc}")

    if scene.warnings:
        result["warnings"].extend(sorted(scene.warnings))
    return result


def make_contact_sheet(out: Path, entries: list[dict], cols: int = 24) -> str | None:
    from PIL import Image, ImageDraw

    items = [(e, out / e["icon"]) for e in entries if e.get("icon") and (out / e["icon"]).exists()]
    if not items:
        return None
    sizes = [Image.open(p).size for _, p in items]
    cell_w = max(w for w, _ in sizes) + 16
    cell_h = max(h for _, h in sizes) + 28
    cell_w = max(cell_w, 120)
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * cell_w, rows * cell_h), (245, 245, 245, 255))
    draw = ImageDraw.Draw(sheet)
    for idx, (entry, path) in enumerate(items):
        im = Image.open(path).convert("RGBA")
        x = (idx % cols) * cell_w + (cell_w - im.width) // 2
        y = (idx // cols) * cell_h + 4
        sheet.alpha_composite(im, (x, y))
        label = entry.get("human_readable_name") or entry.get("name") or ""
        label = (label[:24] + "…") if len(label) > 25 else label
        draw.text(((idx % cols) * cell_w + 6, y + im.height + 6), label, fill=(20, 20, 20, 255))
    dest = out / "contact_sheet.png"
    sheet.save(dest)
    return str(dest.relative_to(out))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assets", type=Path, default=DEFAULT_ASSETS, help="asset root containing sc/, csv_logic/, sfx/")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--scale", type=float, default=1.0, help="render scale (1 = native SC pixels)")
    ap.add_argument("--quality", type=int, default=85, help="animated WebP quality (0-100)")
    ap.add_argument("--workers", type=int, default=0, help="parallel processes (0 = CPU count)")
    ap.add_argument("--limit", type=int, default=0, help="only process the first N libraries (smoke test)")
    args = ap.parse_args()

    assets = args.assets.resolve()
    out = args.out.resolve()
    if not (assets / "csv_logic" / "emotes.csv").exists():
        ap.error(f"emotes.csv not found under {assets}")
    out.mkdir(parents=True, exist_ok=True)
    for sub in ("icons", "animations", "audio"):
        (out / sub).mkdir(exist_ok=True)

    rows = read_emotes_csv(assets / "csv_logic" / "emotes.csv")
    with (out / "emotes.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    by_sc: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("ScFile"):
            by_sc.setdefault(row["ScFile"], []).append(row)

    sc_files = sorted((assets / "sc").glob("emotes_*.sc"))
    if args.limit:
        sc_files = sc_files[: args.limit]

    jobs = []
    seen = set()
    for sc_path in sc_files:
        rel = sc_path.relative_to(assets).as_posix()
        seen.add(rel)
        jobs.append(
            {
                "assets": str(assets),
                "out": str(out),
                "sc_rel": rel,
                "rows": by_sc.get(rel, []),
                "scale": args.scale,
                "quality": args.quality,
            }
        )
    # CSV entries whose .sc is missing would otherwise silently vanish.
    missing_files = sorted(set(by_sc) - seen)

    workers = args.workers or min(12, os.cpu_count() or 1)
    started = time.time()
    entries: list[dict] = []
    unsupported: list[dict] = []
    failures: list[dict] = []
    warn_count = 0
    done = 0
    total = len(jobs)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(extract_one, job): job for job in jobs}
        for fut in as_completed(futures):
            job = futures[fut]
            done += 1
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                failures.append({"sc_file": job["sc_rel"], "error": f"{type(exc).__name__}: {exc}"})
                continue
            if res["error"]:
                if res["error"].startswith(("unsupported-format", "unsupported-sc1")):
                    unsupported.append({"sc_file": res["sc_file"], "reason": res["error"],
                                        "emotes": [r["Name"] for r in job["rows"]]})
                else:
                    failures.append({"sc_file": res["sc_file"], "error": res["error"]})
                continue
            entries.extend(res["emotes"])
            warn_count += len(res["warnings"])
            if done % 25 == 0 or done == total:
                print(f"[{done}/{total}] libs, {len(entries)} emotes, {time.time() - started:.0f}s", flush=True)

    entries.sort(key=lambda e: (e.get("family") or "", e.get("name") or ""))
    sheet = make_contact_sheet(out, entries)

    no_art = [
        {"name": r["Name"], "human_readable_name": r.get("HumanReadableName") or None,
         "family": r.get("Family") or None, "reason": "no ScFile in emotes.csv"}
        for r in rows if not r.get("ScFile")
    ]
    extracted_names = {e["name"] for e in entries}
    unsupported_names = {n for u in unsupported for n in u["emotes"]}
    skipped_rows = [
        {"name": r["Name"], "human_readable_name": r.get("HumanReadableName") or None,
         "family": r.get("Family") or None, "sc_file": r["ScFile"],
         "reason": "library has fewer emote exports than CSV rows (unreleased/shared art)"}
        for r in rows
        if r.get("ScFile") and r["Name"] not in extracted_names
        and r["Name"] not in unsupported_names
    ]
    index = {
        "source": str(assets),
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "render_scale": args.scale,
        "webp_quality": args.quality,
        "counts": {
            "csv_rows": len(rows),
            "extracted": len(entries),
            "icons": sum(1 for e in entries if e.get("icon")),
            "animations": sum(1 for e in entries if e.get("animation")),
            "audio": sum(1 for e in entries if e.get("audio")),
            "unsupported_libraries": len(unsupported),
            "missing_libraries": len(missing_files),
            "no_art_rows": len(no_art),
            "skipped_rows": len(skipped_rows),
            "failures": len(failures),
        },
        "contact_sheet": sheet,
        "emotes": entries,
        "unsupported": unsupported,
        "missing_libraries": missing_files,
        "no_art": no_art,
        "skipped_rows": skipped_rows,
        "failures": failures,
    }
    (out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")

    (out / "README.md").write_text(
        f"""# Clash Royale emote extraction

Generated by `tools/extract_emotes.py` from `{assets}`.

- `icons/` – static emote icons (PNG, straight alpha)
- `animations/` – full emote animations (animated WebP, transparent)
- `audio/` – matching emote sounds (OGG), when the CSV names one
- `index.json` – metadata, export mapping, counts and gaps
- `contact_sheet.png` – overview of every extracted icon
- `emotes.csv` – decoded naming metadata used for the extraction

## Reproduce

```sh
python -m venv .venv-art
.venv-art/bin/pip install -r .resource/clash-royale-research/tools/art/requirements.txt
.venv-art/bin/python tools/extract_emotes.py
```

Counts: {len(entries)} emote exports extracted
({index['counts']['icons']} icons, {index['counts']['animations']} animations,
{index['counts']['audio']} audio clips). Render scale {args.scale}, WebP quality {args.quality}.

Known gaps: {len(unsupported)} libraries could not be parsed (legacy SC1 files
are rendered by `tools/sc1_render.py`), {len(no_art)} unreleased
`EmoteChronos*` rows have no `ScFile`, {len(skipped_rows)} rows share or lack a
distinct export, and {len(failures)} libraries failed. See `index.json` for
the exact lists.

These are original game assets. Keep this directory local/private and do not
commit or redistribute it.
""",
        encoding="utf-8",
    )

    print(
        f"done: {len(entries)} emotes from {total} libraries | icons="
        f"{index['counts']['icons']} animations={index['counts']['animations']} audio={index['counts']['audio']} | "
        f"unsupported={len(unsupported)} no_art={len(no_art)} failures={len(failures)} warnings={warn_count}"
    )
    print(f"output: {out}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
