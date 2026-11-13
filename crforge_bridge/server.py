"""The line-delimited JSON runner server FirstLight's ``NativeClashEnv`` speaks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import socketserver
import sys
import threading
from typing import Any

from .catalog import DEFAULT_CATALOG
from .engine import BridgeError, Engine
from .jvm import DEFAULT_INSTALL, DEFAULT_TABLES

MAX_REQUEST_BYTES = 16 * 1024 * 1024


class RunnerHandler(socketserver.StreamRequestHandler):
    """One connection: one command, or a ``session-v1`` persistent session."""

    engine: Engine  # set on the subclass in `serve`

    def handle(self) -> None:
        try:
            line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        except OSError:
            return
        if not line:
            return
        if line.startswith(b"session-v1"):
            self._reply({"ok": True, "session": "control-session.v1"})
            while True:
                try:
                    line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
                except OSError:
                    return
                if not line:
                    return
                if len(line) > MAX_REQUEST_BYTES:
                    self._reply({"ok": False, "error": "request exceeds 16 MiB"})
                    continue
                if not self._command(line.decode("utf-8", errors="replace").rstrip("\r\n")):
                    return
        else:
            self._command(line.decode("utf-8", errors="replace").rstrip("\r\n"), close_after=True)

    def _command(self, text: str, *, close_after: bool = False) -> bool:
        try:
            reply = dispatch(self.engine, text)
        except BridgeError as error:
            reply = {"ok": False, "error": str(error), "engine": "crforge"}
        except Exception as error:  # never hang, never leak a traceback into the socket
            reply = {"ok": False, "error": f"{type(error).__name__}: {error}", "engine": "crforge"}
        self._reply(reply)
        return not close_after

    def _reply(self, value: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        self.wfile.flush()


class ThreadedServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def dispatch(engine: Engine, text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise BridgeError("empty command")
    verb, _, rest = text.partition(" ")
    rest = rest.strip()

    if verb == "status":
        return engine.status()
    if verb == "attest":
        return engine.attest()
    if verb == "configure":
        return engine.configure(_json(rest, "configure"))
    if verb == "configure-native":
        raise BridgeError("configure-native is not supported by the headless crforge bridge")
    if verb == "step":
        return engine.step(int(rest))
    if verb == "advance-native":
        return engine.step(int(rest))
    if verb == "observe":
        return engine.observe()
    if verb == "observe-rich":
        return engine.observe_rich()
    if verb == "observe-atomic":
        return engine.observe_atomic()
    if verb == "inject":
        return engine.inject(_json(rest, "inject"))
    if verb == "deploy":
        fields = rest.split()
        if len(fields) != 6:
            raise BridgeError("deploy takes: player card_id card_parameter x y delay_ticks")
        return engine.deploy(*(int(value) for value in fields))
    if verb == "activate-ability":
        fields = rest.split()
        if len(fields) != 4:
            raise BridgeError("activate-ability takes: player object_index secondary_index execute_in_ticks")
        return engine.activate_ability(*(int(value) for value in fields))
    if verb == "pause":
        return engine.pause()
    if verb == "resume":
        return engine.resume()
    if verb == "speed":
        return engine.speed(float(rest))
    if verb == "render":
        return {"ok": True, "rendering": False, "ignored": True}
    if verb == "touch":
        return {"ok": True, "ignored": True}
    if verb in {"snapshot-create", "restore", "release-snapshot"}:
        raise BridgeError(f"{verb} is not implemented by the crforge bridge yet")
    if verb.startswith("multi-") or verb == "env":
        raise BridgeError(f"{verb} is not implemented by the crforge bridge yet")
    raise BridgeError(f"unknown command {verb!r}")


def _json(text: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise BridgeError(f"{label} payload is not JSON: {error}") from error
    if not isinstance(value, dict):
        raise BridgeError(f"{label} payload must be a JSON object")
    return value


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 26789,
    install: str | Path = DEFAULT_INSTALL,
    tables: str | Path = DEFAULT_TABLES,
    catalog: str | Path | None = None,
    ready: threading.Event | None = None,
) -> None:
    engine = Engine(
        install=Path(install),
        tables_dir=Path(tables),
        catalog_path=Path(catalog) if catalog else DEFAULT_CATALOG,
    )
    # Fail fast if the jars, tables or catalog are missing; the process is
    # useless as a silent listener.
    engine.start()
    handler = type("BoundRunnerHandler", (RunnerHandler,), {"engine": engine})
    with ThreadedServer((host, int(port)), handler) as server:
        if ready is not None:
            ready.set()
        print(
            f"crforge bridge listening on {host}:{port} "
            f"(tables {engine.tables_dir}, install {engine.install})",
            file=sys.stderr,
            flush=True,
        )
        server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="crforge runner bridge for FirstLight")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=26789)
    parser.add_argument("--install", default=str(DEFAULT_INSTALL))
    parser.add_argument("--tables", default=str(DEFAULT_TABLES))
    parser.add_argument("--catalog", default=None)
    args = parser.parse_args(argv)
    try:
        serve(
            host=args.host,
            port=args.port,
            install=args.install,
            tables=args.tables,
            catalog=args.catalog,
        )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
