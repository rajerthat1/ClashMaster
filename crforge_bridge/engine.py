"""The crforge battle behind FirstLight's runner protocol.

One ``LiveBattle`` is configured from FirstLight's ``configure`` payload. The
environment decides every tick (``EnvConfig.decisionTicks=1``), so a seat's
observation is current after every ``step``; FirstLight owns the decision
cadence, not the bridge.

Plays are injected as the native command JSON FirstLight already sends
(``inject {"ct":153,...}``), resolved against the current hand and given through
``LiveBattle.play`` / ``useAbility``, so crforge's own 20-tick command delay and
elixir promise apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import threading
from typing import Any

import numpy as np

from . import PROTOCOL, BRIDGE_VERSION
from .catalog import Catalog, DEFAULT_CATALOG, TOWER_SELECTIONS, slot_flags_for_availability
from .jvm import DEFAULT_INSTALL, DEFAULT_TABLES, java_class, start
from .observe import (
    UNKNOWN,
    atomic_envelope,
    empty_player,
    entity_rows,
    player_facts,
    rich_envelope,
)
from native_runner.contracts import CrforgeAttestationV1, content_hash

COMMAND_TYPE_PLAY = 153
COMMAND_TYPE_ABILITY = 189
CMD_PLAY = 86  # FirstLight's live inject command type
PLAY_DELAY_TICKS = 20


class BridgeError(RuntimeError):
    """A command the bridge refuses, in a JSON reply rather than a hang."""


@dataclass
class BattleConfig:
    seed: int
    decision_ticks: int
    level: int
    tower_level: int
    max_ticks: int
    decks: tuple[list[str], list[str]]
    slots: tuple[list[int], list[int]]
    tower_troop: tuple[str, str]
    king_levels: tuple[int, int]
    card_levels: tuple[list[int], list[int]]
    account_ids: tuple[int, int] = (0, 1)
    game_mode: int = 72_000_006
    arena: int = 54_000_001
    location: int = 15_000_000


@dataclass
class Engine:
    install: Path = DEFAULT_INSTALL
    tables_dir: Path = DEFAULT_TABLES
    catalog_path: Path = DEFAULT_CATALOG
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _started: bool = field(default=False, init=False)
    _battle: Any = field(default=None, init=False)
    _config: BattleConfig | None = field(default=None, init=False)
    _generation: int = field(default=0, init=False)
    _sequence: int = field(default=0, init=False)
    _command_seq: int = field(default=0, init=False)
    _attestation: dict[str, Any] | None = field(default=None, init=False)

    # ---- lifecycle -----------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            start(self.install)
            self._catalog = Catalog.load(self.catalog_path)
            J = java_class
            path_class = J("java.nio.file.Path")
            vector_env = J("org.crforge.env.VectorEnv")
            self._tables = vector_env.tables(path_class.of(str(Path(self.tables_dir).expanduser().resolve())))
            self._vocabulary = vector_env.vocabulary(self._tables)
            self._schema = J("org.crforge.env.Schema")
            refusal_names = self._schema.refusalNames()
            self._refusal_names = [str(name) if name is not None else "" for name in refusal_names]
            self._live_battle_class = J("org.crforge.env.LiveBattle")
            self._env_config_class = J("org.crforge.env.EnvConfig")
            self._side_setup_class = J("org.crforge.env.SideSetup")
            self._int_array = J("jpype.JArray" if False else "java.lang.Object")  # replaced below
            import jpype

            self._int_array = jpype.JArray(jpype.JInt)
            self._string_list = J("java.util.ArrayList")
            self._started = True

    def _jlist(self, values):
        result = self._string_list()
        for value in values:
            result.add(value)
        return result

    def _observation(self, owner: int) -> list[int]:
        """Copy one JPype seat observation into a plain Python list.

        Iterating a JPype array from Python costs ~7 ms per 7,024-int seat,
        while a numpy copy plus ``tolist()`` costs well under a millisecond.
        The plain list keeps the rest of the bridge JSON-ready.
        """

        return np.asarray(self._battle.observation(int(owner)), dtype=np.int32).tolist()

    # ---- identity ------------------------------------------------------

    def tables_identity(self) -> dict[str, Any]:
        folder = Path(self.tables_dir).expanduser().resolve()
        digest = hashlib.sha256()
        for path in sorted(folder.iterdir()):
            if path.is_file():
                digest.update(path.name.encode("utf-8"))
                digest.update(b"\0")
                digest.update(hashlib.sha256(path.read_bytes()).digest())
        properties: dict[str, str] = {}
        build = folder / "build.properties"
        if build.is_file():
            for line in build.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    properties[key.strip()] = value.strip()
        return {
            "engine": "crforge",
            "protocol": PROTOCOL,
            "bridge_version": BRIDGE_VERSION,
            "data_version": properties.get("data_version", folder.name),
            "client_version": properties.get("client_version"),
            "content_sha": properties.get("content_sha"),
            "schema": properties.get("schema"),
            "lock_commit": _lock_commit(),
            "compatible": _lock_compatible(),
            "tables_sha256": digest.hexdigest(),
            "catalog_sha256": _sha256_file(self.catalog_path),
            "vocabulary_sha256": hashlib.sha256(str(self._vocabulary.hash()).encode("utf-8")).hexdigest()
            if self._started
            else None,
        }

    def attest(self) -> dict[str, Any]:
        with self._lock:
            self.start()
            identity = self.tables_identity()
            crforge_identity = {
                "bridge_version": str(identity["bridge_version"]),
                "catalog_sha256": str(identity.get("catalog_sha256") or ""),
                "client_version": str(identity.get("client_version") or ""),
                "compatible": [str(item) for item in identity.get("compatible") or ()],
                "content_sha": str(identity.get("content_sha") or ""),
                "data_version": str(identity.get("data_version") or ""),
                "lock_commit": str(identity.get("lock_commit") or ""),
                "protocol_version": str(identity["protocol"]),
                "schema": str(identity.get("schema") or ""),
                "tables_sha256": str(identity.get("tables_sha256") or ""),
                "version": CrforgeAttestationV1.VERSION,
                "vocabulary_sha256": str(identity.get("vocabulary_sha256") or ""),
            }
            crforge_ready = crforge_identity["data_version"] in crforge_identity["compatible"] and all(
                crforge_identity[name]
                for name in (
                    "bridge_version",
                    "catalog_sha256",
                    "client_version",
                    "content_sha",
                    "data_version",
                    "lock_commit",
                    "schema",
                    "tables_sha256",
                    "vocabulary_sha256",
                )
            )
            crforge_attestation = CrforgeAttestationV1(
                protocol_version=crforge_identity["protocol_version"],
                bridge_version=crforge_identity["bridge_version"],
                data_version=crforge_identity["data_version"],
                client_version=crforge_identity["client_version"],
                content_sha=crforge_identity["content_sha"],
                lock_commit=crforge_identity["lock_commit"],
                compatible=tuple(crforge_identity["compatible"]),
                tables_sha256=crforge_identity["tables_sha256"],
                vocabulary_sha256=crforge_identity["vocabulary_sha256"],
                catalog_sha256=crforge_identity["catalog_sha256"],
                schema=crforge_identity["schema"],
                production_ready=crforge_ready,
                attestation_digest=content_hash(crforge_identity),
            )
            native = {
                "version": "runner-attestation.v1",
                "protocol_version": "cr-native-control.v1",
                "package_name": "crforge",
                "abi": "jvm",
                "probe_sha256": "",
                "libg_sha256": "",
                "libg_build_id": "",
                "content_fingerprint_sha256": hashlib.sha256(
                    str(identity.get("content_sha") or "").encode("utf-8")
                ).hexdigest(),
                "content_assets_sha256": hashlib.sha256(
                    str(identity.get("tables_sha256") or "").encode("utf-8")
                ).hexdigest(),
                "content_version": identity["data_version"],
                "content_manifest_sha1": identity.get("content_sha") or "",
                "hook_install_ok": True,
                "hooks": {
                    "game_state_load": True,
                    "game_state_step": True,
                    "native_replay_runner": True,
                    "render_gate": False,
                    "replay_json_setter": True,
                },
                "capabilities": {
                    "headless": True,
                    "native_render": False,
                    "snapshot_restore": False,
                    "structured_observation": True,
                    "two_sided_actions": True,
                },
                "content_ready": True,
                "content_assets_mapped": True,
                "production_ready": False,
            }
            native["attestation_digest"] = _content_hash(
                {
                    key: native[key]
                    for key in (
                        "abi",
                        "capabilities",
                        "content_assets_sha256",
                        "content_fingerprint_sha256",
                        "content_manifest_sha1",
                        "content_version",
                        "hook_install_ok",
                        "hooks",
                        "libg_build_id",
                        "libg_sha256",
                        "package_name",
                        "probe_sha256",
                        "protocol_version",
                        "version",
                    )
                }
            )
            self._attestation = native
            return {
                "ok": True,
                "attestation": native,
                "crforge": crforge_attestation.to_dict(),
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "mode": "headless",
                "coldReady": True,
                "ready": self._config is not None,
                "engine": "crforge",
                "protocol": PROTOCOL,
                "bridgeVersion": BRIDGE_VERSION,
                "tick": self._battle.tick() if self._battle is not None else 0,
            }

    # ---- configure -----------------------------------------------------

    def configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.start()
            if not isinstance(payload, dict):
                raise BridgeError("configure payload must be a JSON object")
            battle = payload.get("battle")
            if not isinstance(battle, dict):
                raise BridgeError("configure payload has no battle object")
            config = self._parse_config(payload, battle)
            self._reset(config)
            return {"ok": True, "sequence": self._sequence, "tick": 0, "engine": "crforge"}

    def _parse_config(self, payload: dict[str, Any], battle: dict[str, Any]) -> BattleConfig:
        decks: list[list[str]] = []
        slots: list[list[int]] = []
        card_levels: list[list[int]] = []
        tower_troops: list[str] = []
        king_levels: list[int] = []
        account_ids: list[int] = []
        for owner in (0, 1):
            deck_object = battle.get(f"deck{owner}")
            if not isinstance(deck_object, dict):
                raise BridgeError(f"configure payload has no battle.deck{owner}")
            entries = deck_object.get("sp")
            if not isinstance(entries, list) or len(entries) != 8:
                raise BridgeError(f"battle.deck{owner}.sp must be 8 cards")
            names: list[str] = []
            flags: list[int] = []
            levels: list[int] = []
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict) or "d" not in entry:
                    raise BridgeError(f"battle.deck{owner}.sp[{index}] is not a card")
                names.append(self._catalog.name(int(entry["d"])))
                flags.append(slot_flags_for_availability(int(entry.get("el", 0))))
                levels.append(_level(entry.get("l"), default=11))
            decks.append(names)
            slots.append(flags)
            card_levels.append(levels)
            support = deck_object.get("sc")
            tower_id = int(support[0]["d"]) if isinstance(support, list) and support else 159_000_000
            tower_troops.append(TOWER_SELECTIONS.get(tower_id, "King_PrincessTowers"))
            hbd = battle.get("hbd")
            kt = int(hbd[owner].get("kt", 11)) if isinstance(hbd, list) and len(hbd) > owner else 11
            king_levels.append(max(1, min(16, kt)))
            avatar = battle.get(f"avatar{owner}") or {}
            account_ids.append(
                (int(avatar.get("accountID.hi", 0)) << 32) | (int(avatar.get("accountID.lo", owner)) & 0xFFFFFFFF)
            )

        return BattleConfig(
            seed=int(payload.get("rndSeed", 1)),
            decision_ticks=1,
            level=11,
            tower_level=11,
            max_ticks=max(0, int(payload.get("endTick", 0))),
            decks=(decks[0], decks[1]),
            slots=(slots[0], slots[1]),
            tower_troop=(tower_troops[0], tower_troops[1]),
            king_levels=(king_levels[0], king_levels[1]),
            card_levels=(card_levels[0], card_levels[1]),
            account_ids=(account_ids[0], account_ids[1]),
            game_mode=int(battle.get("gamemode", 72_000_006)),
            arena=int(battle.get("arena", 54_000_001)),
            location=int(battle.get("location", 15_000_000)),
        )

    def _reset(self, config: BattleConfig) -> None:
        env_config = self._env_config_class(config.decision_ticks, config.level, config.tower_level, config.max_ticks)
        self._battle = self._live_battle_class(self._tables, self._vocabulary, env_config)
        # `LiveBattle` has no SideSetup overload yet. The deck, hand and play path
        # are exact; tower troop, king level and per-card levels stay at
        # `EnvConfig`'s values until the setup overload lands upstream.
        self._battle.reset(
            config.seed,
            self._jlist(config.decks[0]),
            self._jlist(config.decks[1]),
            self._int_array(config.slots[0]),
            self._int_array(config.slots[1]),
        )
        self._config = config
        self._generation += 1
        self._sequence = 0

    # ---- stepping ------------------------------------------------------

    def step(self, ticks: int) -> dict[str, Any]:
        with self._lock:
            self._require_battle()
            if not 1 <= int(ticks) <= 1_000_000:
                raise BridgeError("ticks must be in 1..1000000")
            stopped = False
            run_refusals: list[dict[str, Any]] = []
            for _ in range(int(ticks)):
                if self._battle.done():
                    stopped = True
                    break
                self._battle.step()
                # A 5-tick environment step crosses several decisions. Keep the
                # run refusals those decisions reported so the caller can
                # attribute a pending play that died at its run tick. Reading a
                # single header index is cheap; the full array copy is not.
                refusal_tick = int(self._battle.tick())
                for owner in (0, 1):
                    code = int(self._battle.observation(owner)[self._schema.H_LAST_RUN_REFUSAL])
                    if code:
                        run_refusals.append(
                            {
                                "owner": owner,
                                "code": code,
                                "name": (
                                    self._refusal_names[code]
                                    if 0 <= code < len(self._refusal_names)
                                    else ""
                                ),
                                "tick": refusal_tick,
                            }
                        )
            return {
                "ok": True,
                "requestedTicks": int(ticks),
                "tick": self._battle.tick(),
                "stopped": stopped,
                "mode": "headless",
                "paused": True,
                "runRefusals": run_refusals,
            }

    def pause(self) -> dict[str, Any]:
        return {"ok": True, "paused": True}

    def resume(self) -> dict[str, Any]:
        return {"ok": True, "paused": False}

    def speed(self, value: float) -> dict[str, Any]:
        return {"ok": True, "speed": float(value), "ignored": True}

    # ---- actions -------------------------------------------------------

    def inject(self, command: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._require_battle()
            if not isinstance(command, dict):
                raise BridgeError("inject command must be a JSON object")
            kind = int(command.get("ct", -1))
            body = command.get("c")
            if not isinstance(body, dict):
                raise BridgeError("inject command has no c object")
            if kind in (COMMAND_TYPE_PLAY, CMD_PLAY):
                return self._inject_play(body)
            if kind == COMMAND_TYPE_ABILITY:
                return self._inject_ability(body)
            raise BridgeError(f"inject command type {kind} is not modelled by the bridge")

    def deploy(self, owner: int, card_id: int, card_parameter: int, x: int, y: int, delay_ticks: int = 1) -> dict[str, Any]:
        with self._lock:
            self._require_battle()
            slot = self._hand_slot(int(owner), int(card_id))
            if slot is None:
                raise BridgeError(f"owner {owner}'s hand has no card {card_id}")
            return self._give_play(int(owner), slot, int(x), int(y))

    def activate_ability(self, owner: int, object_index: int, secondary_index: int, execute_in: int) -> dict[str, Any]:
        with self._lock:
            self._require_battle()
            slot = self._ability_slot(int(owner), int(secondary_index))
            if slot is None:
                raise BridgeError(f"owner {owner} has no champion slot for unit {secondary_index}")
            return self._give_ability(int(owner), slot, int(secondary_index))

    def _inject_play(self, body: dict[str, Any]) -> dict[str, Any]:
        account = (int(body.get("idHi", 0)) << 32) | (int(body.get("idLo", 0)) & 0xFFFFFFFF)
        owner = self._owner_for_account(account)
        card_id = int(body.get("sel", {}).get("os", 0))
        slot = self._hand_slot(owner, card_id)
        if slot is None:
            # The command names a card that has already cycled: refuse loudly rather
            # than play a different card.
            raise BridgeError(f"owner {owner}'s hand has no card {card_id} at tick {self._battle.tick()}")
        return self._give_play(owner, slot, int(body.get("px", 0)), int(body.get("py", 0)))

    def _give_play(self, owner: int, slot: int, x: int, y: int) -> dict[str, Any]:
        self._command_seq += 1
        name = f"crf{self._command_seq}"
        giving = self._battle.play(owner, slot, x, y, name)
        self._sequence += 1
        given = _giving(giving)
        if not given["given"]:
            # A play the client drops because the match is already decided is
            # not a bridge fault: the caller needs the terminal observation, not
            # an exception. Any other refusal stays loud except CARD_PENDING:
            # the engine already holds a play for this card, which the seat
            # header projects, so the caller can mask the slot and move on.
            if given.get("refusal") == "MATCH_OVER":
                tick = int(self._battle.tick())
                return {
                    "ok": True,
                    "tick": tick,
                    "injected": False,
                    "terminalCanceled": True,
                    "owner": owner,
                    "refusal": "MATCH_OVER",
                    "givenTick": tick,
                    "runTick": tick + 1,
                    "executeTick": tick + 1,
                }
            if given.get("refusal") == "CARD_PENDING":
                return {
                    "ok": True,
                    "tick": int(self._battle.tick()),
                    "injected": False,
                    "owner": owner,
                    **given,
                }
            raise BridgeError(f"crforge refused the play: {given}")
        return {"ok": True, "tick": self._battle.tick(), "injected": True, "owner": owner, **given}

    def _inject_ability(self, body: dict[str, Any]) -> dict[str, Any]:
        account = (int(body.get("idHi", 0)) << 32) | (int(body.get("idLo", 0)) & 0xFFFFFFFF)
        owner = self._owner_for_account(account)
        cgid = int(body.get("cgid", -1))
        slot = self._ability_slot_for_object(owner, cgid)
        if slot is None:
            raise BridgeError(f"owner {owner} has no ready champion slot naming unit {cgid}")
        return self._give_ability(owner, slot, cgid)

    def _give_ability(self, owner: int, slot: int, object_id: int) -> dict[str, Any]:
        self._command_seq += 1
        name = f"crf_ab{self._command_seq}"
        giving = self._battle.useAbility(owner, slot, object_id, name)
        self._sequence += 1
        given = _giving(giving)
        if not given["given"]:
            if given.get("refusal") == "MATCH_OVER":
                tick = int(self._battle.tick())
                return {
                    "ok": True,
                    "tick": tick,
                    "injected": False,
                    "terminalCanceled": True,
                    "owner": owner,
                    "refusal": "MATCH_OVER",
                    "givenTick": tick,
                    "runTick": tick + 1,
                    "executeTick": tick + 1,
                }
            if given.get("refusal") == "ABILITY_PENDING":
                return {
                    "ok": True,
                    "tick": int(self._battle.tick()),
                    "injected": False,
                    "owner": owner,
                    **given,
                }
            raise BridgeError(f"crforge refused the ability: {given}")
        return {"ok": True, "tick": self._battle.tick(), "injected": True, "owner": owner, **given}

    def _hand_slot(self, owner: int, card_id: int) -> int | None:
        rows = self._seat_rows(owner)
        for row in rows["hand"]:
            if int(row["cardId"]) == card_id:
                return int(row["handIndex"])
        return None

    def _ability_slot(self, owner: int, object_id: int) -> int | None:
        return self._ability_slot_for_object(owner, object_id)

    def _ability_slot_for_object(self, owner: int, object_id: int) -> int | None:
        observation = self._observation(owner)
        # The header's ability row names the unit by its seat id; the object
        # identity the model saw is the battle id, matched in `objects`.
        objects, rich, rows = entity_rows(observation, self._schema, self._vocabulary, self._catalog)
        objects, _rich = self._bind_holder_identity(objects, rich, seat=owner)
        seat_to_native = {
            row.entity_id: int(item["nativeObjectId"]) for row, item in zip(rows, objects, strict=True)
        }
        for slot in range(2):
            base = self._schema.H_ABILITY + slot * self._schema.ABILITY_INTS
            unit_id = observation[base + 1]
            if unit_id >= 0 and seat_to_native.get(unit_id) == object_id:
                return slot
        return None

    def _owner_for_account(self, account: int) -> int:
        if self._config is None:
            raise BridgeError("configure was not called")
        for owner, expected in enumerate(self._config.account_ids):
            if expected == account:
                return owner
        # FirstLight's template may use 0/1 directly.
        if account in (0, 1):
            return int(account)
        raise BridgeError(f"inject command names unknown account {account}")

    # ---- observations --------------------------------------------------

    def observe(self) -> dict[str, Any]:
        with self._lock:
            self._require_battle()
            tick, ordinary, _rich, players, _rich_players = self._capture()
            return {
                "ok": True,
                "tick": tick,
                "generation": self._generation,
                "stateEpoch": self._generation,
                "count": len(ordinary),
                "returned": len(ordinary),
                "truncated": False,
                "objects": ordinary,
                "players": players,
                "ended": bool(self._battle.done()),
                "result": _result_name(self._seat_header(0).get("result", 0)),
            }

    def observe_rich(self) -> dict[str, Any]:
        with self._lock:
            self._require_battle()
            tick, _ordinary, rich, _players, rich_players = self._capture()
            return rich_envelope(
                tick=tick,
                generation=self._generation,
                state_epoch=self._generation,
                rich_objects=rich,
                players=rich_players,
            )

    def observe_atomic(self) -> dict[str, Any]:
        with self._lock:
            self._require_battle()
            tick, ordinary, rich, players, rich_players = self._capture()
            return atomic_envelope(
                tick=tick,
                generation=self._generation,
                state_epoch=self._generation,
                objects=ordinary,
                rich_objects=rich,
                players=players,
                rich_players=rich_players,
                ended=bool(self._battle.done()),
            )

    def _capture(self):
        observation = self._observation(0)
        rows, header = self._decode(0, observation)
        objects, rich, _rows = entity_rows(observation, self._schema, self._vocabulary, self._catalog)
        objects, rich = self._bind_holder_identity(objects, rich)
        players = []
        for owner in (0, 1):
            own = observation if owner == 0 else self._observation(owner)
            facts = player_facts(own, self._schema, self._vocabulary, self._catalog, owner=owner)
            if self._config is not None:
                facts["accountId"] = int(self._config.account_ids[owner])
            players.append({**facts, "ended": bool(self._battle.done())})
        # Rich players are the minimal, honest runtime: no champion/evolution
        # claim until the bridge reads those from crforge.
        rich_players = [empty_player(owner) for owner in (0, 1)]
        return header["tick"], objects, rich, players, rich_players

    def _decode(self, owner: int, observation: list[int]):
        from .observe import decode_rows

        return decode_rows(observation, self._schema, self._vocabulary, self._catalog)

    def _seat_rows(self, owner: int) -> dict[str, Any]:
        observation = self._observation(owner)
        _rows, header = self._decode(owner, observation)
        return player_facts(observation, self._schema, self._vocabulary, self._catalog, owner=owner)

    def _seat_header(self, owner: int) -> dict[str, int]:
        observation = self._observation(owner)
        _rows, header = self._decode(owner, observation)
        return header

    def _bind_holder_identity(self, objects: list[dict[str, Any]], rich: list[dict[str, Any]], *, seat: int = 0):
        """Use the battle's own entity ids where a seat row can be matched to one.

        A seat row's id is local to the seat. crforge's ability commands name the
        battle id, and the native probe's ``cgid`` is the object id the model saw,
        so the two must be the same number. Matching by (side, row, position) is
        exact for live entities; unmatched rows (projectiles, areas) keep a
        stable synthetic id that no ability can name.

        The observation is in the seat's frame: side 0's x is mirrored and side
        1's y is mirrored. Convert rows back to game coordinates before matching
        the live holder.
        """

        if self._battle is None:
            return objects, rich
        bucket: dict[tuple[int, str, int, int], list[Any]] = {}
        try:
            entities = self._battle.battle().getBattle().getHolder().entities()
        except Exception:
            return objects, rich
        for entity in entities:
            try:
                if int(entity.getKind()) != 5:  # BattleEntity.KIND_CHARACTER
                    continue
                name = str(entity.getData().name())
                side = int(entity.side())
                x = int(entity.getView().getX())
                y = int(entity.getView().getY())
            except Exception:
                continue
            bucket.setdefault((side, name, x, y), []).append(entity)

        # First pass: choose the matched entity for each row and rewrite identity.
        matched: dict[int, Any] = {}
        for item in objects:
            side = int(item["owner"]) if int(seat) == 0 else 1 - int(item["owner"])
            x = int(self._schema.toSeatX(int(seat), int(item["x"])))
            y = int(self._schema.toSeatY(int(seat), int(item["y"])))
            key = (side, _row_name(item), x, y)
            candidates = bucket.get(key)
            if candidates:
                entity = candidates.pop(0)
                matched[int(item["slot"])] = entity
                item["nativeObjectId"] = int(entity.getId())
                item["entityKey"] = [int(item["owner"]), -2, int(entity.getId())]
                try:
                    hp = entity.getHitPoints()
                    if hp is not None:
                        item["hp"] = int(hp.getHitPoints())
                        item["maxHp"] = int(hp.getMaximum())
                        item["shield"] = (
                            {"current": int(hp.getShield()), "max": int(hp.getShieldMaximum()), "status": "authoritative"}
                            if int(hp.getShield()) > 0
                            else None
                        )
                    item["level"] = int(entity.level())
                except Exception:
                    pass
                try:
                    # The policy's archetype catalog is keyed by the game's
                    # LogicData global id (e.g. Musketeer=34000014), not by the
                    # FirstLight card id. They are different namespaces.
                    global_id = int(entity.getData().globalId())
                    if 0 < global_id <= 0xFFFFFFFF:
                        item["dataGlobalId"] = global_id
                except Exception:
                    pass
        # Second pass: targets, now that every row has its native id.
        by_slot = {int(item["slot"]): item for item in objects}
        for item in objects:
            entity = matched.get(int(item["slot"]))
            target_key = None
            if entity is not None:
                try:
                    reference = entity.getTargeting().getReference()
                    if reference is not None:
                        target_key = None
                        for other in objects:
                            if int(other["nativeObjectId"]) == int(reference.id()):
                                target_key = [int(other["owner"]), -2, int(reference.id())]
                                break
                except Exception:
                    target_key = None
            item["targetEntityKey"] = target_key
            item["targetEntityValidated"] = target_key is not None
        for item, rich_item in zip(objects, rich, strict=True):
            for key in (
                "nativeObjectId",
                "entityKey",
                "dataGlobalId",
                "hp",
                "maxHp",
                "shield",
                "targetEntityKey",
                "targetEntityValidated",
            ):
                if key in item:
                    rich_item[key] = item[key]
        return objects, rich

    # ---- guards --------------------------------------------------------

    def _require_battle(self) -> None:
        self.start()
        if self._battle is None:
            raise BridgeError("configure was not called")


def _row_name(item: dict[str, Any]) -> str:
    return str(item.get("rowName", ""))


def _level(value: object, *, default: int) -> int:
    if value is None:
        return default
    try:
        return max(1, min(16, int(value) + 1))
    except (TypeError, ValueError):
        return default


def _giving(giving) -> dict[str, Any]:
    """Read what crforge's command returned, or fail loudly.

    This used to swallow every Java failure and pretend the play was given.
    That turned a real refusal into a phantom pending record with
    ``executeTick = requested + 1``, which the policy then replayed forever.
    """

    try:
        run_tick = int(giving.runTick())
        return {
            "given": bool(giving.given()),
            "refusal": str(giving.refusal()),
            "code": int(giving.code()),
            "givenTick": int(giving.givenTick()),
            "runTick": run_tick,
            # crforge runs the command on `runTick`; the state a caller observes
            # after that step is `runTick + 1`.
            "executeTick": run_tick + 1,
        }
    except Exception as error:  # noqa: BLE001 - Java exceptions are opaque here
        raise BridgeError(f"crforge giving could not be read: {type(error).__name__}: {error}") from error


def _result_name(value: int) -> str:
    return {0: "running", 1: "won", 2: "lost", 3: "draw"}.get(int(value), "unknown")


def _content_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lock_commit() -> str | None:
    return _lock_value("commit")


def _lock_compatible() -> list[str]:
    value = _lock_value("compatible")
    return value.split() if value else []


def _lock_value(key: str) -> str | None:
    path = Path(__file__).resolve().parents[1] / ".resource" / "crforge" / "crforge-data.lock"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None
