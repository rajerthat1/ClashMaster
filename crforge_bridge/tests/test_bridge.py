from __future__ import annotations

import json
from pathlib import Path
import socket
import threading

import pytest

from crforge_bridge.catalog import (
    Catalog,
    FORM_BASE,
    FORM_EVOLVED,
    pack_card_parameter,
    slot_flags_for_availability,
    unpack_card_parameter,
)
from crforge_bridge.engine import Engine
from crforge_bridge.server import RunnerHandler, ThreadedServer
from native_runner.contracts import CrforgeAttestationV1
from native_runner.ruleset import DEFAULT_CRFORGE_RELEASE


DECK = (26000021, 26000014, 26000038, 26000010, 27000000, 26000030, 28000001, 28000000)


def test_catalog_packs_and_unpacks_the_client_word() -> None:
    catalog = Catalog.load()
    assert catalog.name(26000021) == "HogRider"
    assert catalog.card_id("HogRider") == 26000021
    assert catalog.form_name(26000021, FORM_BASE) == "HogRider"
    assert catalog.form_name(26000021, FORM_EVOLVED) == "HogRider_EV1"
    assert catalog.data_versions[:2] == ("16.635.3", "16.635.8")

    packed = pack_card_parameter(
        deck_slot=3,
        level=11,
        cost=4,
        form_code=FORM_EVOLVED,
        slot_flags=3,
        option=1,
        count=1,
        cosmetic=2,
    )
    assert unpack_card_parameter(packed) == {
        "form_code": FORM_EVOLVED,
        "option": 1,
        "count": 1,
        "level": 11,
        "cosmetic": 2,
        "slot_flags": 3,
        "deck_slot": 3,
        "cost": 4,
    }
    assert slot_flags_for_availability(3) == 3
    with pytest.raises(ValueError):
        pack_card_parameter(deck_slot=8, level=11, cost=4)
    with pytest.raises(ValueError):
        slot_flags_for_availability(4)


def test_form_unit_rows_map_to_their_base_cards() -> None:
    """Hero and evolution unit rows are cards, not anonymous effect rows."""

    from crforge_bridge.observe import _nearest_card_id

    catalog = Catalog.load()
    assert _nearest_card_id(catalog, "KnightHero") == 26_000_000
    assert _nearest_card_id(catalog, "MusketeerHero") == 26_000_014
    assert _nearest_card_id(catalog, "ValkyrieHero") == 26_000_011
    assert _nearest_card_id(catalog, "Archer_EV1") == 26_000_001
    # A spawned unit has no card of its own and must stay unclassified.
    assert _nearest_card_id(catalog, "Skeleton") is None
    assert _nearest_card_id(catalog, "KingTower") is None


def test_run_refusal_names_use_the_match_gate_namespace(engine: Engine) -> None:
    """``lastRunRefusal`` is a LadderMatch code, not a PlayerCommands.Refusal.

    The two namespaces overlap at 4, where the schema list says START_COOLDOWN
    and the match says OVER.  Naming run refusals from the schema list once
    reported a decided match as a start cooldown.
    """

    names = engine._match_refusal_names
    assert names[4] == "OVER"
    assert names[8] == "KING_DEAD"
    assert names[9] == "NOT_IN_HAND"
    assert names[0xD] == "NOT_ENOUGH_ELIXIR"
    assert names[0x16] == "START_COOLDOWN"
    assert names[1000] == "RUN_NOT_PLACED"


def test_attestation_matches_the_checked_in_release(engine: Engine) -> None:
    raw = engine.attest()
    attestation = CrforgeAttestationV1.from_mapping(raw["crforge"])
    release = json.loads(Path(DEFAULT_CRFORGE_RELEASE).read_text(encoding="utf-8"))
    pinned = release["data_versions"][release["default_data_version"]]
    assert attestation.production_ready
    assert attestation.data_version == release["default_data_version"]
    assert attestation.client_version == release["client_version"]
    assert attestation.content_sha == pinned["content_sha"]
    assert attestation.tables_sha256 == pinned["tables_sha256"]
    assert attestation.vocabulary_sha256 == pinned["vocabulary_sha256"]
    assert attestation.catalog_sha256 == release["catalog_sha256"]
    assert attestation.schema == pinned["schema"]


def _configure_payload(seed: int = 1) -> dict[str, object]:
    def deck(owner: int) -> dict[str, object]:
        return {
            "sp": [{"d": card, "el": 0, "l": 10} for card in DECK],
            "sc": [{"d": 159_000_000}],
        }

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


def test_configure_observe_and_short_episode(engine: Engine) -> None:
    reply = engine.configure(_configure_payload())
    assert reply["ok"]
    observation = engine.observe()
    assert observation["tick"] == 0
    assert len(observation["players"]) == 2
    assert len(observation["players"][0]["hand"]) == 4
    assert isinstance(observation["objects"], list)
    assert observation["ended"] is False

    rich = engine.observe_rich()
    assert rich["schema"] == "native-rich-telemetry.v3"
    assert rich["tick"] == 0
    assert set(rich["players"][0]) == {"owner", "ownerRootValidated", "ownerEntityKey", "abilityRuntime", "evolutionRuntime"}

    atomic = engine.observe_atomic()
    assert atomic["atomic"] is True
    assert atomic["ordinary"]["tick"] == 0
    assert atomic["rich"]["tick"] == 0

    stepped = engine.step(5)
    assert stepped["ok"] and stepped["stopped"] is False
    assert engine.observe()["tick"] == 5


def test_hero_unit_row_maps_to_its_base_card_and_unsigned_global_id(engine: Engine) -> None:
    """A hero form is a public unit row, not an unknown effect row.

    crforge names hero units ``KnightHero`` and reports the LogicData global id
    as a signed 32-bit int.  The bridge used to emit ``cardId`` 0 and the
    placeholder ``dataGlobalId`` 1, which left the archetype unresolved and made
    the V4 tensorizer fail closed on the X-Bow learner deck (Knight hero).
    """

    for seed in range(1, 40):
        payload = _configure_payload(seed)
        for owner in (0, 1):
            payload["battle"][f"deck{owner}"]["sp"][0] = {"d": 26000000, "el": 2, "l": 11}
        engine.configure(payload)
        hand = engine.observe()["players"][0]["hand"]
        if any(int(card["cardId"]) == 26_000_000 for card in hand):
            break
    else:  # pragma: no cover - defensive
        pytest.fail("no seed put the Knight hero in the opening hand")
    engine.step(90)
    reply = engine.deploy(0, 26_000_000, 0, 9000, 17000)
    assert reply["injected"] is True
    engine.step(40)

    rich = engine.observe_rich()
    hero_rows = [
        item
        for item in rich["objects"]
        if int(item.get("cardId", 0)) == 26_000_000 and int(item.get("dataGlobalId", 1)) > 1
    ]
    assert hero_rows, "the deployed Knight hero did not appear in the rich observation"
    # Unsigned bit pattern of the Java int -437504191; the compiled archetype
    # catalog and the native probe both use this form.
    assert {int(item["dataGlobalId"]) for item in hero_rows} == {3_857_463_105}


def test_spawned_character_carries_the_native_logic_global_id(engine: Engine) -> None:
    engine.configure(_configure_payload())
    engine.step(90)
    hand = engine.observe()["players"][0]["hand"]
    assert hand
    engine.deploy(0, int(hand[0]["cardId"]), 0, 9000, 17000)
    engine.step(25)

    rich = engine.observe_rich()
    spawned = [
        item
        for item in rich["objects"]
        if 26_000_000 <= int(item["cardId"]) < 27_000_000
    ]
    assert spawned, "the deployed troop did not appear in the rich observation"
    for item in spawned:
        # Character LogicData global ids are 34xxxxxx; FirstLight card ids are
        # 26xxxxxx. Resolving one as the other is what the V4 tensorizer rejects.
        assert int(item["dataGlobalId"]) == 34_000_000 + (int(item["cardId"]) - 26_000_000)


def test_pending_projection_and_typed_card_pending(engine: Engine) -> None:
    engine.configure(_configure_payload())
    engine.step(90)
    hand = engine.observe()["players"][0]["hand"]
    card = int(hand[0]["cardId"])

    first = engine.deploy(0, card, 0, 9000, 17000)
    assert first["injected"] is True
    # The real command delay is 20 ticks; refuse to invent a +1 receipt.
    assert int(first["executeTick"]) == int(first["runTick"]) + 1
    assert int(first["executeTick"]) > int(first["tick"])

    engine.step(1)
    facts = engine.observe()["players"][0]
    assert facts["pendingHandSlots"] == [0]
    assert facts["pending"]
    assert int(facts["elixirReservedRaw"]) > 0

    # A second play of the held card is a typed rejection, not a ghost queue.
    second = engine.deploy(0, card, 0, 9000, 17000)
    assert second["injected"] is False
    assert second["refusal"] == "CARD_PENDING"
    assert int(second["executeTick"]) == int(second["runTick"]) + 1

    receipt = engine.step(25)
    assert receipt["runRefusals"] == []
    assert engine.observe()["players"][0]["pendingHandSlots"] == []


def test_tcp_dispatch_answers_status_and_unknown_verbs(engine: Engine) -> None:
    handler = type("BoundRunnerHandler", (RunnerHandler,), {"engine": engine})
    server = ThreadedServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    def request(command: str) -> dict[str, object]:
        with socket.create_connection((host, port), timeout=10.0) as connection:
            connection.sendall(command.encode("utf-8") + b"\n")
            payload = b""
            while not payload.endswith(b"\n"):
                chunk = connection.recv(65536)
                if not chunk:
                    break
                payload += chunk
        return json.loads(payload.decode("utf-8"))

    try:
        status = request("status")
        assert status["ok"] is True
        assert status["engine"] == "crforge"
        assert status["protocol"] == "crforge-control.v1"

        unknown = request("definitely-not-a-command")
        assert unknown["ok"] is False
        assert "unknown command" in str(unknown["error"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10.0)
