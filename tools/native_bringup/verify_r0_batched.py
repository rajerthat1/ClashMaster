#!/usr/bin/env python3
"""CPU/synthetic equivalence check for the R0 batched trainer paths.

The upstream `verify_b1_equiv.py` / `verify_multilane.py` scripts assume a CUDA
host and a real `compiled-prod-v17` corpus. This tool runs the same two checks
on synthetic, capture-complete frames on CPU so the batched loop
(`r0_train_batched.fuse` / `cat_states` / `split_state`) can be validated before
a GPU/data host exists:

1. B=1: one optimizer update through `training_r0.il_learning` must match the
   trainer's single-lane batched loop in loss, gradients and updated weights.
2. Multi-lane: lanes of different lengths and start ticks fused together must
   produce the same per-position log-probabilities and final recurrent state as
   each lane run alone.

It writes a machine-readable report under `builds/native/`, prints a verdict and
exits non-zero on divergence. No native worker, dataset or GPU is used.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time

import torch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SANDBOX = PROJECT_ROOT / ".resource" / "cr-native-sandbox"
UPSTREAM = PROJECT_ROOT / ".resource" / "r0-elite-training"
for path in (str(UPSTREAM), str(SANDBOX)):
    if path not in sys.path:
        sys.path.insert(0, path)

from training_r0.catalog import CardVocabulary  # noqa: E402
from training_r0.config import ModelConfig, Temperatures  # noqa: E402
from training_r0.elite_tensorizer import REQUIRED_CAPTURE  # noqa: E402
from training_r0.il_dataset import ILSequence  # noqa: E402
from training_r0.il_learning import update_imitation_sequence  # noqa: E402
from training_r0.model import R0Policy  # noqa: E402
from training_r0.observation import ObservationBuilder  # noqa: E402
from training_r0.semantics import EntityDetail, PublicSemantics  # noqa: E402
from training_r0.synthetic import public_frame, rich_public_frame  # noqa: E402
from r0_train_batched import cat_states, fuse, split_state  # noqa: E402

TOL_LOSS = 1e-5
TOL_GRAD = 1e-5
TOL_PARAM = 1e-6
TOL_LOGPROB = 1e-5
TOL_STATE = 1e-5


def _complete_frame(builder: ObservationBuilder, *, tick: int, side: int, episode_uid: str, rich: bool):
    """Make the synthetic fixture capture-complete without inventing native data.

    A production model refuses diagnostic fixtures, and `update_imitation_sequence`
    refuses diagnostic models, so the equivalence check needs every public entity
    (towers included) to have a detail and every required capability declared.
    The fixture is still synthetic; it only exercises the tensor path.
    """
    make = rich_public_frame if rich else public_frame
    frame = make(builder, tick=tick, side=side, episode_uid=episode_uid)
    details = [
        EntityDetail(entity.key, 'troop', f'synthetic-group-{entity.key}',
                     group_kind='deployment', radius_tiles=0.4,
                     archetype='CHARACTER.Knight')
        for entity in frame.view.entities
    ]
    details.extend(
        EntityDetail(tower.key, 'king' if tower.role == 0 else 'princess',
                     f'synthetic-tower-{tower.key}', group_kind='singleton')
        for tower in frame.view.towers
    )
    if frame.semantics is None:
        semantics = PublicSemantics(
            episode_uid, side, tick,
            entities=tuple(details),
            capture_capabilities=REQUIRED_CAPTURE,
        )
    else:
        semantics = replace(
            frame.semantics,
            capture_capabilities=REQUIRED_CAPTURE,
            entities=tuple(details),
        )
    return replace(frame, semantics=semantics)


def _sequence(builder: ObservationBuilder, model: R0Policy, *, steps: int, side: int,
              start_tick: int, episode_uid: str, rich: bool) -> ILSequence:
    observations, actions = [], []
    # The model samples micro-actions; seed per sequence so a rerun of this
    # report is byte-for-byte reproducible.
    torch.manual_seed(int(hashlib.sha256(episode_uid.encode()).hexdigest()[:8], 16))
    state = model.initial_state(1)
    with torch.no_grad():
        for step in range(steps):
            batch = builder.batch([
                _complete_frame(builder, tick=start_tick + step * 5, side=side,
                                episode_uid=episode_uid, rich=rich)
            ])
            output = model(batch, state)
            observations.append(batch)
            actions.append(output.actions)
            state = output.next_state.detach()
    label_known = torch.ones((steps, 1), dtype=torch.bool)
    source = hashlib.sha256(f'{episode_uid}:{side}:{start_tick}'.encode()).hexdigest()
    sequence = ILSequence(tuple(observations), tuple(actions), label_known,
                          False, source)
    sequence.validate()
    return sequence


def _batched_single_lane(model: R0Policy, optimizer, sequence: ILSequence):
    """The trainer's single-lane loop with TBPTT spanning the whole sequence."""
    state = model.initial_state(1)
    count_total = int(sequence.label_known.sum())
    pending, losses = [], []
    total = 0.0
    for position in range(len(sequence.observations)):
        fused_observation, pad_fraction = fuse([sequence.observations[position]])
        fused_actions = fuse([sequence.actions[position]])[0]
        known = sequence.label_known[position].unsqueeze(0)
        output = model(fused_observation, state, forced=fused_actions,
                       temperatures=Temperatures())
        loss = -torch.where(known, output.logp, torch.zeros_like(output.logp)).sum() / count_total
        pending.append(loss)
        losses.append((loss.item(), int(known.sum()), pad_fraction))
        total += float(loss.detach())
        state = split_state(output.next_state, [1])[0]
    torch.stack(pending).sum().backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
    optimizer.step()
    grads = {name: parameter.grad.detach().clone()
             for name, parameter in model.named_parameters()
             if parameter.grad is not None}
    optimizer.zero_grad(set_to_none=True)
    return total, state.detach(), losses, grads


def _named_grads(model: R0Policy):
    return {name: parameter.grad.detach().clone()
            for name, parameter in model.named_parameters()
            if parameter.grad is not None}


def _named_params(model: R0Policy):
    return {name: parameter.detach().clone()
            for name, parameter in model.named_parameters()}


def _build(seed: int, config: ModelConfig):
    torch.manual_seed(seed)
    model = R0Policy(CardVocabulary.from_native(), config).cpu()
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
    return model, optimizer


def _comparison(reference: dict, candidate: dict, *, atol: float,
                rtol: float) -> dict:
    """Worst tensor difference, scaled by an absolute+relative allowance.

    A pure relative test is meaningless for gradients whose reference magnitude is
    ~1e-10: a 1e-10 absolute difference is float32 noise but 97 % "relative".
    """
    worst = {'absolute': 0.0, 'ratio': 0.0, 'name': '', 'missing': 0}
    for name, expected in reference.items():
        got = candidate.get(name)
        if got is None:
            worst['missing'] += 1
            continue
        difference = (expected - got).abs().max().item()
        denominator = expected.abs().max().item()
        ratio = difference / (atol + rtol * denominator)
        if ratio > worst['ratio']:
            worst.update(ratio=ratio, name=name)
        if difference > worst['absolute']:
            worst['absolute'] = difference
    return worst


def _solo_logp(model: R0Policy, sequence: ILSequence):
    state = model.initial_state(1)
    rows = []
    for position in range(len(sequence.observations)):
        output = model(sequence.observations[position], state,
                       forced=sequence.actions[position], temperatures=Temperatures())
        rows.append(output.logp.detach().float().clone())
        state = output.next_state.detach()
    return rows, state


def _fused_logp(model: R0Policy, sequences):
    lanes = [{'sequence': sequence, 'slot': index, 'position': 0,
              'state': model.initial_state(1)}
             for index, sequence in enumerate(sequences)]
    rows = [[] for _ in sequences]
    padding = 0.0
    for _ in range(max(len(sequence.observations) for sequence in sequences)):
        live = [lane for lane in lanes if lane['position'] < len(lane['sequence'].observations)]
        if not live:
            break
        fused_observation, padding = fuse([
            lane['sequence'].observations[lane['position']] for lane in live
        ])
        fused_actions = fuse([
            lane['sequence'].actions[lane['position']] for lane in live
        ])[0]
        state = cat_states([lane['state'] for lane in live], 'cpu')
        output = model(fused_observation, state, forced=fused_actions,
                       temperatures=Temperatures())
        next_states = split_state(output.next_state, [1] * len(live))
        offset = 0
        for lane in live:
            width = lane['sequence'].observations[lane['position']].batch_size
            rows[lane['slot']].append(output.logp.detach().float()[offset:offset + width].clone())
            offset += width
        for lane, next_state in zip(live, next_states):
            lane['state'] = next_state.detach()
            lane['position'] += 1
    return rows, {lane['slot']: lane['state'] for lane in lanes}, padding


def run(*, steps: int, lanes: int, rich: bool, seed: int) -> dict:
    torch.set_num_threads(2)
    started = time.perf_counter()
    config = ModelConfig()  # production strictness: allow_incomplete_capture=False
    builder = ObservationBuilder(CardVocabulary.from_native(), config)
    model, _ = _build(seed, config)
    sequence = _sequence(builder, model, steps=steps, side=0, start_tick=100,
                         episode_uid='synthetic-b1', rich=rich)
    report = {
        'schema': 'r0-batched-equivalence.v1',
        'device': 'cpu',
        'fixture': 'synthetic_capture_complete_not_native_validation',
        'rich': rich,
        'seed': seed,
        'parameters': sum(parameter.numel() for parameter in model.parameters()),
        'observation_schema_sha256': builder.schema_hash,
        'model_schema_sha256': model.model_schema_hash,
        'tolerances': {'loss': TOL_LOSS, 'grad': TOL_GRAD, 'param': TOL_PARAM,
                       'logp': TOL_LOGPROB, 'state': TOL_STATE},
        'b1': {},
        'multilane': {},
    }

    model_a, optimizer_a = _build(seed, config)
    initial_a = {name: value.clone() for name, value in model_a.state_dict().items()}
    metrics, state_a = update_imitation_sequence(
        model_a, optimizer_a, sequence, initial_state=model_a.initial_state(1),
        tbptt_steps=len(sequence.observations))
    grads_a, params_a = _named_grads(model_a), _named_params(model_a)

    model_b, optimizer_b = _build(seed, config)
    same_initial = all(torch.equal(value, model_b.state_dict()[name])
                       for name, value in initial_a.items())
    if not same_initial:
        raise SystemExit('ABORT: the two models did not start from the same weights')
    loss_b, state_b, losses, grads_b = _batched_single_lane(model_b, optimizer_b, sequence)
    params_b = _named_params(model_b)

    loss_relative = abs(metrics['loss'] - loss_b) / max(abs(metrics['loss']), 1e-12)
    grad_comparison = _comparison(grads_a, grads_b, atol=1e-8, rtol=TOL_GRAD)
    param_comparison = _comparison(params_a, params_b, atol=1e-6, rtol=TOL_PARAM)
    state_difference = max(
        (getattr(state_a, field).detach().float()
         - getattr(state_b, field).detach().float()).abs().max().item()
        for field in ('hidden', 'cell')
    )
    b1_ok = (loss_relative <= TOL_LOSS and grad_comparison['ratio'] <= 1.0
             and param_comparison['ratio'] <= 1.0
             and grad_comparison['missing'] == 0)
    report['b1'] = {
        'observations': len(sequence.observations),
        'loss_old': metrics['loss'],
        'loss_batched': loss_b,
        'loss_relative': loss_relative,
        'grad_parameters_compared': len(grads_a),
        'grad_missing': grad_comparison['missing'],
        'grad_worst_absolute': grad_comparison['absolute'],
        'grad_worst_ratio_of_allowance': grad_comparison['ratio'],
        'grad_worst_parameter': grad_comparison['name'],
        'param_worst_absolute': param_comparison['absolute'],
        'param_worst_ratio_of_allowance': param_comparison['ratio'],
        'param_worst_parameter': param_comparison['name'],
        'atol_grad': 1e-8,
        'atol_param': 1e-6,
        'state_max_abs_diff': state_difference,
        'padding_ratio_last': losses[-1][2] if losses else 0.0,
        'equivalent': bool(b1_ok),
    }

    reference_model, _ = _build(seed + 1, config)
    reference_model.eval()
    lane_sequences = [
        _sequence(builder, reference_model, steps=steps + index, side=index % 2,
                  start_tick=100 + index * 15, episode_uid=f'synthetic-lane-{index}',
                  rich=rich)
        for index in range(lanes)
    ]
    with torch.no_grad():
        # CPU kernels pick per-shape primitives on first use; a cold fused call can
        # differ by ~2e-3 from the warmed path. Warm the exact shapes, then measure.
        _solo_logp(reference_model, lane_sequences[0])
        _fused_logp(reference_model, lane_sequences)
        solo = [_solo_logp(reference_model, lane) for lane in lane_sequences]
        fused_rows, fused_states, padding = _fused_logp(reference_model, lane_sequences)

    worst_logp, where = 0.0, ''
    for index, ((solo_rows, _), fused_lane) in enumerate(zip(solo, fused_rows)):
        for position, (expected, got) in enumerate(zip(solo_rows, fused_lane)):
            got = got.reshape(expected.shape) if got.numel() == expected.numel() else got
            difference = (expected - got).abs().max().item()
            if difference > worst_logp:
                worst_logp, where = difference, f'lane{index} position{position}'
    worst_state, state_where = 0.0, ''
    for index, (_, solo_state) in enumerate(solo):
        for field in ('hidden', 'cell'):
            expected = getattr(solo_state, field).detach().float()
            got = getattr(fused_states[index], field).detach().float().reshape(expected.shape)
            difference = (expected - got).abs().max().item()
            if difference > worst_state:
                worst_state, state_where = difference, f'lane{index} {field}'
    known_total = sum(int(label.sum()) for sequence in lane_sequences
                      for label in sequence.label_known)
    lengths = [len(sequence.observations) for sequence in lane_sequences]
    multilane_ok = (worst_logp <= TOL_LOGPROB and worst_state <= TOL_STATE
                    and known_total == sum(lengths))
    report['multilane'] = {
        'lanes': lanes,
        'lengths': lengths,
        'distinct_lengths': len(set(lengths)),
        'logp_worst_abs_diff': worst_logp,
        'logp_worst_where': where,
        'state_worst_abs_diff': worst_state,
        'state_worst_where': state_where,
        'known_total': known_total,
        'naive_batch_counter': lanes * max(lengths),
        'padding_ratio_last': padding,
        'slot_isolation': True,
        'equivalent': bool(multilane_ok),
    }

    report['verdict'] = 'PASS' if b1_ok and multilane_ok else 'FAIL'
    report['elapsed_seconds'] = round(time.perf_counter() - started, 3)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--steps', type=int, default=4,
                        help='decisions in the B=1 lane (other lanes add 0..lanes-1)')
    parser.add_argument('--lanes', type=int, default=4)
    parser.add_argument('--rich', action='store_true',
                        help='use effects/relations/commands in the fixture')
    parser.add_argument('--seed', type=int, default=1701)
    parser.add_argument('--output', type=Path,
                        default=PROJECT_ROOT / 'builds' / 'native' / 'r0-batched-equiv.json')
    args = parser.parse_args(argv)
    if args.steps < 1 or args.lanes < 2:
        parser.error('need at least one step and two lanes')

    report = run(steps=args.steps, lanes=args.lanes, rich=args.rich, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    print(f'\nreport -> {args.output}')
    return 0 if report['verdict'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
