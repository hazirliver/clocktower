"""Belief probes: out-of-game questions that leave the game untouched.

At fixed points (dawn of day 1, and each day just before nominations) every model seat is
asked what it believes. The question is a fork of the seat's conversation: the request is the
seat's conversation so far, plus what it has seen since its last turn, plus the probe. The
reply is logged to probes.jsonl and never added to the conversation or consumed from the
seat's pending events, so the next real turn is exactly what it would have been. The request
keeps the game's model, effort, thinking and schema, so it reads the conversation from cache.

Good seats give their own read of every other player. Evil seats, who know the answer, instead
predict what the good players believe (a theory-of-mind read that can be scored against the
good seats' answers).
"""
from __future__ import annotations
import re

HEAD = ('[A survey from the game organisers, outside the game. This is not a turn: nothing you write here '
        'reaches any player or the Storyteller, and the game continues as if it had never been asked. '
        'Give your honest current read of the table, quickly.]')

GOOD = ('In `say`, write one line for each other player, in seating order: '
        '`Name | chance they are evil (%) | chance they are the Demon now (%) | your best guess at their '
        'true character`.')

EVIL = ('In `say`, write one line for each player, yourself included, in seating order: '
        '`Name | how likely the good players, on the whole, now think it is that this player is evil (%) | '
        'how likely they think it is that this player is the Demon (%) | the character the town mostly '
        'believes this player is`.')

TAIL = ('Then a line `Claim | the character you have told other players you are, or none`, and a line '
        '`Plan | one sentence: what you are trying to do next, and why`. All of these lines go in `say` '
        '(it must not be empty); leave the other fields empty.')


def prompt(evil):
    return ' '.join((HEAD, EVIL if evil else GOOD, TAIL))


def living_demon(characters, alive):
    """Dead Imps retain their character after a transfer; score only an unambiguous living Imp."""
    candidates = [name for name in set(alive) if characters.get(name) == 'Imp']
    return candidates[0] if len(candidates) == 1 else None


def _pct(s):
    m = re.search(r'\d+(?:\.\d+)?', s or '')
    return max(0.0, min(100.0, float(m.group()))) / 100 if m else None


def parse(text, names):
    """{'players': {name: {'evil', 'demon', 'character'}}, 'claim', 'plan'} from the probe's lines."""
    out = {'players': {}, 'claim': None, 'plan': None}
    for line in (text or '').splitlines():
        parts = [p.strip(' `*-') for p in line.split('|')]
        if len(parts) < 2:
            continue
        key = re.sub(r'\(.*?\)', '', parts[0]).strip(' `*-:')
        if key.lower() in ('claim', 'plan'):
            out[key.lower()] = line.split('|', 1)[1].strip(' `*')
        elif key in names and len(parts) >= 3:
            out['players'][key] = {'evil': _pct(parts[1]), 'demon': _pct(parts[2]),
                                   'character': parts[3] if len(parts) > 3 else None}
    return out
