# Clocktower

An eval in which language models play [Blood on the Clocktower](https://bloodontheclocktower.com/) (the Trouble
Brewing script) against each other, with a scripted Storyteller. One model plays every good seat and another every
evil seat, so each game pits one model's **detection** (finding the Demon) against another's **deception** (hiding
it) across a whole game of claims, private whispers, bluffs and votes: 10–12 players, several hours of play, one
continuous conversation per seat. A single Bradley–Terry fit turns the results into one rating per model.

The Storyteller is code, not a person or a model: a probability table, set by hand to match what an experienced human
Storyteller would typically do, so every outcome the rules allow can still happen but nobody steers the game. The
rules went through 17 versions, each checked by having models play batches of games and other models read every one.

![Ratings of the 11 models released in 2026, by release date, with 90% intervals](docs/charts/clocktower-final-ratings.png)

Website (leaderboard, every game's replay): [clocktowerbench.com](https://clocktowerbench.com) · Play against the
models in your browser: [play.clocktowerbench.com](https://play.clocktowerbench.com) · Write-up and video essay:
[dwarkesh.com/p/clocktower](https://www.dwarkesh.com/p/clocktower) · Rules of the eval:
[`docs/PROTOCOL.md`](docs/PROTOCOL.md) (v17, frozen)

> Unofficial fan project, not affiliated with or endorsed by The Pandemonium Institute. Blood on the Clocktower
> is a trademark of The Pandemonium Institute; character names are used here for research. Buy the game!

## Results

386 games between the 11 models released in 2026 (protocol v16/v17; Elo scale, the 11 models average 1500;
90% intervals from a bootstrap over side-swapped pairs):

| Model | Lab | Released | Rating | 90% interval | Detection (good) | Deception (evil) |
|---|---|---|---|---|---|---|
| GPT-6 Astra | OpenAI | Sep 2026 | **1859** | 1779–1983 | 1940 | 1806 |
| GPT-6.1 Sol | OpenAI | Sep 2026 | **1734** | 1684–1815 | 1793 | 1688 |
| Claude Fable 5.1 | Anthropic | Sep 2026 | **1595** | 1537–1668 | 1582 | 1588 |
| GPT-5.6 Sol | OpenAI | Jul 2026 | **1591** | 1543–1647 | 1666 | 1519 |
| Claude Opus 5.5 | Anthropic | Sep 2026 | **1549** | 1489–1612 | 1500 | 1585 |
| Claude Fable 5 | Anthropic | Jun 2026 | **1544** | 1454–1643 | 1438 | 1643 |
| GPT-5.5 | OpenAI | Apr 2026 | **1482** | 1408–1551 | 1380 | 1560 |
| Claude Opus 4.6 | Anthropic | Feb 2026 | **1432** | 1340–1517 | 1489 | 1338 |
| GPT-5.4 | OpenAI | Mar 2026 | **1276** | 1193–1335 | 1309 | 1228 |
| Claude Opus 4.7 | Anthropic | Apr 2026 | **1241** | 1126–1323 | 1266 | 1224 |
| Claude Opus 4.8 | Anthropic | May 2026 | **1198** | 1076–1275 | 1136 | 1320 |

- Models released in 2026 gain about **650 Elo a year** (90% CI 553–827): GPT-6 Astra beats Opus 4.6, seven months
  older, about 92% of the time. Detection and deception rise together.
- Evil wins 33% of games. Setup matters: a Spy or Baron game helps good (+65, +70), a Scarlet Woman game helps
  evil (−66), relative to a Poisoner game.
- **Why OpenAI's newer models win:** mostly detection. Both labs' towns are level on day 1; from day 2 OpenAI towns
  weigh each new result against everything so far, while Claude towns take reported results at face value, treat
  early results as settled and execute more good players.
- **Smarter tables don't favour either side much:** evil's win rate drifts slightly down as both teams get stronger,
  but the drift is within chance, so the benchmark should not saturate.
- **Mixed teams** (half Opus 5.5, half GPT-5.6 Sol, 132 games) rate a little below their members' average; the gap
  is within chance overall and largest as evil.

Data: [`docs/plans/f2-ratings.json`](docs/plans/f2-ratings.json). Reports: [`docs/findings/`](docs/findings/README.md).
Every experiment and its cost: [`experiments/`](experiments/README.md). How the rules got here, version by version:
[`docs/protocol-history.md`](docs/protocol-history.md).

## Quick start (5 minutes)

```sh
git clone https://github.com/dwarkeshsp/clocktower && cd clocktower
python -m venv .venv && . .venv/bin/activate    # Python 3.11+
pip install -e '.[dev]'
python -m unittest discover -s tests            # rules, Storyteller and engine tests; no API calls

# A free game: every seat is a random practice bot (no API key needed)
python -m clocktower run --model random --seed 1 --out games/demo/random
python -m clocktower replay games/demo/random   # read it, with every seat's information

# A real game with cheap models (about $2-5): GPT-6.1 Sol plays good, Claude Opus 5.5 plays evil
export ANTHROPIC_API_KEY=... OPENAI_API_KEY=...
python -m clocktower run --good-model gpt-6.1-sol --evil-model claude-opus-5-5 --seed 1 --out games/demo/first
python -m clocktower watch                       # spectator app at http://127.0.0.1:8800 (follows live games)
```

A game directory holds `transcript.md` (an omniscient replay with every seat's thinking), `events.jsonl` (every
event, with who could see it), `calls.jsonl` (every API call with tokens and cost), `probes.jsonl` (belief probes),
`conversations/` (each seat's full API conversation), `config.json` and `result.json`. `replay DIR --view Casey`
shows the game as one seat saw it (`--thinking` adds its reasoning). Spend is appended to `games/ledger.jsonl`, and
`--budget` (default $1,000) stops new calls once the shared recorded spend reaches it. Requests already in flight
can exceed this threshold; it is not a hard billing cap.

Keys: `ANTHROPIC_API_KEY` for Claude models, `OPENAI_API_KEY` for OpenAI models, `OPENROUTER_API_KEY` only for
models no longer served by their lab (Opus 4.1, Sonnet 4). The log dataset is public, so downloads need no token.
`ANTHROPIC_WORKSPACE_ID` is optional (sent as the `anthropic-workspace-id` header if your organisation needs it).

## Nebius Token Factory

Use `nebius:<API model id>` with `TOKENFACTORY_API_KEY` and `--nebius-config` for explicit model capabilities
and prices. Nebius-only runs need no other provider keys. See [setup, validation and experiment plan](docs/nebius.md).
Tournament uploads are opt-in: a plan must set `"upload": true` as well as having `HF_TOKEN` in its environment.

## Running a matchup or a tournament

```sh
# One matchup: 2 seeds, each played twice with the sides swapped (so deal luck cancels), 10-12 players by seed
python -m clocktower run --good-model claude-opus-5-5 --evil-model gpt-6.1-sol --games 2 --swap --seed 1 --out games/v17/demo
python -m clocktower run --good-model claude-opus-5-5,gpt-6.1-sol ...   # a mixed team (seats split evenly)
python -m clocktower run ... --seat-model good:claude-opus-5-5:1       # upgrade one good seat
python -m clocktower run ... --seat-model Imp:gpt-6-astra              # a different model plays the Imp

# A tournament: write a plan from a model list, then run every game at once
python -m clocktower plan experiments/models-2026.json --within 3 --pairs 1 --seed 200000 --out games/v17/mine > experiments/mine.json
python -m clocktower tournament experiments/mine.json --dry-run    # see the commands first
python -m clocktower tournament experiments/mine.json              # resumable: rerun after a crash
python -m clocktower analyze games/v17/mine-*/game-*                # win rates, probe scores, quick ratings
```

A plan is JSON: a list of matchups, each with `good`, `evil`, `pairs` and optionally `seed`, `args` (extra `run`
flags), `good_effort`/`evil_effort`, `seat_models` or `minions`; a top-level `budget` sets the same stopping threshold
on all-time spend across processes (`games/ledger.jsonl`). Every game has its own seed; a side-swapped pair shares one. `tournament` deals each pair's
guaranteed Minion so the batch is balanced across Minions and models.

Controlling a running batch:

- `tournament PLAN` runs one process per matchup. Rerun the same command after a crash: finished games are kept and
  unfinished ones continue from their last dusk (`checkpoint.pkl`).
- `touch <game>/STOP` halts one game at its next turn; `tournament PLAN --only MATCHUP/g03` (or `g03-swap`) reruns or
  resumes one game; `tournament PLAN --resume-stopped` relaunches every game that stopped (a seat failed, the budget
  ran out, or it was halted).
- Health: `python -m clocktower supervise games/v17/mine` or, from Python,
  `clocktower.supervise.scan(['games/v17/mine-'])` then `report`/`alarms` (spend, lost and capped turns, stopped games).
- `python -m clocktower sim --games 200` plays random bots with no API calls (a fast check of the engine).

## How ratings are computed

```sh
python -m clocktower fetch 'v16/f2-*' 'v17/f3*' 'v17/f4-*' 'v17/f5-*'   # the 2026 games' logs (~2 GB)
python -m clocktower ratings --no-page      # refit: rewrites docs/plans/f2-ratings.json (reproduces it exactly)
python -m clocktower ratings --lab-trends   # also per-lab trends and the HTML pages
```

One Bradley–Terry model over every game: P(good wins) = logistic(r_good − r_evil + h + setup), where h is the
good side's advantage and the setup terms are the Minions in play (vs a Poisoner game) and the table size. Each
model gets one combined rating, plus a detection rating (as good) and a deception rating (as evil) from a split fit.
Ratings are on the Elo scale, centred so the models in `experiments/models-2026.json` average 1500; 90% intervals
come from 500 bootstrap resamples of side-swapped pairs (both games of a pair together). Code:
[`clocktower/ratings.py`](clocktower/ratings.py). Where to spend extra games is decided by a rule fixed in advance
(uncertainty per dollar, never which way results lean): `python -m clocktower allocate` ([`clocktower/allocate.py`](clocktower/allocate.py)).

## Adding a model

1. Add its price to `PRICES` in [`clocktower/llm.py`](clocktower/llm.py) (USD per million tokens; cache read/write
   prices if they differ from the defaults).
2. Add a `MODELS` entry if it differs from the default (adaptive thinking with an effort level for Claude; reasoning
   carried across turns for OpenAI): `thinking: 'budget'` for older Claude models, `memory: 'replies'` if it cannot
   see its earlier reasoning, `window` (context size, so long games are trimmed instead of failing), `max_output`,
   and `can_disable_thinking: False` if it rejects `thinking: disabled` (its ceiling retries then run at low effort).
3. Smoke test: `python -m clocktower run --model NEW --days 1 --players 10 --out games/smoke/NEW` and read the
   transcript.
4. Memory check: `python -m clocktower memory-check NEW --trials 3` asks it to think of a number on one turn and
   reveal it on the next, with its reasoning carried back and with it stripped. Revealed when carried and "NO
   ACCESS" when stripped means `memory: 'reasoning'`; "NO ACCESS" both times means `'replies'` (the prompt then
   tells it so). A few cents.
5. Add it to a model list in `experiments/` and plan games against rated models. Open a pull request with the
   `llm.py` change and the smoke-test and memory-check output.

## The data

Every game's full logs (transcripts, events, every API call, probes, conversations) are in the Hugging Face dataset
[`dwarkesh/clocktower-games`](https://huggingface.co/datasets/dwarkesh/clocktower-games) (v1–v17, about 1,100
finished games, plus `media/` for videos). `python -m clocktower fetch v17/b2-*` downloads matching game folders into
`games/`. Git keeps only each game's `config.json` and `result.json`. If downloads fail with size errors, set
`HF_HUB_DISABLE_XET=1`; fetch one batch at a time to avoid rate limits.

## Playing with friends

`python -m clocktower play` opens a table on port 8765 for several people on their own devices, plus AI seats
(`random` bots are free). Each person's seat gets exactly what a model's seat gets: the same events, prompts, moves
and word limits. Friends on the same network open the printed address; for others, use a tunnel such as
`cloudflared tunnel --url http://localhost:8765`. The public table at
[play.clocktowerbench.com](https://play.clocktowerbench.com) runs on Fly (`fly.toml`); `render.yaml` is an
alternative. See [`docs/hosting.md`](docs/hosting.md).

## Repository map

| Path | What it holds |
|---|---|
| `clocktower/characters.py` | Trouble Brewing characters, setups by table size, night order |
| `clocktower/state.py` | Grimoire, registration, deaths and victory; the Storyteller's choices and `POLICY` |
| `clocktower/game.py` | Setup, nights, beats, nominations, votes; one typography for all speech |
| `clocktower/prompts.py`, `schema.py` | The player system prompt and the fixed output schema ([full prompt](docs/player-prompt.md)) |
| `clocktower/llm.py` | Claude and OpenAI conversations, per-model settings (`MODELS`), prices, context trimming, ledger |
| `clocktower/agents.py`, `probe.py` | Seats (model, random bot); belief probes (out-of-game forks of a seat's conversation) |
| `clocktower/tournament.py`, `supervise.py` | Plans, balanced Minions, the parallel launcher; batch health |
| `clocktower/analyze.py`, `report.py` | Win rates, voting and probe scores; batch reports and replays |
| `clocktower/ratings.py`, `allocate.py`, `mixed.py` | The rating fit and pages; where extra games go; mixed-team analysis |
| `clocktower/memory_check.py` | The reasoning-memory check for a new model |
| `clocktower/store.py` | Downloading logs from the Hugging Face dataset |
| `clocktower/web.py`, `table/` | The multiplayer table for human play |
| `clocktower/watch.py`, `viewer/`, `site.py`, `render.py` | Spectator app (live or as static files) |
| `clocktower/cli.py` | Every command (`python -m clocktower --help`) |
| `tests/` | Unit tests: rules, Storyteller, engine, tools, the table (no API calls) |
| `experiments/` | Every experiment plan, with a table of dates, games and cost |
| `games/` | Each game's `config.json` and `result.json` (logs are in the dataset) |
| `docs/PROTOCOL.md`, `protocol-history.md` | The rules (v17) and why each is what it is |
| `docs/findings/` | Analysis reports, with an index |
| `docs/plans/`, `docs/charts/` | Rating data and pages; charts |
| `docs/experiments.md` | Open questions to explore next |
| `media/` | Pipelines for videos made from game logs |

## Contributing

Pull requests are welcome: new models, new experiments, analyses of the logs, engine fixes. Ideas we didn't get to
(other scripts, learning across games, one smart player, mixed teams across labs, teammate recognition by writing
style, humans vs AIs) are written up in [`docs/experiments.md`](docs/experiments.md). Read
[`CONTRIBUTING.md`](CONTRIBUTING.md) first (the protocol is frozen per batch, so rule changes go through a versioned
proposal).

## License

[MIT](LICENSE). Blood on the Clocktower and its characters belong to The Pandemonium Institute.
