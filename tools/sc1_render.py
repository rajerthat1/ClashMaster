"""Minimal renderer for legacy Supercell SC1 (v4) emote libraries.

The modern SC2 decoder in ``.resource/clash-royale-research/tools/art`` cannot
read the six ``.sc`` v4 files that ship with the 16.635.1 asset pack.  This
module parses just enough of the tag-based SC1 format to render those emotes:

* container:  ``SC`` + big-endian version 4, LZMA-wrapped payload
* header:     6 x u16 counts, 5 bytes, export count/ids/names
* tags:       0x01/0x10/... texture, 0x12/0x18 shape, 0x08 matrix,
              0x0c movie clip, 0x09 colour transform
* geometry:   shape regions reference a texture and store int32 twip
              coordinates plus u16 texture coordinates
* transforms: matrix values are 1/1024 scale and twip translation;
              shape coordinates are 1/20 px (twips)

Only the features used by the emote files are implemented.  Masks, text and
non-identity colour transforms are ignored with a warning.
"""

from __future__ import annotations

import lzma
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

SUPERCELL_LZMA = bytes.fromhex("5d00000400")
TEXTURE_TAGS = {1, 16, 19, 24, 27, 28, 29, 34}
SHAPE_TAGS = {2, 0x12}
MOVIE_CLIP_TAGS = {3, 10, 12, 14, 35, 49}
MATRIX_TAG = 0x08
COLOR_TAG = 0x09
REGION_TAG = 0x16


def _decompress(raw: bytes) -> bytes:
    idx = raw.find(SUPERCELL_LZMA)
    if idx >= 0:
        expected = struct.unpack_from("<I", raw, idx + 5)[0]
        dec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
        out = dec.decompress(raw[idx : idx + 9] + bytes(4) + raw[idx + 9 :])
        if len(out) != expected:
            raise ValueError(f"SC1 LZMA length mismatch: {len(out)} != {expected}")
        return out
    zidx = raw.find(b"\x28\xb5\x2f\xfd")
    if zidx >= 0:
        import zstandard

        return zstandard.ZstdDecompressor().decompress(raw[zidx:])
    raise ValueError("SC1 payload has no LZMA or zstd frame")


def _texture_image(pixel_code: int, width: int, height: int, data: bytes, interlaced: bool) -> Image.Image:
    count = width * height
    if pixel_code in (0, 1):
        arr = np.frombuffer(data[: count * 4], dtype=np.uint8).reshape(-1, 4)
    elif pixel_code in (2, 9):
        v = np.frombuffer(data[: count * 2], dtype="<u2")
        arr = np.stack(
            [((v >> 12) & 0xF) * 17, ((v >> 8) & 0xF) * 17, ((v >> 4) & 0xF) * 17, (v & 0xF) * 17],
            axis=1,
        ).astype(np.uint8)
    elif pixel_code == 3:
        v = np.frombuffer(data[: count * 2], dtype="<u2")
        arr = np.stack(
            [((v >> 11) & 0x1F) * 255 // 31, ((v >> 6) & 0x1F) * 255 // 31,
             ((v >> 1) & 0x1F) * 255 // 31, (v & 1) * 255],
            axis=1,
        ).astype(np.uint8)
    elif pixel_code == 4:
        v = np.frombuffer(data[: count * 2], dtype="<u2")
        arr = np.stack(
            [((v >> 11) & 0x1F) * 255 // 31, ((v >> 5) & 0x3F) * 255 // 63,
             (v & 0x1F) * 255 // 31, np.full(count, 255)],
            axis=1,
        ).astype(np.uint8)
    elif pixel_code == 6:
        v = np.frombuffer(data[: count * 2], dtype="<u2")
        lum = ((v >> 8) & 0xFF).astype(np.uint8)
        arr = np.stack([lum, lum, lum, (v & 0xFF).astype(np.uint8)], axis=1)
    elif pixel_code == 10:
        lum = np.frombuffer(data[:count], dtype=np.uint8)
        arr = np.stack([lum, lum, lum, np.full(count, 255, np.uint8)], axis=1)
    else:
        raise ValueError(f"unsupported SC1 pixel code {pixel_code}")

    if interlaced:
        # SC1 TEXTURE_5/6 store 32x32 blocks in scan order.
        src = arr.reshape(-1, 4)
        out = np.empty((height, width, 4), dtype=np.uint8)
        i = 0
        for by in range(0, height, 32):
            for bx in range(0, width, 32):
                bh = min(32, height - by)
                bw = min(32, width - bx)
                out[by : by + bh, bx : bx + bw] = src[i : i + bh * bw].reshape(bh, bw, 4)
                i += bh * bw
        arr = out.reshape(-1, 4)
    return Image.frombytes("RGBA", (width, height), arr.tobytes())


@dataclass
class ShapeRegion:
    texture: int
    points: list[tuple[int, int]]
    uvs: list[tuple[float, float]]


IDENTITY_COLOR = (0, 0, 0, 255, 255, 255, 255)


@dataclass
class MovieClip:
    fps: int
    frames: int
    elements: list[tuple[int, int, int]]
    children: list[int] = field(default_factory=list)
    frame_counts: list[int] = field(default_factory=list)
    frame_offsets: list[int] = field(default_factory=list)

    def frame_slice(self, frame: int) -> tuple[int, int]:
        f = frame % max(self.frames, 1)
        if self.frame_counts and f < len(self.frame_counts):
            start = self.frame_offsets[f]
            return start, start + self.frame_counts[f]
        per = len(self.elements) // max(self.frames, 1)
        return f * per, f * per + per


class Scene:
    """Small adapter with the same render API used by tools/extract_emotes.py."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.warnings: set[str] = set()
        payload = _decompress(self.path.read_bytes())
        self.exports: dict[str, int] = {}
        self.shapes: dict[int, list[ShapeRegion]] = {}
        self.clips: dict[int, MovieClip] = {}
        self.matrices: list[tuple[int, int, int, int, int, int]] = []
        self.colors: list[tuple[int, int, int, int, int, int, int]] = []
        self.textures: list[Image.Image] = []
        self._parse(payload)
        if not self.exports:
            raise ValueError(f"{self.path}: no SC1 exports found")

    def _parse(self, b: bytes) -> None:
        if len(b) < 17:
            raise ValueError("truncated SC1 payload")
        pos = 12 + 5
        count = struct.unpack_from("<H", b, pos)[0]
        pos += 2
        ids = [struct.unpack_from("<H", b, pos + 2 * i)[0] for i in range(count)]
        pos += 2 * count
        names = []
        for _ in range(count):
            length = b[pos]
            pos += 1
            names.append(b[pos : pos + length].decode("latin1"))
            pos += length
        self.exports = dict(zip(names, ids))

        while pos + 5 <= len(b):
            tag = b[pos]
            size = struct.unpack_from("<I", b, pos + 1)[0]
            if tag == 0:
                break
            q = pos + 5
            end = q + size
            if tag in TEXTURE_TAGS:
                pixel_code = b[q]
                width = struct.unpack_from("<H", b, q + 1)[0]
                height = struct.unpack_from("<H", b, q + 3)[0]
                self.textures.append(
                    _texture_image(pixel_code, width, height, b[q + 5 : end], tag in (27, 28, 29))
                )
            elif tag in SHAPE_TAGS:
                shape_id = struct.unpack_from("<H", b, q)[0]
                region_count = struct.unpack_from("<H", b, q + 2)[0]
                rpos = q + (6 if tag == 0x12 else 4)
                regions = []
                for _ in range(region_count):
                    if rpos + 5 > end or b[rpos] != REGION_TAG:
                        break
                    rsize = struct.unpack_from("<I", b, rpos + 1)[0]
                    rp = rpos + 5
                    texture = b[rp]
                    points_count = b[rp + 1]
                    p = rp + 2
                    pts = [struct.unpack_from("<ii", b, p + 8 * i) for i in range(points_count)]
                    p += 8 * points_count
                    uvs = [
                        (struct.unpack_from("<H", b, p + 4 * i)[0] / 65535.0,
                         struct.unpack_from("<H", b, p + 4 * i + 2)[0] / 65535.0)
                        for i in range(points_count)
                    ]
                    regions.append(ShapeRegion(texture, pts, uvs))
                    rpos = rp + rsize
                self.shapes[shape_id] = regions
            elif tag == MATRIX_TAG:
                self.matrices.append(struct.unpack_from("<6i", b, q))
            elif tag in MOVIE_CLIP_TAGS:
                clip_id = struct.unpack_from("<H", b, q)[0]
                fps = b[q + 2]
                frames = struct.unpack_from("<H", b, q + 3)[0]
                p = q + 5
                if tag == 49:
                    property_count = b[p]
                    p += 1
                    for _ in range(property_count):
                        property_type = b[p]
                        p += 1
                        if property_type == 0:
                            p += 1  # boolean
                        else:
                            self.warnings.add(f"SC1 custom movieclip property {property_type} ignored")
                            break
                if tag in (3, 14):
                    element_count = 0
                    elements = []
                else:
                    element_count = struct.unpack_from("<i", b, p)[0]
                    p += 4
                    elements = [struct.unpack_from("<HHH", b, p + 6 * i) for i in range(element_count)]
                    p += 6 * element_count
                child_count = struct.unpack_from("<H", b, p)[0]
                p += 2
                children = [struct.unpack_from("<h", b, p + 2 * i)[0] for i in range(child_count)]
                p += 2 * child_count
                if tag in (12, 35, 49):
                    p += child_count  # blend states
                for _ in range(child_count):  # bind names
                    length = b[p]
                    p += 1
                    if length != 255:
                        p += length
                frame_counts: list[int] = []
                while p + 5 <= end:
                    frame_tag = b[p]
                    frame_size = struct.unpack_from("<I", b, p + 1)[0]
                    if frame_tag == 0:
                        break
                    if frame_tag == 11 and frame_size >= 2:
                        frame_counts.append(struct.unpack_from("<h", b, p + 5)[0])
                    p += 5 + frame_size
                offsets: list[int] = []
                if sum(frame_counts) == len(elements):
                    acc = 0
                    for count in frame_counts:
                        offsets.append(acc)
                        acc += count
                else:
                    frame_counts = []
                self.clips[clip_id] = MovieClip(
                    fps or 60, frames, elements, children, frame_counts, offsets
                )
            elif tag == COLOR_TAG:
                self.colors.append(struct.unpack_from("<7B", b, q))
            # Unknown tags are size-delimited and skipped safely.
            pos = end

    # -- API used by extract_emotes.py --------------------------------------
    def export_id(self, name: str) -> int:
        return self.exports[name]

    def frame_count(self, oid: int) -> int:
        clip = self.clips.get(oid)
        return clip.frames if clip else 1

    def fps(self, oid: int) -> int:
        clip = self.clips.get(oid)
        return clip.fps if clip else 60

    def _collect(
        self,
        oid: int,
        frame: int,
        matrix: tuple[float, float, float, float, float, float] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
        color: tuple[int, int, int, int, int, int, int] = IDENTITY_COLOR,
        out: list | None = None,
        depth: int = 0,
    ) -> list:
        out = [] if out is None else out
        if depth > 16:
            return out
        clip = self.clips.get(oid)
        if clip is not None:
            start, stop = clip.frame_slice(frame)
            if stop <= start:
                return out
            for sid, mid, cid in clip.elements[start:stop]:
                child_color = (
                    _compose_color(color, self.colors[cid])
                    if cid != 0xFFFF and cid < len(self.colors)
                    else color
                )
                child_matrix = self._matrix(mid)
                composed = _mul(matrix, child_matrix)
                self._collect(sid, frame, composed, child_color, out, depth + 1)
            return out
        regions = self.shapes.get(oid)
        if regions is not None:
            for region in regions:
                if region.texture >= len(self.textures):
                    continue
                points = [
                    (
                        (matrix[0] * x + matrix[2] * y) / 20.0 + matrix[4] / 20.0,
                        (matrix[1] * x + matrix[3] * y) / 20.0 + matrix[5] / 20.0,
                    )
                    for x, y in region.points
                ]
                out.append((region.texture, points, region.uvs, color))
            return out
        self.warnings.add(f"unknown SC1 object id {oid}")
        return out

    def _matrix(self, index: int) -> tuple[float, float, float, float, float, float]:
        if index == 0xFFFF or index >= len(self.matrices):
            return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        a, b, c, d, tx, ty = self.matrices[index]
        return (a / 1024.0, b / 1024.0, c / 1024.0, d / 1024.0, float(tx), float(ty))

    def render_frames(self, oid: int, frames: list[int], scale: float = 1.0, pad: int = 2):
        per_frame = [self._collect(oid, f) for f in frames]
        points = [p for polys in per_frame for _, pts, _, _ in polys for p in pts]
        if not points:
            return [], {}
        x0 = min(p[0] for p in points) - pad / scale
        y0 = min(p[1] for p in points) - pad / scale
        x1 = max(p[0] for p in points) + pad / scale
        y1 = max(p[1] for p in points) + pad / scale
        width = int(math.ceil((x1 - x0) * scale))
        height = int(math.ceil((y1 - y0) * scale))
        images = [
            _to_image(_rasterize(polys, self.textures, (x0, y0), (width, height), scale))
            for polys in per_frame
        ]
        meta = {
            "width": width,
            "height": height,
            "scale": scale,
            "anchor": [round(float(-x0 * scale), 2), round(float(-y0 * scale), 2)],
        }
        return images, meta


def _compose_color(
    parent: tuple[int, int, int, int, int, int, int],
    child: tuple[int, int, int, int, int, int, int],
) -> tuple[int, int, int, int, int, int, int]:
    """Apply child then parent: parent_mul * (child_mul * x + child_add) + parent_add."""
    pa, pga, pba, pa_mul, pr_mul, pg_mul, pb_mul = parent
    ca, cga, cba, ca_mul, cr_mul, cg_mul, cb_mul = child
    return (
        min(255, pa + pr_mul * ca // 255),
        min(255, pga + pg_mul * cga // 255),
        min(255, pba + pb_mul * cba // 255),
        pa_mul * ca_mul // 255,
        pr_mul * cr_mul // 255,
        pg_mul * cg_mul // 255,
        pb_mul * cb_mul // 255,
    )


def _mul(p: tuple[float, ...], c: tuple[float, ...]) -> tuple[float, float, float, float, float, float]:
    """Apply child then parent (same convention as sc2.Matrix.mul)."""
    pa, pb, pc, pd, ptx, pty = p
    ca, cb, cc, cd, ctx, cty = c
    return (
        pa * ca + pc * cb,
        pb * ca + pd * cb,
        pa * cc + pc * cd,
        pb * cc + pd * cd,
        pa * ctx + pc * cty + ptx,
        pb * ctx + pd * cty + pty,
    )


def _rasterize(polys: list, textures: list[Image.Image], origin: tuple[float, float],
               size: tuple[int, int], scale: float) -> np.ndarray:
    width, height = size
    canvas = np.zeros((height, width, 4), dtype=np.float32)
    ox, oy = origin
    cache: dict[int, np.ndarray] = {}
    for texture_index, points, uvs, color in polys:
        if texture_index not in cache:
            tex = textures[texture_index]
            arr = np.asarray(tex.convert("RGBA"), dtype=np.float32) / 255.0
            arr[..., :3] *= arr[..., 3:4]
            cache[texture_index] = arr
        tex = cache[texture_index]
        th, tw = tex.shape[:2]
        p = np.array(points, dtype=np.float64) * scale
        p -= (ox * scale, oy * scale)
        uv = np.array(uvs, dtype=np.float64)
        n = len(points)
        if n < 3:
            continue
        # Ensure counter-clockwise winding for the barycentric inside test.
        area = 0.0
        for i in range(n):
            x1, y1 = p[i]
            x2, y2 = p[(i + 1) % n]
            area += x1 * y2 - x2 * y1
        order = list(range(n)) if area > 0 else list(range(n))[::-1]
        for t in range(1, n - 1):
            idx = [order[0], order[t], order[t + 1]]
            tri = p[idx]
            tuv = uv[idx]
            (ax, ay), (bx, by), (cx, cy) = tri
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-9:
                continue
            x0 = max(int(math.floor(tri[:, 0].min())), 0)
            x1 = min(int(math.ceil(tri[:, 0].max())), width)
            y0 = max(int(math.floor(tri[:, 1].min())), 0)
            y1 = min(int(math.ceil(tri[:, 1].max())), height)
            if x1 <= x0 or y1 <= y0:
                continue
            ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float64)
            xs += 0.5
            ys += 0.5
            l0 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / den
            l1 = ((cy - ay) * (xs - cx) + (ax - cx) * (ys - cy)) / den
            l2 = 1.0 - l0 - l1
            inside = (l0 >= -1e-4) & (l1 >= -1e-4) & (l2 >= -1e-4)
            if not inside.any():
                continue
            u = l0 * tuv[0, 0] + l1 * tuv[1, 0] + l2 * tuv[2, 0]
            v = l0 * tuv[0, 1] + l1 * tuv[1, 1] + l2 * tuv[2, 1]
            fx = np.clip(u * tw - 0.5, 0, tw - 1)
            fy = np.clip(v * th - 0.5, 0, th - 1)
            ix = np.floor(fx).astype(np.int32)
            iy = np.floor(fy).astype(np.int32)
            ix1 = np.minimum(ix + 1, tw - 1)
            iy1 = np.minimum(iy + 1, th - 1)
            wx = (fx - ix)[..., None].astype(np.float32)
            wy = (fy - iy)[..., None].astype(np.float32)
            sample = (
                tex[iy, ix] * (1 - wx) * (1 - wy)
                + tex[iy, ix1] * wx * (1 - wy)
                + tex[iy1, ix] * (1 - wx) * wy
                + tex[iy1, ix1] * wx * wy
            )
            sample *= inside[..., None]
            if color != IDENTITY_COLOR:
                r_add, g_add, b_add, a_mul, r_mul, g_mul, b_mul = color
                alpha = sample[..., 3:4]
                rgb = sample[..., :3] * np.array(
                    [r_mul / 255.0, g_mul / 255.0, b_mul / 255.0], dtype=np.float32
                )
                rgb += alpha * np.array(
                    [r_add / 255.0, g_add / 255.0, b_add / 255.0], dtype=np.float32
                )
                sample = np.concatenate([np.minimum(rgb, alpha), alpha], axis=-1) * np.float32(
                    a_mul / 255.0
                )
            dst = canvas[y0:y1, x0:x1]
            dst *= 1.0 - sample[..., 3:4]
            dst += sample
    return canvas


def _to_image(canvas: np.ndarray) -> Image.Image:
    alpha = canvas[..., 3:4]
    rgb = np.where(alpha > 1e-6, canvas[..., :3] / np.maximum(alpha, 1e-6), 0)
    out = np.clip(np.concatenate([rgb, alpha], axis=-1), 0, 1)
    return Image.fromarray((out * 255 + 0.5).astype(np.uint8), "RGBA")
