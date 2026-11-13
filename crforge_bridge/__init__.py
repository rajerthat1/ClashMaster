"""crforge as FirstLight's runner: a Python control server over the crforge battle core.

The server answers the same line-delimited JSON protocol FirstLight's
``NativeClashEnv`` speaks on ``127.0.0.1:26789``. It does not reimplement the
model, the evaluator, or ``ObservationV1``: it emits raw facts and lets Python
encode them.

Layout:

- :mod:`crforge_bridge.catalog`  FirstLight card ids <-> crforge row names
- :mod:`crforge_bridge.jvm`      one JVM per process, crforge jars on its path
- :mod:`crforge_bridge.engine`   ``LiveBattle`` + configure/step/inject/observe
- :mod:`crforge_bridge.rich`     the minimal v3 rich envelope
- :mod:`crforge_bridge.server`   the TCP server
"""

from __future__ import annotations

PROTOCOL = "crforge-control.v1"
BRIDGE_VERSION = "0.1.0"

__all__ = ["PROTOCOL", "BRIDGE_VERSION"]
