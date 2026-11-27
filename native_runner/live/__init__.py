"""Official-client live bridge: memory frames to V4 observations and ADB taps.

This package deliberately does not import torch or the policy.  It is the
sensor/actuator half of the live stack; the V4 brain runs in the training
process (or ``serve_policy``) and only ever sees an ``ObservationV1``.
"""

from .frames import LiveEntity, LiveFrame, LivePlayer, LiveTower, normalize_frame, parse_frame
from .observation import LiveObservationConfig, build_live_observation
from .taps import AdbTapper, ScreenLayout, taps_for_action, taps_for_actions

__all__ = [
    "AdbTapper",
    "LiveEntity",
    "LiveFrame",
    "LiveObservationConfig",
    "LivePlayer",
    "LiveTower",
    "ScreenLayout",
    "build_live_observation",
    "normalize_frame",
    "parse_frame",
    "taps_for_action",
    "taps_for_actions",
]
