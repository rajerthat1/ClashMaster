"""Unit tests for the A51 PPO engine-node manifest builder."""

from __future__ import annotations

import json

import pytest

from tools.native_analysis import a51_engine_node


class FakeEnv:
    def __init__(self, port: int) -> None:
        self.port = port
        self.closed = False
        self.requests: list[str] = []

    def status(self) -> dict:
        return {"clientVersion": "16.635.1", "coldReady": True}

    def _request(self, command: str) -> dict:
        self.requests.append(command)
        return {}

    def close_transport(self) -> None:
        self.closed = True


def _endpoint(offset: int, *, digest: str = "d" * 64):
    port = 26789 + offset
    env = FakeEnv(port)
    status = {
        "ok": True,
        "engineId": offset,
        "pid": 1000 + offset,
        "processName": f"{a51_engine_node.PACKAGE}:engine{offset}",
        "port": port,
        "slotsPerEngine": 8,
        "assignedCpu": 4 + offset,
    }
    attestation = {
        "production_ready": True,
        "attestation_digest": digest,
        "probe_sha256": "p" * 64,
        "libg_sha256": "l" * 64,
        "content_version": "16.635.93",
        "content_fingerprint_sha256": "c" * 64,
    }
    return env, status, attestation


def test_manifest_attests_every_engine(monkeypatch, tmp_path):
    outputs = {offset: _endpoint(offset) for offset in range(2)}

    def wait_endpoint(port: int, timeout: float):
        return outputs[port - 26789]

    monkeypatch.setattr(a51_engine_node, "wait_endpoint", wait_endpoint)
    monkeypatch.setattr(a51_engine_node, "engine_cgroup", lambda serial, pid: "5:schedtune:/foreground")
    output = tmp_path / "engine-node-ready.json"
    assert (
        a51_engine_node.main(
            [
                "--serial",
                "SERIAL",
                "--engines",
                "2",
                "--output",
                str(output),
                "--no-verify-build",
            ]
        )
        == 0
    )
    manifest = json.loads(output.read_text())
    assert manifest["schema"] == "v4-ppo-engine-node.v1"
    assert manifest["engine_count"] == 2
    assert manifest["resident_slots_per_engine"] == 8
    assert manifest["decision_ticks"] == 5
    assert [row["port"] for row in manifest["engines"]] == [26789, 26790]
    assert manifest["checks"]["foreground"] is True
    assert all(outputs[offset][0].closed for offset in range(2))
    assert all("render off" in outputs[offset][0].requests for offset in range(2))


def test_background_engine_is_rejected(monkeypatch, tmp_path):
    outputs = {offset: _endpoint(offset) for offset in range(2)}
    monkeypatch.setattr(a51_engine_node, "wait_endpoint", lambda port, timeout: outputs[port - 26789])
    monkeypatch.setattr(
        a51_engine_node,
        "engine_cgroup",
        lambda serial, pid: "5:schedtune:/background" if pid == 1001 else "5:schedtune:/top-app",
    )
    with pytest.raises(RuntimeError, match=r"failed checks \['foreground'\]"):
        a51_engine_node.main(
            ["--serial", "SERIAL", "--engines", "2", "--output", str(tmp_path / "m.json"), "--no-verify-build"]
        )


def test_mismatched_engine_ids_are_rejected(monkeypatch, tmp_path):
    # Port 26789 reports engine 1's identity: the engine_id/process/port checks
    # must catch a mis-wired forward instead of trusting the port.
    by_port = {26789: _endpoint(1), 26790: _endpoint(1)}
    monkeypatch.setattr(a51_engine_node, "wait_endpoint", lambda port, timeout: by_port[port])
    monkeypatch.setattr(a51_engine_node, "engine_cgroup", lambda serial, pid: "5:schedtune:/foreground")
    with pytest.raises(RuntimeError, match="engine_id"):
        a51_engine_node.main(
            ["--serial", "SERIAL", "--engines", "2", "--output", str(tmp_path / "m.json"), "--no-verify-build"]
        )
