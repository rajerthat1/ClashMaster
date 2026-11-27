# Competitive data releases

A V4 checkpoint is bound to the exact catalogs it was trained on. The repo used
to hard-pin a single 15.535 release, which made the 16.635 baselines
unloadable. Releases are now selectable: the 15.535 default and the 16.635.1
line coexist, each with its own verified data and identity pins.

## How it works

| Piece | Location |
| --- | --- |
| Release descriptors | `native_runner/releases/<name>.json` |
| Compiled release data | `native_runner/data/competitive-16.635/` (16.635.1) |
| Default release | `native_runner/data/competitive/` (15.535, pinned in code) |
| Selector | `native_runner/competitive_release.py` (`contextvars`) |
| Bundle builder | `factory.production_semantic_bundle(release)` |

The default release is `None`, i.e. the historical module constants and pinned
manifest — existing behaviour and tests are unchanged. A named release adds:

- `data_root` and `manifest_sha256` — the compiled files are hashed and
  verified exactly like the default; a new catalog line never disables a check;
- `identity` — source release, card-partition ids, counts, catalog/source-file
  hashes, form-evidence pin and compatibility ids.

`load_policy_v4()` reads the checkpoint's `contract`, picks the release whose
built catalog ids equal `contract.catalog_ids`, builds `ModelConfigV4` from
`contract.model_config`, and loads strictly. A checkpoint that matches no known
release fails closed.

## 16.635.1 release

Descriptor: `native_runner/releases/16.635.1.json`
Data: `native_runner/data/competitive-16.635/`

Provenance: compiled catalogs and reviewed facts adopted from
`.resource/RoyaleFish/data_16635` (Apache-2.0), verified against the published
`B-general-v275` contract. The adopted bundle needed three repairs to become
self-consistent; none of them changes catalog semantics:

1. `model_catalogs.input_catalog_ids` named hashes that did not exist in the
   shipped `card_logic`/`effects`/`projectiles`; rewritten to the actual ids.
2. `form_evidence` carried 15.535 `card_specs_hash`/`ability_specs_hash` and a
   15.535 source-file hash; rewritten to the 16.635 values.
3. Its `manifest.json` hashed files it did not describe; regenerated.

Resulting catalog ids (equal to `B-general-v275`'s contract):

```
card              8c0baac97613adee70b0945b4aefb491ecd9a798e94aa3199056d6c3933f7d5d
ability           128b6679df4663b12e5a1d658d866db85ee71fc22b0b830ed86467fc44568f9c
entity_archetype  140e32c1ab59443983b6f8662e401e2c992e08d5e0581a9b4bb4b0ae8b2d1478
effect            a5bf4db42bc953ce8ccd39b802e03973dcfd17bd4b948540651ef59752cd79eb
mechanic_profile  00b73f61309bfcc293c6b30ce1e72b6f969d94aad2eea0021918125c913f4eab
```

The checkpoint's model config also selects the 16.x observation schema
(`card_runtime_feature_dim: 21`, with the five pending-execution columns),
LSTM 768, spatial 128/5, 453 archetypes and 26 abilities.

## First native play smoke

`evaluate.py --sample` on the A51 (`R58NA0QTHWV`), seed 11, one game, both seats
alternating: **B-general-v275 (A) vs General-460 (B)**. Both checkpoints loaded
through the normal selector in one process (each with its own catalog bundle and
observation schema).

| Field | Result |
| --- | --- |
| Terminal | `native_final_result`, tick 6089 (overtime) |
| Actions | 56 (A) / 55 (B) |
| Rejected actions | **0** |
| Winner | B (General) |
| Crash buffer | empty |

One game is a plumbing gate, not a strength result. The 17.5M model runs end to
end on the native 16.635.1 engine with the 21-column schema, so the loader,
tensorizer pending columns and 16.635 catalogs are wired correctly. A paired
gate (both seats, ≥150 games, Wilson intervals) is step 3.

## Adding a release

Two paths, both producing a descriptor:

- **From source assets** — `python -m native_runner.resource_compiler
  --workspace <workspace> --output <dir> --emit-release`. The workspace must
  contain `<release>/assets/csv_logic`. This regenerates everything and emits a
  correct manifest. Note: local `Clashback` assets are an older 16.635 content
  revision than the published models, so they do not reproduce those catalog
  ids.
- **From a compiled bundle** — `python tools/native_analysis/adopt_compiled_release.py`
  with `--expect-checkpoint` to refuse adoption unless the catalog ids match a
  published model. Use this when the compiled facts are licensed and
  version-matched but their manifest is stale.

Both paths verify the result through the normal loader afterwards. Do not adopt
a bundle by disabling `competitive_data` checks; fail-closed verification is the
point of the pin.

## Schema profiles

`ModelConfigV4.card_runtime_feature_dim` selects the observation schema:

| Width | Profile | Used by |
| --- | --- | --- |
| 16 | `CARD_RUNTIME_FEATURE_NAMES` | 15.535 checkpoints (General, IL, Hog26) |
| 21 | `CARD_RUNTIME_FEATURE_NAMES_V2` | 16.635 checkpoints (`B-general-v275`) |

The tensorizer emits the pending-execution columns only for the 21-column
profile. `neutralize_pending_ticks_remaining` is honoured when set by the
checkpoint's contract. `delay_aware_legality` is also honoured: when the
checkpoint declares it, the decoder, the environment action mask and the
compound validator all check elixir at the touch delay bin (see
`docs/native/16.635.1/FINDINGS.md` §3.39). The remaining 16.x switches
(`ablate_*`, `card_id_gate`) are carried through the contract for exact
round-tripping; this tree does not branch on them yet.
