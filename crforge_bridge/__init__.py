"""crforge as FirstLight's runner: a Python control server over the crforge battle core.

The server answers the same line-delimited JSON protocol FirstLight's
``NativeClashEnv`` speaks on ``127.0.0.1:26789``. It does not reimplement the
model, the evaluator, or ``ObservationV1``: it emits raw facts and lets Python
encode them.

Layout:

- :mod:`crforge_bridge.catalog`  FirstLight card ids <-> crforge row names
- :mod:`crforge_bridge.jvm`      one JVM per process, crforge jars on its path
- :mod:`crforge_bridge.engine`   ``LiveBattle`` + configure/step/inject/observe
- :mod:`crforge_bridge.vector_observation`  zero-copy facts for all vector lanes
- :mod:`crforge_bridge.vector_rows`         batched V4 row sections over those facts
- :mod:`crforge_bridge.vector_tensorize`    facts-only ``UniversalSemanticBatchV4`` per lane
- :mod:`crforge_bridge.vector_actions`       stacked ``ActionSequenceV4`` -> engine action index
- :mod:`crforge_bridge.rollout`              native-shape reward + ``StoredRolloutV4`` storage
- :mod:`crforge_bridge.rich`     the minimal v3 rich envelope
- :mod:`crforge_bridge.server`   the TCP server
"""

from __future__ import annotations

PROTOCOL = "crforge-control.v1"
BRIDGE_VERSION = "0.1.0"

__all__ = ["PROTOCOL", "BRIDGE_VERSION"]
