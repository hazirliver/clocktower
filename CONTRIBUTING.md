# Contributing

Thanks for helping. Useful contributions, roughly in order of how easy they are to review:

- **Analyses of the existing games** (new questions answered from the logs, no API spend): add a report under
  `docs/findings/` with its scripts, and a row in `docs/findings/README.md`.
- **New models**: the `llm.py` entry plus a smoke test and memory check (see "Adding a model" in the README).
- **New experiments**: a plan in `experiments/` and, once run, its results, logs and a report. See
  [`docs/experiments.md`](docs/experiments.md) for open questions.
- **Engine, harness and tooling fixes**, with tests.
- **Rule changes**: possible, but they go through the process below.

## Development setup

```sh
python -m venv .venv && . .venv/bin/activate     # Python 3.11+
pip install -e '.[dev]'
python -m unittest discover -s tests             # must pass; makes no API calls and needs no keys
python -m clocktower run --model random --seed 1 --out games/demo/random   # a whole game with random bots
```

Tests cover the rules and the Storyteller (`tests/test_rules.py`, including checks that the Storyteller can give
every legal answer and never an impossible one), the tools (`tests/test_tools.py`: plans, Minion balancing,
ratings), the command line (`tests/test_cli.py`) and the human table (`tests/test_web.py`). CI runs them on every
push and pull request. Never add a test that calls a paid API.

To check the rating code against the published numbers, fetch the 2026 logs and refit; the output must match
`docs/plans/f2-ratings.json` byte for byte:

```sh
python -m clocktower fetch 'v16/f2-*' 'v17/f3*' 'v17/f4-*' 'v17/f5-*'
python -m clocktower ratings --no-page --out /tmp/f2.json && cmp /tmp/f2.json docs/plans/f2-ratings.json
```

## Style

- Plain Python 3.11, standard library first; the only runtime dependencies are `anthropic`, `huggingface_hub` and
  `numpy`. Ask before adding one.
- Small functions, short docstrings that say *why*; comments for anything a reader would otherwise have to
  rediscover. Match the surrounding code (single quotes, 120-column lines). `pyflakes clocktower tests` should be clean.
- Every rule should be explainable in one line, with no special cases. Storyteller choices are weights in
  `state.POLICY`, not branches scattered through the engine.
- Docs and reports: lead with the answer, plain words, numbers with intervals, and say how many games are behind a
  claim. Quotes from games must be verbatim from `events.jsonl`.
- Commits: one logical change each, with a message that says what changed and why.

## Changing the rules (the protocol)

The rules are versioned (`PROTOCOL` in `clocktower/cli.py`, currently v17) and **frozen per batch**: every game in a
batch, and every game behind a published rating, is played under the same version. Ratings compare models only
because they played the same game; a change to prompts, word limits, turn order, Storyteller weights, retries or
anything else that affects play would make new games incomparable with old ones. Even "fixes" shift balance:
[`docs/protocol-history.md`](docs/protocol-history.md) records how small-looking changes moved balance: with up to three
whispers a turn good won 69% of games (v6), and a public who-whispered-to-whom log cut evil from about a third of
games to about a fifth (v15).

So a rule change is a proposal, not a patch:

1. **Open an issue** (template "Rule change proposal") saying what to change, what problem in real games it
   fixes (cite games or a reader report), and what it may cost each side.
2. **Calibrate.** Implement it behind a flag, run a small side-swapped batch against the current rules on the same
   seeds, and compare balance (evil win rate), realism and cost. The `c`- and `w`-batches in `experiments/` are
   examples, and `docs/findings/protocol-design/` shows what the readers checked.
3. **Bump the version.** If adopted, the change becomes the default, `PROTOCOL` goes up by one, `docs/PROTOCOL.md`
   is updated, and `docs/protocol-history.md` gets a section saying what changed and why. New games go to
   `games/<new version>/`. The published fit pools v16 and v17, which differ only in how over-long turns are retried
   and how Minions are chosen; a change to play itself starts a new fit.

Harness changes that don't affect play (logging, telemetry, cost accounting, resuming) need no version bump; say so
in the pull request and show that a seed replays identically.

## Running experiments that cost money

Every game costs from about $2 (cheap models) to $50+ (the largest models, 12 players, long games). Set
`--budget` or a plan's `budget`; it stops new calls when the shared all-time spend in `games/ledger.jsonl` reaches
the threshold. Calls already in flight may exceed it, so this is not a hard billing cap. Start with one side-swapped
pair, read a transcript, then scale. Use fresh seeds (blocks of 100 per matchup, not overlapping existing batches
in `experiments/`), and decide in advance how many games you will run and how you will allocate extra ones
(`python -m clocktower allocate`): never add games because results lean one way.

If you'd like your games in the shared dataset, open a pull request with the plan, each game's `config.json` and
`result.json`, and a link to the logs (or ask in the pull request how to upload them).
