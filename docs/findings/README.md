# Findings

Analysis reports on the games. Most were written by "reader" agents (Claude sessions given a question, the game
logs and analysis scripts) and checked against the logs; every quote is from `events.jsonl`. Where a report says
"the user" it means the project maintainer, who set the design questions. Scripts mentioned as living in a
`scratchpad/` were throwaway and are not kept; the scripts in `scripts/` folders here are.

To rerun an analysis, fetch the logs first (`python -m clocktower fetch 'v17/f3-*'`, see the main README).

The historical Demon-belief figures below need recomputing: the original scorers sometimes selected a dead Imp
after a starpass or Scarlet Woman succession. The analysis code now selects the living Imp at each probe point;
the stored reports and derived figures have not been regenerated. Winner-based ratings are unaffected.

## Results (2026 models, protocol v16/v17)

| Report | Question | Headline |
|---|---|---|
| [`why-astra-wins/`](why-astra-wins/README.md) | Why does GPT-6 Astra top the ratings? (6 readers, 372 games) | 26/26 as good, 62% as evil. Its edge as good is group decisions, not sharper beliefs: the town's top suspect is evil 92% of the time (field 56%). As evil its Demon stays quiet and its lies fit the real night information. It thinks the least of any model. |
| [`why-openai-wins/`](why-openai-wins/metrics.md) | Why do OpenAI good teams beat Anthropic ones? ([metrics](why-openai-wins/metrics.md), [transcripts](why-openai-wins/transcripts.md)) | Level on day 1; from day 2 OpenAI towns keep updating (top suspect evil 52→70→81% by day) while Claude towns treat results as settled and execute two or more good players in 61% of games (OpenAI 31%). Not credulity, verbosity or effort. |
| [`round-robin-2026/validity.md`](round-robin-2026/validity.md) | Is the f2 round robin valid enough to scale? | No engine bugs; two tiers (+364 Elo for models from Fable 5 on); one pair per matchup can't order neighbours. Led to the v17 fixes. |
| [`round-robin-2026/duds.md`](round-robin-2026/duds.md) | Which games fell flat, and why? | 25% duds: mostly older-model blunders and deal luck (day-1 Slayer hits); 10-player tables dud more than 12. |
| [`round-robin-2026/realism.md`](round-robin-2026/realism.md) | Do the games read like real Trouble Brewing? | Yes, often like good tables; the unrealistic parts come from rules (dead players talk a lot, swing voters get swarmed). |
| [`round-robin-2026/interesting.md`](round-robin-2026/interesting.md) | Which games are worth watching? | About a third; a ranked list with game paths. |
| [`round-robin-2026/style-lies-probes.md`](round-robin-2026/style-lies-probes.md) | Do players find teammates by writing style? Do good players lie? Do probes track skill? | Nobody inferred teams from style, but simple text features separate teams (AUC 0.81): a validity risk. Good seats rarely lie (2.7% hard role lies). Probe accuracy correlates 0.89 with detection Elo. |
| [`mixed-teams/b2-mixed-rating.md`](mixed-teams/b2-mixed-rating.md) | Does a team split across two models play at its members' average? | A little worse overall (−48 Elo, interval crosses zero) and clearly worse as evil (−158 [−330, −14]). |
| [`mixed-teams/b2-why-mixed.md`](mixed-teams/b2-why-mixed.md) | Why do mixed evil teams lose? | Evil cover is a weakest link: the more visible Opus seats get caught first and the town follows their links. |
| [`best-games.md`](best-games.md) | Eight games that each show one skill | The Saint deal, a fake Undertaker and deliberate star-pass, the evil "writers' room", and more, with checked quotes. |

Data files beside the reports: `round-robin-2026/good-lies.json` (every good-side claim, graded),
`why-openai-wins/metrics.json`, `mixed-teams/*.json` (outputs of `python -m clocktower mixed`).

## Protocol design (how the rules were chosen)

Reader reports from the calibration batches that shaped the current protocol. They are the evidence behind
the decisions in [`docs/protocol-history.md`](../protocol-history.md); read them before proposing a rule change.

| Folder | Batch | What the readers checked |
|---|---|---|
| [`protocol-design/e1/`](protocol-design/e1/) | v9 e1, 60 games | Replies-only models (R1), frontier play (R2), reasoning effort across labs (R3), engine and Storyteller audit (R4), balance (R5), realism (S1), day structure (S2), the Storyteller judged as a human expert would (S3), diversity (S4) |
| [`protocol-design/c2/`](protocol-design/c2/C2-v12.md) | v12 c2, 12 games | First audit of the v12 Storyteller rules ("misinformation is false") |
| [`protocol-design/w1/`](protocol-design/w1/) | v14 w1, 50 games | One whisper per private turn: balance, conversation depth, realism, harness |
| [`protocol-design/c3/`](protocol-design/c3/) | v15 c3, 40 games | 6 vs 8 private beats under a public whisper log; three-person huddles |
| [`protocol-design/private-time/`](protocol-design/private-time/) | w1 + c3 | The private-time designs compared on realism, deception, diversity, interest and eval value (why v16 has 6 private beats and private whispers) |
