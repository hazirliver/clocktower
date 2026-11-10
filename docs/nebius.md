# Nebius Token Factory

Use `nebius:<exact API model id>` to route to Token Factory Chat Completions. This adapter uses JSON Schema
responses and per-seat message histories. It does not send OpenAI Responses items, encrypted reasoning,
explicit cache controls or Flex settings. Existing providers and game rules are unchanged.

## Setup

From the repository root, install `.[dev]` as in the README. No new runtime dependency is needed.
Export `TOKENFACTORY_API_KEY` into the process environment. The CLI does not load `.env` automatically.
For an existing, trusted shell-compatible `.env`, `set -a; source .env; set +a` exports it without printing it;
do not use shell tracing. Nebius-only runs and memory checks need no other provider keys.

`TOKENFACTORY_BASE_URL` defaults to `https://api.tokenfactory.nebius.com/v1`. Override it only for a trusted
Token Factory endpoint; the key is sent there. Use HTTPS with no embedded credentials or query parameters.
Redirects are rejected. TLS verification stays enabled; configure a trusted CA bundle if local Python lacks one.

```sh
mkdir -p experiments/nebius-results
cp experiments/nebius/models.example.json experiments/nebius-results/models.json
cp experiments/nebius/pilot.example.json experiments/nebius-results/pilot.json
python -m clocktower tournament experiments/nebius-results/pilot.json --dry-run
```

Before a paid call, replace each `null` price with the current account's USD per million input, output and
cached-input tokens. Unknown prices fail before inference. The example's 32,768-token window is a conservative
experiment setting, not a measured provider limit; check context/output limits and freeze them for the batch.
`max_output` clamps the CLI ceiling (default 8,000). The pilot budget starts at zero deliberately.

Check the [live catalog](https://tokenfactory.nebius.com/models), [API schema](https://api.tokenfactory.nebius.com/docs),
[structured-output documentation](https://docs.tokenfactory.nebius.com/ai-models-inference/json), and
[cookbook](https://github.com/nebius/token-factory-cookbook). Catalog presence does not prove schema, reasoning,
context or pricing support. Test the exact endpoint and model. This adapter requires `json_schema`; there is no
silent prompt-only fallback for models that reject it.

## Memory, effort and measurement

`memory: "replies"` is the default: only assistant replies are carried forward. Reasoning returned as `reasoning`
or `reasoning_content` is logged but not replayed. A model verified to consume its previous reasoning can use
`memory: "reasoning"` and `reasoning_field` set to the exact supported field. Both forms are stripped in the
memory-check control. A successful recall of public text does not establish reasoning continuity.

`reasoning_effort: true` opts into the API's `reasoning_effort` parameter (high normally, low on a ceiling retry).
Leave it false unless verified for that model. Those calls log `provider-default`; passing `--effort high` does
not establish high reasoning effort on an endpoint without this capability. The two example Instruct models
were tested without an effort parameter. Treat this as a separate experimental configuration, not a reproduction
of the historical high-effort study.

Probes copy the seat's messages and trimming state, leave its pending game text untouched, and share only the
ledger and call logger. Every successful HTTP response with valid usage is billed in the ledger, including
malformed JSON, schema failures, refusals and capped attempts. Cached tokens are subtracted from ordinary input;
reasoning tokens are already included in completion tokens and are not charged twice. Costs are estimates from
the configured rates, with raw usage retained in `calls.jsonl`.

Transient HTTP/transport failures use bounded backoff; HTTP 402 waits up to 30 minutes for credit. Permanent
errors stop the seat with sanitized messages. A ceiling or refusal is retried once before passing; four unusable
outputs stop the seat. Missing/invalid usage stops immediately rather than recording free inference. Lost HTTP
responses can still be billable: reconcile these and missing usage with the provider before resuming. The ledger
threshold is not a hard billing cap; concurrent calls and transport retries can overshoot it.

Checkpoints retain messages, trimming state and input counts in the existing format. A Nebius run also records
endpoint, capabilities and prices in `config.json`; resume rejects a changed configuration before overwriting it.
Rerun with the original settings and output directory. Only load trusted local checkpoints (they use pickle).
After resume, game logs roll back to the checkpoint while the spend ledger retains all billed attempts.

## Proposed experiment, requires approval

No live game or tournament was run for this integration. Keep the full pipeline gated on explicit approval.

1. Verify prices, limits, schema support and memory for the selected models; record the date and endpoint.
2. Obtain approval for a dollar threshold and one side-swapped pair. Set that threshold in the copied pilot plan.
   Review the seed block (91,000,000) against other local experiments before starting.
3. Run the pair with full default game/probe settings. Inspect both transcripts, probe coverage, failures,
   trimming, usage and provider billing before proposing any larger batch.
4. Predeclare additional seeds and sample size. Do not extend the experiment because one result looks favorable.
   Two games are a plumbing pilot, not enough to support a comparative ranking.

Commands for use only after the relevant approval:

```sh
python -m clocktower memory-check nebius:Qwen/Qwen3-30B-A3B-Instruct-2507 \
  --nebius-config experiments/nebius-results/models.json --trials 1 \
  --ledger experiments/nebius-results/ledger.jsonl
python -m clocktower tournament experiments/nebius-results/pilot.json
python -m clocktower analyze experiments/nebius-results/pilot-*/game-*
python -m clocktower replay experiments/nebius-results/pilot-qwen30b-v-qwen235b/game-01
```

Memory checks are paid and append to the ledger but do not set a spending threshold. Individual games also accept
`--nebius-config`, `--ledger`, `--budget`, `--good-model`, `--evil-model` and prefixed seat overrides, for example
`--seat-model good:nebius:Qwen/Qwen3-30B-A3B-Instruct-2507:2`.

For a separate rating fit, create `experiments/nebius-results/rating-models.json` containing entries with `id`,
verified `released` date and `lab` (model author, not hosting provider). Then use:

```sh
python -m clocktower ratings --games experiments/nebius-results --batches 'pilot-*' \
  --models experiments/nebius-results/rating-models.json \
  --out experiments/nebius-results/ratings.json --no-page
```

Custom batches require separate model/output files and disable historical page/trend generation. The default
historical batch selection is unchanged. New outputs live outside `games/v16` and `games/v17` and are ignored by
Git. Tournament uploads now require both top-level `"upload": true` and `HF_TOKEN`; a token alone never triggers
an upload. The existing upload destination is the upstream public dataset, so leave uploads disabled here.

## Validation, 2026-11-10

All 149 tests passed with `python -m unittest discover -s tests` (Python 3.14.3);
`python -m pyflakes clocktower tests` and `git diff --check` also passed. The full suite required local socket
access for its HTTP tests. Existing resource warnings remain in unrelated file reads.

Offline tests cover the request contract, nested schema validation, retries, usage/costs, reply/reasoning memory,
concurrent probe isolation, model-only credentials, a complete fake-provider game and interrupted resume, replay,
analysis, a side-swapped fake batch, a separate rating fit and upload prevention. Fake outputs test harness
behavior, not model quality. No live games or tournaments, deployments or dataset uploads were performed.

The authenticated catalog listed both example models. Eight tiny live Chat Completions calls verified simple
JSON, two-turn public-text recall, isolated probes, and the exact player action schema. Both models returned
valid schemas; the smaller model returned empty `say` despite a request to say hello. No reasoning tokens were
reported. This does not verify hidden reasoning memory, long-context behavior, 8K output, rate-limit recovery,
full-game reliability or provider quality/equivalence claims.

[Recorded smoke evidence](../experiments/nebius/smoke-2026-11-10.json): 380 prompt tokens (80 reported cached),
155 completion tokens. At **illustrative**, unverified rates of $1/M input and cached input and $3/M output,
the estimate is **$0.000845**. Actual billed dollars remain unverified; use the provider's billing view.
The local Python TLS setup needed the already-installed `truststore` during the manual smoke test; production
code still uses the standard library TLS context and needs a valid local trust store.

The feature branch includes prerequisite PR #1 (test lint), #2 (living-Demon probe scoring) and #3 (shared CLI
ledger) without merging them. Historical winner-based ratings were not regenerated. PR #2's warning about stale
historical Demon-belief figures still applies.
