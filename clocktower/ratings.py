"""The 2026 ratings: one Bradley-Terry fit over every 2026-model game, and the pages built from it.

P(good wins) = logistic(r[good] - r[evil] + h + setup), setup = which Minions were in play (vs a Poisoner
game) and the table size (vs 10 players). One combined rating per model, plus a detection (good) /
deception (evil) split; 90% intervals from a bootstrap over side-swapped pairs (both games of a pair
resampled together). Ratings are centred on 1500 = the mean of the models in `experiments/models-2026.json`.

    python -m clocktower ratings                    # fit -> docs/plans/f2-ratings.json and f2-ratings.html
    python -m clocktower ratings --lab-trends       # also per-lab gains -> docs/charts/lab-trends.json and .html
    python -m clocktower ratings --games /path/to/games --boot 100 --out /tmp/r.json --no-page

Needs the games' events.jsonl (`python -m clocktower fetch v16/f2-* v17/f3* v17/f4-* v17/f5-*`).
"""
from __future__ import annotations
import collections
import datetime
import glob
import json
import math
import os
import random
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
K = 400 / math.log(10)  # logits -> Elo points
MINIONS = ['Spy', 'Scarlet Woman', 'Baron']  # setup terms, each vs a Poisoner game
BATCHES = ['v16/f2-*', 'v17/f3*-*', 'v17/f4-*', 'v17/f5-*']  # the 2026 study
DROPPED = ('/v16/', 'claude-opus-4-8')  # Opus 4.8's 20 v16 games (bugged retries; replayed under v17)


def load(games_root=ROOT / 'games', batches=BATCHES):
    """One record per finished game: its side-swapped pair, models, winner and setup."""
    games = []
    dirs = [d for b in batches for d in glob.glob(f'{games_root}/{b}/game-*')]
    for d in dirs:
        if not os.path.exists(d + '/result.json'):
            continue
        r = json.load(open(d + '/result.json')); c = json.load(open(d + '/config.json'))
        if r.get('winner') not in ('good', 'evil'):
            continue
        if DROPPED[0] in d and DROPPED[1] in (c['good_model'], c['evil_model']):
            continue
        setup = next(json.loads(l) for l in open(d + '/events.jsonl') if '"kind": "setup"' in l)
        inplay = {s['character'] for s in setup['data']['seats']}
        parts = d.split('/')
        games.append({'pair': parts[-2] + '/' + parts[-1].replace('-swap', ''), 'good': c['good_model'],
                      'evil': c['evil_model'], 'won': r['winner'] == 'good', 'minions': [m in inplay for m in MINIONS],
                      'players': c.get('players') or len(setup['data']['seats'])})
    return games


def fit(games, names, split=False, prior=1.5, iters=40):
    """Newton's method on the penalised log-likelihood (Gaussian prior: sd 1.5 logits on ratings, 3 on setup
    terms, flat on h). Returns (combined or detection, None or deception, setup terms + h in Elo)."""
    N = len(names); idx = {n: i for i, n in enumerate(names)}
    P = (2 * N if split else N) + len(MINIONS) + 2 + 1
    X = np.zeros((len(games), P)); y = np.array([g['won'] for g in games], float)
    for k, g in enumerate(games):
        if split:
            X[k, idx[g['good']]] = 1; X[k, N + idx[g['evil']]] = -1
        else:
            X[k, idx[g['good']]] += 1; X[k, idx[g['evil']]] -= 1
        base = 2 * N if split else N
        X[k, base:base + 3] = g['minions']
        X[k, base + 3] = g['players'] == 11; X[k, base + 4] = g['players'] == 12
        X[k, -1] = 1
    w = np.zeros(P); pr = np.full(P, 1 / prior ** 2); pr[-6:] = 1 / 3 ** 2; pr[-1] = 1e-6
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w)); H = (X.T * (p * (1 - p))) @ X + np.diag(pr)
        w += np.linalg.solve(H, X.T @ (y - p) - pr * w)
    if split:
        d = w[:N] - w[:N].mean(); v = w[N:2 * N] - w[N:2 * N].mean()
        return {n: 1500 + K * d[i] for n, i in idx.items()}, {n: 1500 + K * v[i] for n, i in idx.items()}, w[-6:] * K
    r = w[:N] - w[:N].mean()
    return {n: 1500 + K * r[i] for n, i in idx.items()}, None, w[-6:] * K


def by_pair(games):
    pairs = collections.defaultdict(list)
    for g in games:
        pairs[g['pair']].append(g)
    return pairs


def years(models):
    return np.array([(datetime.date.fromisoformat(m['released']) - datetime.date(2026, 1, 1)).days / 365 for m in models])


def q90(xs):
    return [round(float(np.percentile(xs, 5))), round(float(np.percentile(xs, 95)))]


def ratings(games, models, boot=500, seed=0):
    """The ratings JSON (docs/plans/f2-ratings.json)."""
    if not games or not models or boot < 1:
        raise ValueError('Ratings need finished games, models and at least one bootstrap sample')
    ms = models; names = [m['id'] for m in ms]; yrs = years(ms)
    has_trend = len(set(yrs)) > 1
    comb, _, extra = fit(games, names); det, dec, _ = fit(games, names, split=True)
    pairs = by_pair(games)
    keys = list(pairs); rng = random.Random(seed); B = collections.defaultdict(list)
    for _ in range(boot):
        s = [g for k in (rng.choice(keys) for _ in keys) for g in pairs[k]]
        c, _, _ = fit(s, names); d, v, _ = fit(s, names, split=True)
        for n in names:
            B['c' + n].append(c[n]); B['d' + n].append(d[n]); B['v' + n].append(v[n])
        for key, rr in (('slope_c', c), ('slope_d', d), ('slope_v', v)):
            if has_trend:
                B[key].append(np.polyfit(yrs, [rr[n] for n in names], 1)[0])
    wins = collections.defaultdict(lambda: [0, 0, 0, 0])
    for g in games:
        wins[g['good']][0] += g['won']; wins[g['good']][1] += 1; wins[g['evil']][2] += not g['won']; wins[g['evil']][3] += 1
    return {'games': len(games), 'evil_win_rate': round(sum(not g['won'] for g in games) / len(games), 3),
            'good_advantage': round(float(extra[-1])),
            'setup_effects': {**dict(zip(MINIONS, map(lambda x: round(float(x)), extra[:3]))),
                              '11 players': round(float(extra[3])), '12 players': round(float(extra[4]))},
            'trend_per_year': {k: [round(float(np.polyfit(yrs, [rr[n] for n in names], 1)[0])), q90(B['slope_' + k])]
                               for k, rr in (('c', comb), ('d', det), ('v', dec))} if has_trend else {},
            'models': [{'id': m['id'], 'date': m['released'], 'lab': m['lab'], 'combined': round(comb[m['id']]),
                        'c90': q90(B['c' + m['id']]), 'detection': round(det[m['id']]), 'd90': q90(B['d' + m['id']]),
                        'deception': round(dec[m['id']]), 'v90': q90(B['v' + m['id']]),
                        'good_games': wins[m['id']][1], 'good_wins': wins[m['id']][0],
                        'evil_games': wins[m['id']][3], 'evil_wins': wins[m['id']][2]} for m in ms]}


def lab_trends(games, models, boot=400, seed=1):
    """Per-lab improvement per year (Anthropic vs OpenAI, and all) in combined (c), detection (d) and deception (v):
    a straight line through each lab's models by release date, 90% intervals from the same pair bootstrap
    (docs/charts/lab-trends.json)."""
    names = [m['id'] for m in models]
    yr = dict(zip(names, years(models))); labs = {m['id']: m['lab'] for m in models}

    def slopes(c, d, v):
        out = {}
        for lab in ('Anthropic', 'OpenAI', None):
            ns = [n for n in names if lab is None or labs[n] == lab]
            x = [yr[n] for n in ns]
            for k, rr in (('c', c), ('d', d), ('v', v)):
                b, a = np.polyfit(x, [rr[n] for n in ns], 1); out[(lab or 'all', k)] = (b, a)
        return out
    c, _, _ = fit(games, names); d, v, _ = fit(games, names, split=True)
    point = slopes(c, d, v)
    pairs = by_pair(games)
    keys = list(pairs); rng = random.Random(seed); B = collections.defaultdict(list)
    for _ in range(boot):
        s = [g for k in (rng.choice(keys) for _ in keys) for g in pairs[k]]
        cc, _, _ = fit(s, names); dd, vv, _ = fit(s, names, split=True)
        sl = slopes(cc, dd, vv)
        for k, (b, a) in sl.items():
            B[k].append(b)
        for lk in ('d', 'v', 'c'):
            B[('gap', lk)].append(sl[('OpenAI', lk)][0] - sl[('Anthropic', lk)][0])
    res = {f'{l}|{k}': {'slope': round(float(b)), 'intercept': round(float(a)), 'ci': q90(B[(l, k)])}
           for (l, k), (b, a) in point.items()}
    for lk in 'dvc':
        res[f'gap|{lk}'] = {'slope': round(float(point[('OpenAI', lk)][0] - point[('Anthropic', lk)][0])),
                            'ci': q90(B[('gap', lk)]),
                            'p_openai_faster': round(float(np.mean(np.array(B[('gap', lk)]) > 0)), 3)}
    return res


def ratings_page(data, template=ROOT / 'docs/plans/f2-ratings.template.html', out=ROOT / 'docs/plans/f2-ratings.html'):
    t = Path(template).read_text()
    Path(out).write_text(t.replace('__DATA__', json.dumps(data)).replace('__GAMES__', str(data['games'])))


def lab_trends_page(data, trends, template=ROOT / 'docs/charts/lab-trends.template.html',
                    out=ROOT / 'docs/charts/lab-trends.html'):
    t = Path(template).read_text()
    Path(out).write_text(t.replace('__RATINGS__', json.dumps(data)).replace('__TRENDS__', json.dumps(trends)).replace('__GAMES__', str(data['games'])))


def main(games_root=None, out=None, models=None, boot=500, page=True, trends=False, trends_out=None, batches=None):
    if batches is not None and (not out or not models or page or trends
                                or Path(models).resolve() == (ROOT / 'experiments/models-2026.json').resolve()
                                or Path(out).resolve() == (ROOT / 'docs/plans/f2-ratings.json').resolve()):
        raise ValueError('Custom batches require separate --out, --models and --no-page; no --lab-trends')
    games = load(games_root or ROOT / 'games', batches=BATCHES if batches is None else batches)
    ms = json.loads(Path(models or ROOT / 'experiments/models-2026.json').read_text())
    data = ratings(games, ms, boot)
    out = Path(out or ROOT / 'docs/plans/f2-ratings.json')
    out.write_text(json.dumps(data, indent=1))
    print(json.dumps({k: v for k, v in data.items() if k != 'models'}))
    for m in data['models']:
        print(f"{m['id']:20s} {m['combined']:5d} {m['c90']}  det {m['detection']} {m['d90']}  dec {m['deception']} {m['v90']}")
    if page:
        ratings_page(data)
    if trends:
        t = lab_trends(games, ms)
        Path(trends_out or ROOT / 'docs/charts/lab-trends.json').write_text(json.dumps(t, indent=1))
        print(json.dumps(t))
        if page:
            lab_trends_page(data, t)
    return data
