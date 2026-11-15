"""Tolerant TOML loading for decompressed game assets.

Supercell's runtime accepts a table declared twice and merges the bodies: the
16.635.1 ``character_buffs.toml`` declares ``[ImmuneToWeakPushback]`` once with
``IgnorePushBack`` and later with ``Rarity``.  ``tomllib`` follows the TOML
spec and rejects that, which made the 16.635 catalogs look uncompilable.

:func:`loads` first uses the strict parser.  Only when it rejects a duplicate
table does it merge repeated table headers (keeping first-seen order) and retry;
a genuinely malformed document still raises the strict error.
"""

from __future__ import annotations

import tomllib


def _merge_duplicate_tables(text: str) -> str:
    sections: list[tuple[str | None, list[str]]] = []
    index: dict[str, int] = {}
    current: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[[") and stripped.endswith("]]"):
            sections.append((stripped, []))
            current = len(sections) - 1
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            header = stripped
            if header in index:
                current = index[header]
            else:
                sections.append((header, []))
                index[header] = len(sections) - 1
                current = len(sections) - 1
            continue
        if current is None:
            sections.append((None, []))
            current = len(sections) - 1
        sections[current][1].append(line)
    lines: list[str] = []
    for header, body in sections:
        if header is not None:
            lines.append(header)
        lines.extend(body)
    return "\n".join(lines)


def loads(text: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as strict_error:
        merged = _merge_duplicate_tables(text)
        if merged == text:
            raise
        try:
            return tomllib.loads(merged)
        except tomllib.TOMLDecodeError:
            raise strict_error from None
