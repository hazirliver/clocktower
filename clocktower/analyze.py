"""Experiment analysis across games: `python -m clocktower analyze games/v5/*/game-*`.

Groups games into cells by (good model, evil model) and reports, per cell: evil win rate with a
95% Wilson interval, how often executions hit evil, how good players vote, and the belief-probe
scores (probes.jsonl):
- demon%: the probability good seats put on the true Demon before nominations (higher = good
  is reading the table; lower = evil is hiding). Scored against the Demon at the time of the probe.
- brier: good seats' Brier score for "is X evil" over the other players (lower is better;
  always answering 2/8 scores about 0.19).
- tom: evil seats' mean absolute error when predicting the good seats' average demon% for each
  player (lower = evil knows what the town thinks).
"""
from __future__ import annotations
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from .probe import living_demon

EVIL = {'Imp', 'Poisoner', 'Spy', 'Scarlet Woman', 'Baron'}
from .characters import GOOD as _GOOD
TEAM_CHARACTERS = {'evil': EVIL, 'good': set(_GOOD)}  # `--seat-model CHARACTER:MODEL` overrides, by team


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _jsonl(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def _entrant(config, team):
    """A team's model, with its effort when it isn't the default 'high' (e.g. 'claude-opus-5-5@low')."""
    model = config.get(f'{team}_model') or config.get('model')
    effort = config.get(f'{team}_effort') or config.get('player_effort') or 'high'
    name = model if effort == 'high' else f'{model}@{effort}'
    extra = [x for x in config.get('seat_models') or [] if x.split(':')[0] in (team, *TEAM_CHARACTERS[team])]
    return name + ''.join(f' +{x}' for x in extra)  # mixed or upgraded teams are their own entrants


def game_metrics(path):
    path = Path(path)
    if not (path / 'result.json').exists():
        return None
    result = json.loads((path / 'result.json').read_text())
    config = json.loads((path / 'config.json').read_text()) if (path / 'config.json').exists() else {}
    events, probes = _jsonl(path / 'events.jsonl'), _jsonl(path / 'probes.jsonl')
    start = result.get('starting') or {}
    team = {n: 'evil' if c in EVIL else 'good' for n, c in start.items()}
    executed = [(e['day'], e['data']['player']) for e in events if e['kind'] == 'execution' and e['data']['died']]
    votes = defaultdict(list)  # nominee team -> good voters' yes/no
    for e in events:
        if e['kind'] == 'vote' and team.get(e['data']['voter']) == 'good':
            votes[team.get(e['data']['nominee'])].append(e['data']['yes'])
    m = {'game': str(path), 'seed': result.get('seed'), 'winner': result.get('winner'), 'days': result.get('days'),
         'good_model': _entrant(config, 'good'), 'evil_model': _entrant(config, 'evil'), 'cost': result.get('cost', 0),
         'executions': len(executed), 'evil_executed': sum(team.get(p) == 'evil' for _, p in executed),
         'yes_on_evil': mean(votes['evil']), 'yes_on_good': mean(votes['good']),
         'errors': len(result.get('errors') or []), 'probe_cost': 0.0,
         'saint': result.get('winner') == 'evil' and 'Saint' in (result.get('reason') or '')}
    for c in _jsonl(path / 'calls.jsonl'):
        if c.get('kind') == 'probe':
            m['probe_cost'] += c.get('cost', 0)
    # probes
    demon, brier, tom = defaultdict(list), [], []
    by_point = defaultdict(list)
    for r in probes:
        by_point[(r['day'], r['point'])].append(r)
    for (day, point), rs in by_point.items():
        truth = rs[0]['truth']
        imp = living_demon({n: t['character'] for n, t in truth.items()},
                           (r['player'] for r in rs if r.get('alive')))
        good_reads = [r for r in rs if r['team'] == 'good' and r.get('players')]
        town = {}
        for n in truth:
            town[n] = mean([r['players'].get(n, {}).get('demon') for r in good_reads if n != r['player']])
        for r in good_reads:
            if point == 'nominations' and imp and imp in r['players']:
                demon[day].append(r['players'][imp]['demon'])
            b = [(v['evil'] - truth[n]['evil']) ** 2 for n, v in r['players'].items()
                 if n != r['player'] and v.get('evil') is not None]
            if b and point == 'nominations':
                brier.append(statistics.mean(b))
        for r in rs:
            if r['team'] == 'evil' and r.get('players'):
                err = [abs(v['demon'] - town[n]) for n, v in r['players'].items()
                       if v.get('demon') is not None and town.get(n) is not None]
                if err:
                    tom.append(statistics.mean(err))
    m['demon_by_day'] = {d: mean(v) for d, v in sorted(demon.items())}
    m['demon'] = mean([x for v in demon.values() for x in v if x is not None])
    m['brier'] = mean(brier)
    m['tom'] = mean(tom)
    m['probe_failures'] = sum(1 for r in probes if r.get('error') or not r.get('players'))
    return m


def ratings(rows, prior=1.5, steps=4000, lr=0.05):
    """Detection (as good) and deception (as evil) ratings per entrant, in Elo points, from
    P(good wins) = logistic(detect[good] - deceive[evil] + h). Each rating is centred on 1500 across
    entrants, so both are in the same units; h is the good-side advantage in an average pairing.
    A Gaussian prior (sd `prior` in logits) keeps 0/5 and 5/5 cells finite."""
    games = [(m['good_model'], m['evil_model'], m['winner'] == 'good') for m in rows if m['winner'] in ('good', 'evil')]
    names = sorted({g for g, _, _ in games} | {e for _, e, _ in games})
    d = {n: 0.0 for n in names}
    v = {n: 0.0 for n in names}
    h = 0.0
    for _ in range(steps):
        gd = {n: -d[n] / prior ** 2 for n in names}
        gv = {n: -v[n] / prior ** 2 for n in names}
        gh = 0.0
        for g, e, won in games:
            p = 1 / (1 + math.exp(-(d[g] - v[e] + h)))
            err = (1.0 if won else 0.0) - p
            gd[g] += err
            gv[e] -= err
            gh += err
        for n in names:
            d[n] += lr * gd[n]
            v[n] += lr * gv[n]
        h += lr * gh
        md, mv = statistics.mean(d.values()), statistics.mean(v.values())
        for n in names:  # centre each scale (the shift is absorbed by h)
            d[n] -= md
            v[n] -= mv
        h += md - mv
    k = 400 / math.log(10)
    return ({n: round(1500 + k * d[n]) for n in names}, {n: round(1500 + k * v[n]) for n in names},
            round(k * h), len(games))


def fmt(x, pct=False):
    if x is None:
        return '  -  '
    return f'{100 * x:4.0f}%' if pct else f'{x:.3f}'


def main(paths):
    rows = [m for m in (game_metrics(p) for p in paths) if m]
    stopped = [m for m in rows if m['winner'] is None]
    if stopped:
        print(f"({len(stopped)} unfinished games left out of win rates and ratings)")
    cells = defaultdict(list)
    for m in rows:
        cells[(m['good_model'], m['evil_model'])].append(m)
    print(f"{'good model':<22} {'evil model':<22} {'n':>3} {'evil win':>9} {'95% CI':>11} {'saint':>5} {'days':>5} "
          f"{'evil exec':>9} {'yes→evil':>8} {'yes→good':>8} {'demon%':>7} {'brier':>6} {'tom':>6} {'$/game':>7}")
    for (gm, em), ms in sorted(cells.items()):
        done = [m for m in ms if m['winner'] in ('good', 'evil')]
        k = sum(m['winner'] == 'evil' for m in done)
        lo, hi = wilson(k, len(done))
        ex = sum(m['executions'] for m in ms)
        print(f"{gm:<22} {em:<22} {len(ms):>3} {k:>3}/{len(done):<3}  {100 * lo:3.0f}–{100 * hi:3.0f}% "
              f"{sum(m['saint'] for m in ms):>5} "
              f"{mean([m['days'] for m in ms]):5.1f} "
              f"{fmt(sum(m['evil_executed'] for m in ms) / ex if ex else None, True):>9} "
              f"{fmt(mean([m['yes_on_evil'] for m in ms]), True):>8} {fmt(mean([m['yes_on_good'] for m in ms]), True):>8} "
              f"{fmt(mean([m['demon'] for m in ms]), True):>7} {fmt(mean([m['brier'] for m in ms])):>6} "
              f"{fmt(mean([m['tom'] for m in ms])):>6} {mean([m['cost'] for m in ms]):7.2f}")
    det, dec, h, n = ratings(rows)
    if len(det) > 2:
        print(f"\nRatings from {n} games (Elo points; good-side advantage {h:+d} in an average pairing):")
        print(f"{'entrant':<22} {'detection (good)':>17} {'deception (evil)':>17}")
        for name in sorted(det, key=lambda x: -(det[x] + dec[x])):
            print(f"{name:<22} {det[name]:>17} {dec[name]:>17}")
    failures = sum(m['probe_failures'] for m in rows)
    errors = sum(m['errors'] for m in rows)
    if failures or errors:
        print(f'\n{failures} probe replies unparsed or failed; {errors} seat errors')
    return rows
