"""The resident adapter: same contract as the bridge, no wire under it."""

from __future__ import annotations

import json

import pytest

from crforge_bridge.engine import Engine
from crforge_bridge.observe import decode_rows, entity_rows
from native_runner.cr_native_env import RunnerError
from native_runner.crforge_resident import CrforgeResidentEnv

DECK = (26000021, 26000014, 26000038, 26000010, 27000000, 26000030, 28000001, 28000000)


def _configure_payload(seed: int = 1) -> dict[str, object]:
    def deck(_owner: int) -> dict[str, object]:
        return {"sp": [{"d": card, "el": 0, "l": 10} for card in DECK], "sc": [{"d": 159_000_000}]}

    return {
        "rndSeed": seed,
        "endTick": 7_200,
        "battle": {
            "deck0": deck(0),
            "deck1": deck(1),
            "hbd": [{"kt": 11}, {"kt": 11}],
            "gamemode": 72_000_006,
            "arena": 54_000_001,
            "location": 15_000_000,
            "avatar0": {"accountID.hi": 0, "accountID.lo": 0},
            "avatar1": {"accountID.hi": 0, "accountID.lo": 1},
        },
    }


def test_resident_status_identifies_the_adapter(resident_env: CrforgeResidentEnv) -> None:
    status = resident_env.status()
    assert status["engine"] == "crforge"
    assert status["resident"] is True
    assert status["lanes"] == 1
    assert status["transport"] == "in-process"


def test_configure_observe_and_step_match_the_bridge_exactly(
    resident_env: CrforgeResidentEnv, engine: Engine
) -> None:
    """The same commands on the same seed produce the same receipts, 50 steps long."""

    from crforge_bridge.server import dispatch

    payload = _configure_payload()
    bridge = engine.configure(payload)
    assert resident_env._request(f"configure {json.dumps(payload)}") == bridge

    hand = None
    for step in range(50):
        # Observe both views at the contract level: the ordinary envelope, the
        # rich one and the atomic pair are separately shaped and validated.
        assert resident_env._request("observe") == dispatch(engine, "observe")
        assert resident_env._request("observe-rich") == dispatch(engine, "observe-rich")
        assert resident_env._request("observe-atomic") == dispatch(engine, "observe-atomic")

        if step == 20 and hand is None:
            hand = resident_env.observe()["players"][0]["hand"][0]
            # The same play on both engines: a real command, not a wait.
            command = f"deploy 0 {int(hand['cardId'])} 0 9000 17000 1"
            resident_give = resident_env._request(command)
            bridge_give = dispatch(engine, command)
            assert resident_give == bridge_give

        assert resident_env._request("step 5") == dispatch(engine, "step 5")


def test_resident_raises_runner_errors_like_the_bridge(resident_env: CrforgeResidentEnv) -> None:
    with pytest.raises(RunnerError):
        resident_env.observe()  # configure was not called

    resident_env._request(f"configure {json.dumps(_configure_payload())}")
    with pytest.raises(RunnerError, match="unknown command"):
        resident_env._request("definitely-not-a-command")


def test_capture_cache_cannot_serve_a_stale_tick(resident_env: CrforgeResidentEnv) -> None:
    resident_env._request(f"configure {json.dumps(_configure_payload())}")
    resident_env.step(95)
    before = resident_env.observe()
    assert resident_env.observe() == before  # the second read is the cache

    hand = before["players"][0]["hand"][0]
    resident_env._request(f"deploy 0 {int(hand['cardId'])} 0 9000 17000 1")
    # The engine writes a seat observation when it ends a decision, so the first
    # visible change is after one tick, not on the giving.
    resident_env.step(1)
    pending = resident_env.observe()
    assert pending["tick"] == before["tick"] + 1
    assert pending["players"][0]["pendingHandSlots"] == [0]
    assert int(pending["players"][0]["elixirReservedRaw"]) > 0

    resident_env.step(5)
    after = resident_env.observe()
    assert after["tick"] == pending["tick"] + 5


def test_engine_close_drops_the_battle_but_keeps_the_engine(engine: Engine) -> None:
    engine.configure(_configure_payload())
    engine.step(5)
    engine.close()
    assert engine.status()["ready"] is False
    # A closed battle must not look like a configured one, but a fresh match
    # must still build in the same engine (the JVM is never restarted).
    with pytest.raises(RuntimeError):
        engine.observe()
    engine.configure(_configure_payload())
    assert engine.observe()["tick"] == 0


def test_entity_rows_reuses_decoded_rows(engine: Engine) -> None:
    engine.configure(_configure_payload())
    engine.step(95)
    observation = engine._observation(0)
    rows, _header = decode_rows(observation, engine._schema, engine._vocabulary, engine._catalog)
    reused = entity_rows(observation, engine._schema, engine._vocabulary, engine._catalog, rows=rows)
    default = entity_rows(observation, engine._schema, engine._vocabulary, engine._catalog)
    assert reused == default
