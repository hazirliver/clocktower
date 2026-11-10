"""Experiments as plan files: `python -m clocktower plan ...` writes one, `tournament PLAN` runs it.

A plan lists matchups (a good model and an evil model, optional efforts and per-seat overrides) and how
many side-swapped pairs each plays. `tournament` launches every matchup at once as its own `run`
process (one process per matchup, so one failure never stops the others), each with its own block of
seeds, and resumes: finished games are kept, unfinished ones continue from their last checkpoint.
Logs go to `<out>-logs/`.

Each game's guaranteed Minion is chosen here, from the plan alone (so a rerun or `--only` gets the same
choice): a matchup's `args` may force them (`["--minions", "Spy,Baron"]`, one per pair), a matchup may list
them (`"minions": [...]`, as `clocktower.allocate` writes), and otherwise each pair gets the Minion its two
models have been dealt least so far in the plan, then the least dealt in the whole plan, cycling.

    python -m clocktower plan experiments/models.json --within 10 --pairs 1 --seed 80000 --out games/v17/f1 > experiments/f1.json
    python -m clocktower tournament experiments/f1.json
    python -m clocktower tournament experiments/f1.json --resume-stopped   # relaunch stopped games, one process each
"""
from __future__ import annotations
import collections
import itertools
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from .characters import MINIONS

SEEDS_PER_MATCHUP = 100  # matchup i uses seeds seed + 100*i onwards, unless it sets its own `seed`
UPLOAD_EVERY = 1800  # seconds between snapshots of the plan's games to the Hugging Face dataset


def upload(plan):
    """Snapshot the plan's game folders (finished games, and checkpoints of unfinished ones) to the log
    dataset, so a replaced machine loses nothing: `fetch` them back and rerun `tournament` to resume."""
    if plan.get('upload') is not True:
        return
    from huggingface_hub import HfApi
    from .store import REPO
    out = Path(plan['out'])
    try:
        HfApi().upload_folder(repo_id=REPO, repo_type='dataset', folder_path=str(out.parent),
                              path_in_repo=out.parent.name, allow_patterns=[f'{out.name}-*/**'],
                              commit_message=f'{out.name}: snapshot')
        print(f'uploaded {out}-* to {REPO}', flush=True)
    except Exception as e:  # a failed upload never stops the games
        print(f'upload failed: {e!r}'[:300], flush=True)


def make_plan(models, within=None, pairs=1, seed=80000, out='games/v12/f1', players='10-12'):
    """Every pair of models at most `within` places apart in the list (all pairs if None)."""
    ms = [m if isinstance(m, dict) else {'id': m, 'short': m.replace(':', '-').replace('/', '-')} for m in models]
    matchups = []
    for (i, a), (j, b) in itertools.combinations(enumerate(ms), 2):
        if within is None or j - i <= within:
            matchups.append({'name': f"{a['short']}-v-{b['short']}", 'good': a['id'], 'evil': b['id'], 'pairs': pairs})
    return {'out': out, 'seed': seed, 'players': players, 'matchups': matchups}


def commands(plan, per_game=False):
    """One command per matchup (all its games in one asyncio process: the games are I/O-bound, so a process
    runs hundreds of API calls at once), or with per_game one per game (MATCHUP/gNN, MATCHUP/gNN-swap) to rerun
    a single game. To halt one game, touch <game>/STOP; to resume it, delete STOP and launch MATCHUP/gNN."""
    for name, cmd, pairs in _matchup_commands(plan):
        if not per_game:
            yield name, cmd
            continue
        for k in range(1, pairs + 1):
            for side, suffix in (('normal', ''), ('swap', '-swap')):
                yield f'{name}/g{k:02d}{suffix}', cmd + ['--only', str(k), '--side', side]


def assign_minions(plan):
    """{matchup name: [guaranteed Minion for pair 1, 2, ...]}, balanced per model and across the plan."""
    per_model = collections.defaultdict(collections.Counter); total = collections.Counter(); out = {}

    def deal(m, minion):
        for model in (m['good'], m['evil']):
            per_model[model][minion] += 2  # both games of the side-swapped pair
        total[minion] += 2

    auto = []
    for m in plan['matchups']:  # forced choices count first, wherever they are in the plan
        args = m.get('args', [])
        if '--characters' in args:
            out[m['name']] = None
        elif '--minions' in args or m.get('minions'):
            forced = args[args.index('--minions') + 1].split(',') if '--minions' in args else m['minions']
            forced = [x.strip() for x in forced]
            out[m['name']] = forced
            for k in range(m.get('pairs', 1)):
                deal(m, forced[k % len(forced)])
        else:
            auto.append(m)
    for i, m in enumerate(auto):
        ms = []
        for k in range(m.get('pairs', 1)):
            pick = min(MINIONS, key=lambda x: (max(per_model[m['good']][x], per_model[m['evil']][x]),
                                               per_model[m['good']][x] + per_model[m['evil']][x], total[x],
                                               (MINIONS.index(x) - i - k) % len(MINIONS)))
            ms.append(pick); deal(m, pick)
        out[m['name']] = ms
    return out


def _matchup_commands(plan):
    minions = assign_minions(plan)
    for i, m in enumerate(plan['matchups']):
        cmd = [sys.executable, '-m', 'clocktower', 'run', '--games', str(m.get('pairs', 1)), '--swap',
               '--seed', str(m.get('seed', plan['seed'] + SEEDS_PER_MATCHUP * i)), '--players', str(plan.get('players', '10-12')),
               '--good-model', m['good'], '--evil-model', m['evil'], '--out', f"{plan['out']}-{m['name']}",
               '--budget', str(plan.get('budget', 100000))]
        for flag in ('nebius_config', 'ledger'):
            if plan.get(flag):
                cmd += [f"--{flag.replace('_', '-')}", plan[flag]]
        for flag in ('good_effort', 'evil_effort'):
            if m.get(flag):
                cmd += [f"--{flag.replace('_', '-')}", m[flag]]
        for s in m.get('seat_models', []):
            cmd += ['--seat-model', s]
        if minions[m['name']] and '--minions' not in m.get('args', []):
            cmd += ['--minions', ','.join(minions[m['name']])]
        cmd += m.get('args', [])  # any other run flags, e.g. ["--private-beats", "6"] for a protocol variant
        yield m['name'], cmd, m.get('pairs', 1)


def stopped(path):
    """Games of the plan that stopped (seat failed, budget, halted: a result.json with no winner, and no STOP
    file) and that no running process is playing, as MATCHUP/gNN[-swap] names."""
    plan = json.loads(Path(path).read_text())
    running = subprocess.run(['pgrep', '-af', 'clocktower run'], capture_output=True, text=True).stdout
    names = []
    for m in plan['matchups']:
        root = Path(f"{plan['out']}-{m['name']}")
        for d in sorted(root.glob('game-*')):
            r = d / 'result.json'
            if not r.exists() or json.loads(r.read_text()).get('winner') or (d / 'STOP').exists():
                continue
            k = int(d.name.split('-')[1]); swap = d.name.endswith('-swap')
            if f"--out {root} " in running and f"--only {k} --side {'swap' if swap else 'normal'}" in running:
                continue
            names.append(f"{m['name']}/g{k:02d}{'-swap' if swap else ''}")
    return names


def run(path, dry=False, only=None):
    """Launch the plan's matchups (or just those named in `only`, with the seeds and output folders
    they have in the full plan, so a pilot's games count toward the full run)."""
    plan = json.loads(Path(path).read_text())
    logs = Path(plan['out'] + '-logs')
    if not dry:
        logs.mkdir(parents=True, exist_ok=True)
    procs = []
    single = only and any('/' in o for o in only)  # MATCHUP/gNN[-swap]: run just those games, one process each
    for name, cmd in commands(plan, per_game=bool(single)):
        if only and name not in only and name.split('/')[0] not in only:
            continue
        if dry:
            print(' '.join(cmd))
            continue
        log = logs / f"{name.replace('/', '--')}.log"
        procs.append((name, subprocess.Popen(cmd, stdout=open(log, 'w'), stderr=subprocess.STDOUT)))
    if dry:
        return 0
    print(f'launched {len(procs)} processes; logs in {logs}/', flush=True)
    done = threading.Event()
    if plan.get('upload') is True and os.environ.get('HF_TOKEN'):
        def snapshots():
            while not done.wait(UPLOAD_EVERY):
                upload(plan)
        threading.Thread(target=snapshots, daemon=True).start()
    failed = [name for name, p in procs if p.wait() != 0]
    done.set()
    if plan.get('upload') is True and os.environ.get('HF_TOKEN'):
        upload(plan)
    print('all matchups finished' + (f'; failed: {", ".join(failed)}' if failed else ''))
    return 1 if failed else 0
