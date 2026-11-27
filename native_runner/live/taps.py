"""ADB tap planning for the official client.

The reader gives the model native grid cells; this module turns a decoded
``ActionV1`` into ordinary Android touch events.  Coordinates come from the
same 1080x1920 calibration used by the r0/Hasty MuMu bots, expressed as
fractions so a 720x1280 instance works without a second table.

Nothing here writes game memory.  An ADB tap is the same input event a human
finger produces, which is why it is the safest actuation path on the official
client.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..contracts import ActionKind, ActionV1, BOARD_HEIGHT, BOARD_WIDTH

VIEWPORT_ASPECT = 9.0 / 16.0
ARENA_LEFT_FRACTION = 0.055
ARENA_RIGHT_FRACTION = 0.945
ARENA_TOP_FRACTION = 0.105 - 0.685 / 32.0
ARENA_BOTTOM_FRACTION = 0.790
HAND_Y_FRACTION = 0.890
HAND_X_FRACTIONS = (0.31, 0.50, 0.69, 0.88)

WORLD_WIDTH = BOARD_WIDTH * 1000
WORLD_HEIGHT = BOARD_HEIGHT * 1000


class TapError(ValueError):
    """An action cannot be expressed as a touch on this screen."""


@dataclass(frozen=True, slots=True)
class ScreenLayout:
    width: int
    height: int
    viewport_left: float
    viewport_width: float
    arena_left: float
    arena_right: float
    arena_top: float
    arena_bottom: float
    hand_y: float
    hand_x: tuple[float, float, float, float]

    @classmethod
    def from_size(cls, width: int, height: int) -> "ScreenLayout":
        if width <= 0 or height <= 0:
            raise TapError("display size must be positive")
        viewport_width = min(float(width), float(height) * VIEWPORT_ASPECT) if width / height > 0.8 else float(width)
        left = (width - viewport_width) / 2.0
        return cls(
            width=int(width),
            height=int(height),
            viewport_left=left,
            viewport_width=viewport_width,
            arena_left=left + viewport_width * ARENA_LEFT_FRACTION,
            arena_right=left + viewport_width * ARENA_RIGHT_FRACTION,
            arena_top=height * ARENA_TOP_FRACTION,
            arena_bottom=height * ARENA_BOTTOM_FRACTION,
            hand_y=height * HAND_Y_FRACTION,
            hand_x=tuple(left + viewport_width * fraction for fraction in HAND_X_FRACTIONS),  # type: ignore[arg-type]
        )

    def hand_point(self, slot: int) -> tuple[int, int]:
        if not 0 <= int(slot) < 4:
            raise TapError("hand slot must be 0..3")
        return self._inside(round(self.hand_x[int(slot)]), round(self.hand_y))

    def world_point(self, x_units: float, y_units: float) -> tuple[int, int]:
        """World units (0..18000, 0..32000) to a display pixel."""

        x_fraction = min(max(float(x_units) / WORLD_WIDTH, 0.0), 1.0)
        y_fraction = min(max(float(y_units) / WORLD_HEIGHT, 0.0), 1.0)
        return self._inside(
            round(self.arena_left + x_fraction * (self.arena_right - self.arena_left)),
            round(self.arena_bottom - y_fraction * (self.arena_bottom - self.arena_top)),
        )

    def cell_point(self, cell: Sequence[int]) -> tuple[int, int]:
        """A native grid cell ``(column, row)`` to a display pixel."""

        if len(cell) != 2:
            raise TapError("target grid must contain x and y")
        column, row = int(cell[0]), int(cell[1])
        if not 0 <= column < BOARD_WIDTH or not 0 <= row < BOARD_HEIGHT:
            raise TapError("target grid is outside the arena")
        return self.world_point((column + 0.5) * 1000.0, (row + 0.5) * 1000.0)

    def _inside(self, x: int, y: int) -> tuple[int, int]:
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise TapError(f"tap ({x}, {y}) is outside the {self.width}x{self.height} display")
        return x, y


def taps_for_action(action: ActionV1, layout: ScreenLayout) -> tuple[tuple[int, int], ...]:
    """Return the ordered touches for one decoded native action."""

    if action.kind in (ActionKind.WAIT, ActionKind.WAIT_UNTIL_AFFORDABLE):
        return ()
    if action.kind == ActionKind.PLAY_CARD:
        if action.hand_slot is None:
            raise TapError("play_card has no hand slot")
        if action.target_grid is None:
            raise TapError("play_card has no target grid")
        return layout.hand_point(action.hand_slot), layout.cell_point(action.target_grid)
    if action.kind == ActionKind.ACTIVATE_ABILITY:
        # Champion abilities need a source-entity screen point that the current
        # readers do not expose.  Refuse rather than tap a guess.
        raise TapError("ability activation is not mapped on the official client yet")
    raise TapError(f"unsupported action kind: {action.kind}")


def taps_for_actions(actions: Iterable[ActionV1], layout: ScreenLayout) -> tuple[tuple[int, int], ...]:
    points: list[tuple[int, int]] = []
    for action in actions:
        points.extend(taps_for_action(action, layout))
    return tuple(points)


class AdbTapper:
    """Send taps through ``adb shell input tap`` on one serial."""

    def __init__(self, serial: str, adb: str = "adb", *, inter_tap_ms: int = 50) -> None:
        self.serial = serial
        self.adb = adb
        self.inter_tap_ms = int(inter_tap_ms)
        if self.inter_tap_ms < 0:
            raise TapError("inter_tap_ms must be non-negative")

    def _run(self, *arguments: str, timeout: float = 10.0) -> str:
        result = subprocess.run(
            [self.adb, "-s", self.serial, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode:
            raise TapError(result.stderr.strip() or result.stdout.strip() or "adb failed")
        return result.stdout

    def tap(self, point: tuple[int, int]) -> None:
        self._run("shell", "input", "tap", str(int(point[0])), str(int(point[1])))

    def send(self, points: Sequence[tuple[int, int]]) -> None:
        """Send a card + target pair in one Android-side command.

        Keeping the 50 ms gap on the device avoids a host round trip between
        the card tap and the arena tap, which is the difference between a
        clean deploy and a cancelled selection.
        """

        if not points:
            return
        if len(points) == 1:
            self.tap(points[0])
            return
        parts = []
        for index, (x, y) in enumerate(points):
            if index:
                parts.append(f"sleep {self.inter_tap_ms / 1000.0:.3f}")
            parts.append(f"input tap {int(x)} {int(y)}")
        self._run("shell", "; ".join(parts))

    def display_size(self) -> tuple[int, int]:
        output = self._run("shell", "wm", "size")
        for token in output.replace(":", " ").split():
            if "x" in token:
                width, _, height = token.partition("x")
                if width.isdigit() and height.isdigit():
                    return int(width), int(height)
        raise TapError(f"cannot parse display size from {output!r}")

    def layout(self) -> ScreenLayout:
        return ScreenLayout.from_size(*self.display_size())

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"AdbTapper(serial={self.serial!r}, adb={shlex.quote(self.adb)!r})"
