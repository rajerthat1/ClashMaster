# Live capture format (16.635) — optional crforge conversion

Status: **draft, 2026-11-12**. This is the contract between the live reader
(`ROADMAP.md` Phase 1) and the trainer (`ROADMAP.md` Phase 3).
It exists so the reader and the converter can be built against one schema, and so a capture is
rejected loudly when it cannot become a 16.635 replay.

The capture is the primary artifact for native training, behavior cloning, and validation. The
crforge replay conversion is **optional**, for the fallback/CI path in
[`ROADMAP_CRFORGE.md`](../ROADMAP_CRFORGE.md). Native training does not need the conversion.

A capture has **two artifacts**:

| Artifact | Written by | Consumed by |
| --- | --- | --- |
| `<battle>.crfcapture.jsonl.zst` | live reader (Track B) | converter, frame-parity check |
| `<battle>.crforge.replay.json` | converter (no game access) | `crforge_env.label_replays` |

The capture is the ground truth. The replay is a derived artifact; it is never edited by hand.

---

## 1. Capture file

One file per battle, zstd-compressed JSON Lines. Line 1 is the header; every later line is one
frame, in tick order. Blank lines are not written.

### 1.1 Header (line 1)

```json
{
  "type": "crfcapture.header.v1",
  "client_version": "16.635.1",
  "data_version": "16.635.8",
  "content_sha": "062a11e0500c03cc041f123413b1ef1846b539a4",
  "captured_at": "2026-11-12T18:04:11Z",
  "libg_sha256": "<64 hex>",
  "reader_build": "<git sha>",
  "seed_hint": 1,
  "location": 15000000,
  "arena": 54000001,
  "game_mode": 72000006,
  "players": [
    {
      "owner": 0,
      "name": "Side 0",
      "account_id": 0,
      "tower_troop": "TowerPrincess",
      "king_level": 15,
      "deck": [
        {"slot": 0, "card_id": 26000021, "level": 15, "form": "base"},
        {"slot": 1, "card_id": 28000001, "level": 15, "form": "base"},
        {"slot": 2, "card_id": 26000014, "level": 15, "form": "base"},
        {"slot": 3, "card_id": 26000010, "level": 15, "form": "base"},
        {"slot": 4, "card_id": 26000000, "level": 15, "form": "base"},
        {"slot": 5, "card_id": 26000002, "level": 15, "form": "base"},
        {"slot": 6, "card_id": 28000000, "level": 15, "form": "base"},
        {"slot": 7, "card_id": 26000012, "level": 15, "form": "base"}
      ]
    },
    {"owner": 1, "...": "same shape"}
  ]
}
```

Rules:

- `owner` is the **engine owner**, the same owner used in `ObservationV1`. Side 0 is the client's
  own player only if the reader says so; the converter must never infer sides from screen position.
- `card_id` is the FirstLight/native global id (`26xxxxxx` spells, `28xxxxxx` support, etc.).
  `form` is `base`, `evolution`, or `hero`. The slot is the deck slot (0–7), not the hand slot.
- `level` is the native level, counted from 1.
- `content_sha`/`data_version` come from the runner's `attest`, never from the filename.

### 1.2 Frame

```json
{
  "type": "crfcapture.frame.v1",
  "tick": 1234,
  "players": [
    {
      "owner": 0,
      "elixir_raw": 63420,
      "hand": [
        {"hand_index": 0, "card_id": 26000021, "card_parameter": 814753794, "cost": 4},
        {"hand_index": 1, "card_id": 28000001, "card_parameter": -1, "cost": 3}
      ],
      "next": {"card_id": 26000014, "card_parameter": -1}
    },
    {"owner": 1, "elixir_raw": null, "hand": []}
  ],
  "towers": [
    {"owner": 0, "kind": "king", "x": 9000, "y": 3000, "hp": 4823, "max_hp": 4823},
    {"owner": 0, "kind": "princess_left", "x": 3500, "y": 6500, "hp": 2534, "max_hp": 2534}
  ],
  "entities": [
    {
      "native_object_id": 41,
      "owner": 1,
      "card_id": 26000021,
      "form": "base",
      "kind": "character",
      "x": 3500, "y": 22499,
      "hp": 1696, "max_hp": 1696,
      "target_native_object_id": 3,
      "deploy_ms": 0
    }
  ],
  "result": "running"
}
```

- `elixir_raw` is ten-thousandths, exactly as `ObservationV1` reads it. Owner 1's elixir is
  `null` when the reader cannot see it; the converter uses the replay's own promised-elixir rule,
  never a guess from the frame.
- `hand` is the player's private hand. The converter requires one frame with a full 4-card hand
  before the first play of that player; otherwise the replay is refused.
- Entity coordinates are in the **battle frame** (side 0's frame; `y` grows from side 0 to side 1),
  in game units, 1000 per tile. The reader is responsible for un-mirroring the seat's screen.
- `native_object_id` must be stable for the life of the entity. It is the replay's `cgid` for
  abilities and the target link in later analysis.
- `result`: `running`, `won`, `lost`, `draw`, or `aborted`.

### 1.3 Play record

Plays are written as their own records, in the tick they were **given**, not the tick they ran:

```json
{
  "type": "crfcapture.play.v1",
  "tick": 169,
  "owner": 0,
  "hand_index": 0,
  "card_id": 26000021,
  "card_parameter": 814753794,
  "x": 3500,
  "y": 9500,
  "kind": "card"
}
```

For an ability:

```json
{
  "type": "crfcapture.play.v1",
  "tick": 189,
  "owner": 0,
  "kind": "ability",
  "target_native_object_id": 5000006
}
```

A record whose `tick` is missing, or whose card was not in that owner's hand at that tick, is a
capture fault and stops the conversion.

---

## 2. Converter: capture → crforge replay

The output is the document `crforge_env.label_replays` and the desktop replay viewer read:

```json
{
  "rndSeed": 1,
  "time": 0,
  "endTick": 3051,
  "battle": {"...": "..."},
  "cmd": [{"ct": 153, "c": {"t": 169, "t2": 189, "idHi": 0, "idLo": 0,
                             "px": 3500, "py": 9500, "sid": -1,
                             "sel": {"os": 26000021, "pd": 814753794}}}],
  "evt": [],
  "srq": [],
  "srs": [],
  "capture": {
    "client_version": "16.635.1",
    "content_version": "16.635.8",
    "content_sha": "062a11e0500c03cc041f123413b1ef1846b539a4",
    "captured_at": "2026-11-12T18:04:11Z"
  }
}
```

`capture.content_sha` must equal the tables' `build.properties` `content_sha`; crforge refuses
the replay otherwise (`unsupported: a replay recorded on client …, read against the game tables
of data version …`). This is the guard that keeps a 15.535 capture out of a 16.635 corpus.

### 2.1 `battle`

`battle.deck0`/`deck1.sp` are the deck in deck-slot order:

```json
{"d": 26000021, "l": 15, "el": 1}
```

`el` is the form availability mask used by the native template: `1` evolution, `2` hero, `3` both.
It is **availability**, not the form actually played. A base-only card omits `el`.

`battle.deckN.sc[0].d` is the tower troop id (`159000000` Princess, `159000001` Cannoneer,
`159000002` Dagger Duchess, `159000004` Royal Chef). `battle.hbd[i].kt` is the king level.

### 2.2 Coordinates and sides

The play record's `x`/`y` are copied to `px`/`py`. `sid` stays `-1`; the side is `idHi`/`idLo`
(the player's account id) exactly as in a client replay. If the capture has no account id, the
converter writes the synthetic ids `0` and `1` and records that in the capture block.

### 2.3 Timing

For client 16.635.1 the command types are:

| `ct` | Command | Fields |
| ---: | --- | --- |
| 153 | card play | `t`, `t2`, `idHi`, `idLo`, `px`, `py`, `sid`, `sel` |
| 189 | ability | `t`, `t2`, `idHi`, `idLo`, `cgid` |

`t` is the tick the command was given. `t2 = t + 20` is the command-age boundary; crforge runs
the play from that boundary. The converter writes `t` from the capture record and `t2 = t + 20`;
it never infers the run tick from the observation.

### 2.4 Packed card item (`pd`)

`sel.pd` is the client's packed word. crforge checks the fields the deck decides and the fields
the battle decides:

| Bits | Width | Field | Converter value |
| ---: | ---: | --- | --- |
| 0..3 | 4 | evolution field | `1` evolved, `2` hero, `0` base |
| 4..6 | 3 | option + 1 | `1` for a card with no option; variant cards use the recorded option + 1 |
| 7..9 | 3 | evolution count + 1 | `1` unless the form is an evolution slot |
| 10..16 | 7 | level | native level, counted from 1 |
| 17..18 | 2 | cosmetic | `0` |
| 19..21 | 3 | deck slot flags | `1` evolution slot, `2` hero slot, `3` both, `0` neither |
| 22..27 | 6 | deck index + 1 | the deck slot 0–7 plus 1 |
| 28..31 | 4 | cost | whole elixir cost |

If the reader supplies `card_parameter`, the converter must **verify** it against the formula
above and against the card in the frame's hand, and refuse the play when the two disagree. It
must not trust a `pd` from the capture blindly: a wrong `pd` is a silently wrong label.

### 2.5 Card names

crforge identifies cards by table row name, not by native global id. The converter needs the
generated `native_runner/data/competitive/crforge_catalog.json` (see
`ROADMAP_CRFORGE.md` §3):

```json
{
  "cards": {"26000021": "HogRider", "...": "..."},
  "forms": {"26000021:evolution": "HogRider_Evolved", "...": "..."},
  "unmapped": []
}
```

The converter refuses to write a replay while `unmapped` is non-empty **only** for a card the
capture uses. An unmapped card that no captured deck uses is a warning, not a failure.

---

## 3. Validation

Run in this order; stop at the first failure.

1. **Schema**: header and every frame/play record validate against `crfcapture.*.v1`; ticks are
   strictly increasing; `native_object_id` is unique and stable.
2. **Content**: `capture.content_sha` equals the tables' `content_sha`.
3. **Label**: `label_replays(..., check=True)` on the converted replay. Pass conditions:
   - `counts.refusal is None`;
   - `counts.inexpressible == 0` for the player the capture followed;
   - `counts.collisions` is 0 at decision ticks that were actually sampled (a reader at 20 Hz
     should never give two commands in one 5-tick window);
   - `counts.label_masked == 0` unless the capture shows a genuinely illegal tap.
4. **Drive** (optional, for hand-played captures): `drive.state_reproduced` may be `false` for a
   real battle because the capture does not observe every command (a card played while the
   reader was disconnected). It must be `true` for a capture that records every play.
5. **Parity** (M2): 50 frames from the capture and the crforge battle at the same tick agree on
   entity count, entity ids, positions, tower HP, hand, elixir, and result. This is the check
   that catches a lane flip or a side swap.

A capture that fails step 3 is not "mostly good"; it is unusable and must be quarantined with the
refusal text. Do not patch the replay to make it pass.

---

## 4. Open questions for the reader

These are the facts the reader must establish before the converter can be finished:

- Which seat is `owner 0` on the screen, and where the un-mirroring rule lives.
- Whether the hand's `hand_index` order is the screen order or the queue order.
- Whether `card_parameter` is readable for each form (base/evolution/hero) and what it means for
  a variant card.
- How the reader knows a play was **refused** (the tap produced nothing) so it is not written as
  a play.
- Whether the reader can see both hands (for capture) or only its own (for live play). The
  converter only needs the playing side's hand; the other side's hand is not a replay input.
- The account ids the client uses, or a decision to use synthetic ids `0`/`1`.

Until these are answered, the converter can be built and tested on the crforge example replay
(`.resource/crforge/examples/replays/ladder.json`) and on hand-written captures, but not on a
live Null's capture.

---

## 5. CLI shape

```
python -m crforge_bridge.capture convert \
  --capture runs/captures/2026-11-12/battle-0001.crfcapture.jsonl.zst \
  --tables ~/.crforge/tables/16.635.8 \
  --catalog native_runner/data/competitive/crforge_catalog.json \
  --out runs/replays/16.635/battle-0001.crforge.replay.json
```

The converter is deterministic: the same capture and the same catalog produce byte-identical
JSON. It never touches the network and never reads the game.
