"""The resident crforge environment: ``NativeClashEnv`` with no wire under it.

Stage C2 of [`ROADMAP_CRFORGE.md`](../ROADMAP_CRFORGE.md) moves the bulk path
off the loopback socket.  The bridge already speaks FirstLight's
``cr-native-control.v1`` line protocol, and :class:`NativeClashEnv` is a thin
client over it: every public method (`create_match`, `observe`,
`observe_atomic`, `queue_hand_action_at`, `attest`, `status`,
`close_transport`) is one ``_request(command)`` call.  So the resident adapter
replaces exactly that seam:

    transport:  JSON line over TCP  ->  dispatch() into an in-process Engine
    contract:   unchanged           ->  the same command strings and dicts

That is deliberate.  It makes bridge parity a property of construction rather
than a test result, and it keeps one encoder: FirstLight's ``BattleEnvV1`` /
``ObservationV1`` / tensorizer still build the policy row above the seam.  The
throughput win is the socket round trip (and the bridge's duplicate seat decode,
which is now shared); the *per-lane* observation build stays where C3 addresses
it.

What this is not:

* Not a second engine.  It is ``crforge_bridge.engine.Engine`` (one
  ``LiveBattle``) running in this process.
* Not ``VectorBattle``.  The 256-lane zero-copy vector is C3/C4; it needs a
  batched observation contract, which does not exist yet.  :meth:`status`
  reports ``"resident": true`` and ``"lanes": 1`` so a run artifact cannot
  mistake one for the other.
* Not a gate.  Promotion and parity stay on the native engine.

Usage::

    from native_runner.crforge_resident import CrforgeResidentEnv

    with CrforgeResidentEnv() as env:          # same surface as NativeClashEnv
        env.create_match(match_config)
        observations = env.observe_atomic()

``BattleEnvV1`` detects it the same way it detects the bridge (``status()``
reports ``engine: crforge``), so the evaluator, the trainer and the C0 soak can
be pointed at it without a branch of their own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

from .cr_native_env import NativeClashEnv, RunnerError

DEFAULT_INSTALL = Path(__file__).resolve().parents[1] / ".resource" / "crforge" / "env" / "build" / "install" / "env"
DEFAULT_TABLES = Path.home() / ".crforge" / "tables" / "16.635.8"


class CrforgeResidentError(RunnerError):
    """The resident engine could not be built, or refused a command."""


def _adopt_jvm_identity(install: Path) -> None:
    """Let ``crforge_env`` reuse the JVM the bridge started.

    ``crforge_bridge.jvm`` and ``crforge_env._jvm`` each remember which
    distribution started the process JVM, and each refuses one it did not
    start.  A C3 tool that builds a ``VectorBattle`` beside the resident engine
    must see the same folder, exactly as ``crforge_bridge/tests/conftest.py``
    arranges for the bridge tests.
    """

    try:
        from crforge_env import _jvm as env_jvm
    except ImportError:  # the vector package is optional
        return
    if getattr(env_jvm, "_started_with", None) is None:
        env_jvm._started_with = install


class CrforgeResidentEnv(NativeClashEnv):
    """``NativeClashEnv`` backed by an in-process crforge ``Engine``.

    Every transport method is inherited; only ``_request`` and
    ``close_transport`` change.  Validation, envelope checks, action queueing
    and the attestation contract all stay on the shared path, so a resident run
    and a bridge run cannot disagree about the protocol.
    """

    def __init__(
        self,
        *,
        install: str | Path = DEFAULT_INSTALL,
        tables: str | Path = DEFAULT_TABLES,
        catalog: str | Path | None = None,
        engine: Any | None = None,
    ) -> None:
        # ``host``/``port`` are inert: nothing listens.  They are kept so a repr
        # or a log line reads like the transport it replaces.
        super().__init__(host="resident", port=0, timeout=0.0)
        self._install = Path(install).expanduser().resolve()
        self._tables = Path(tables).expanduser().resolve()
        self._catalog = Path(catalog) if catalog is not None else None
        self._engine = engine
        self._owns_engine = engine is None

    # ---- lifecycle -----------------------------------------------------

    @property
    def engine(self) -> Any:
        """The in-process bridge engine, started on first use."""

        if self._engine is None:
            from crforge_bridge.catalog import DEFAULT_CATALOG
            from crforge_bridge.engine import Engine
            from crforge_bridge.jvm import start as start_jvm

            start_jvm(self._install)
            _adopt_jvm_identity(self._install)
            self._engine = Engine(
                install=self._install,
                tables_dir=self._tables,
                catalog_path=Path(self._catalog) if self._catalog is not None else DEFAULT_CATALOG,
            )
            self._engine.start()
        return self._engine

    def close_transport(self) -> None:
        """Release the live battle (if owned) and the base transport state."""

        with self._lock:
            self._close_persistent_transport_locked()
            engine, self._engine = self._engine, None
            if engine is not None and self._owns_engine:
                engine.close()

    def close(self) -> None:
        self.close_transport()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close_transport()

    # ---- the seam ------------------------------------------------------

    def _request(self, command: str, *, _read_retry_count: int = 2) -> dict[str, Any]:
        """Answer one control command in-process.

        The signatures and receipts are the bridge's own: ``dispatch`` is the
        exact function the socket handler calls.  Only the transport error
        type is translated, so callers see ``RunnerError`` for both paths.
        """

        from crforge_bridge.engine import BridgeError
        from crforge_bridge.server import dispatch

        try:
            reply = dispatch(self.engine, command)
        except BridgeError as error:
            raise RunnerError(str(error)) from error
        except RunnerError:
            raise
        except Exception as error:
            raise RunnerError(f"{type(error).__name__}: {error}") from error
        if not isinstance(reply, dict) or not reply.get("ok"):
            detail = reply.get("error", reply) if isinstance(reply, dict) else reply
            raise RunnerError(str(detail))
        return reply

    # ---- identity ------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """The bridge status plus the fact that this process is the engine.

        ``BattleEnvV1._runner_engine`` reads ``engine`` only, so adding fields
        is safe.  ``resident``/``lanes`` keep a run artifact from confusing this
        with the 256-lane vector or the socket bridge.
        """

        reply = dict(super().status())
        reply.update({"resident": True, "lanes": 1, "transport": "in-process"})
        return reply

    def tables_identity(self) -> dict[str, Any]:
        return self.engine.tables_identity()


def open_resident_env(
    *,
    install: str | Path = DEFAULT_INSTALL,
    tables: str | Path = DEFAULT_TABLES,
    catalog: str | Path | None = None,
) -> CrforgeResidentEnv:
    """Build a resident env with the standard paths (a one-line factory)."""

    return CrforgeResidentEnv(install=install, tables=tables, catalog=catalog)
