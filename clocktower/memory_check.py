"""Memory check for a new model: does it see its own earlier reasoning on the next turn?

Turn 1 asks the model to think of a number and an animal and reply only "ok"; turn 2 asks it
to reveal them, or to answer NO ACCESS if it cannot see its earlier reasoning. Each trial runs twice: with the
reasoning carried back exactly as the game engine carries it, and with it stripped (the control). A model that
sees its reasoning reveals the secret when carried and says NO ACCESS when stripped; a model that sees only its
replies says NO ACCESS (or guesses) both times. Record the answer as `memory` in llm.MODELS: 'reasoning' or
'replies'. Each trial costs a few cents.

    python -m clocktower memory-check gpt-6.1-sol --trials 3
"""
from __future__ import annotations
import argparse
import asyncio
import json
import re
from pathlib import Path

from .llm import Ledger, NoLimit, conversation, make_client, OPENAI_PREFIXES

SYSTEM = 'You are taking part in a short test of your memory across turns. Answer in the JSON format given.'
SCHEMA = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer'],
          'additionalProperties': False}
TURN1 = ('A guessing game. Think of a random four-digit number and an animal, but keep them to yourself for now: '
         'answer with exactly "ok".')
TURN2 = ('Game over: now tell me the number and the animal you thought of on the previous turn. If you can no '
         'longer see what you picked, answer exactly "NO ACCESS" instead of making up new ones.')


def strip_reasoning(messages):
    """The conversation with every reasoning block removed (Claude thinking blocks, OpenAI reasoning items)."""
    out = []
    for m in messages:
        if isinstance(m, dict) and m.get('type') == 'reasoning':
            continue  # an OpenAI reasoning item
        if isinstance(m, dict) and isinstance(m.get('content'), list):
            m = {**m, 'content': [b for b in m['content'] if not (isinstance(b, dict) and b.get('type') in
                                                                   ('thinking', 'redacted_thinking'))]}
        if isinstance(m, dict):
            m = {k: v for k, v in m.items() if k not in ('reasoning', 'reasoning_content')}
        out.append(m)
    return out


async def trial(client, ledger, model, effort, carried):
    c = conversation(client, SYSTEM, SCHEMA, model=model, label='memory-check', ledger=ledger, semaphore=NoLimit(),
                     effort=effort, max_tokens=8000)
    first = await c.send(TURN1)
    if not carried:
        c.messages = strip_reasoning(c.messages)
    second = await c.send(TURN2)
    return {'carried': carried, 'turn1_thinking': first.thinking[:300], 'turn2_answer': second.action.get('answer', '')}


async def run(model, trials, effort, ledger_path):
    client = None if model.startswith(('nebius:', 'openrouter:', *OPENAI_PREFIXES)) else make_client()
    ledger = Ledger(ledger_path)
    jobs = [trial(client, ledger, model, effort, carried) for carried in (True, False) for _ in range(trials)]
    return await asyncio.gather(*jobs)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='clocktower memory-check', description=__doc__.split('\n\n')[0])
    ap.add_argument('model')
    ap.add_argument('--nebius-config', help='Token Factory model capabilities and prices JSON')
    ap.add_argument('--trials', type=int, default=3, help='trials per condition (carried, stripped)')
    ap.add_argument('--effort', default='high')
    ap.add_argument('--ledger', default=str(Path(__file__).resolve().parent.parent / 'games' / 'ledger.jsonl'),
                    help='spend ledger to append to')
    args = ap.parse_args(argv)
    if args.nebius_config:
        from .nebius import configure
        configure(args.nebius_config)
    results = asyncio.run(run(args.model, args.trials, args.effort, args.ledger))
    for r in results:
        print(json.dumps(r))
    for carried in (True, False):
        answers = [r['turn2_answer'] for r in results if r['carried'] == carried]
        blind = sum('NO ACCESS' in a.upper() for a in answers)
        shown = sum(bool(re.search(r'\d{4}', a)) for a in answers)
        print(f"{'carried ' if carried else 'stripped'}: {shown}/{len(answers)} revealed a number, {blind} NO ACCESS")
    print("Sees its reasoning if 'carried' reveals and 'stripped' says NO ACCESS: memory 'reasoning'; "
          "NO ACCESS both times: memory 'replies'. Check revealed secrets against turn1_thinking where shown.")
    return results


if __name__ == '__main__':
    main()
