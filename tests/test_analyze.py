"""Belief scores must follow the living Demon, including when its own probe fails."""
import json
import tempfile
import unittest
from pathlib import Path

from clocktower.analyze import game_metrics
from clocktower.probe import living_demon


class DemonProbeTests(unittest.TestCase):
    def test_transfer_scores_the_successor_not_the_dead_imp(self):
        for predecessor in ('Poisoner', 'Scarlet Woman'):
            with self.subTest(successor_was=predecessor), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                (root / 'config.json').write_text('{"model": "test"}')
                starting = {'Alex': 'Imp', 'Blair': predecessor, 'Casey': 'Chef'}
                (root / 'result.json').write_text(json.dumps({'starting': starting, 'winner': 'evil'}))
                rows = []
                for day, transferred in ((1, False), (2, True)):
                    truth = {n: {'character': 'Imp' if transferred and n == 'Blair' else c, 'evil': n != 'Casey'}
                             for n, c in starting.items()}
                    for name in starting:
                        r = {'day': day, 'point': 'nominations', 'player': name, 'truth': truth,
                             'alive': not (transferred and name == 'Alex'),
                             'team': 'good' if name == 'Casey' else 'evil'}
                        if name == 'Casey':
                            r['players'] = {n: {'demon': p, 'evil': .5} for n, p in
                                            (('Alex', 0.0 if transferred else .7), ('Blair', .9))}
                        else:
                            r['error'] = 'probe failed'  # truth/alive remain useful without a parsed reply
                        rows.append(r)
                # Completion order must not change the selected Demon.
                (root / 'probes.jsonl').write_text('\n'.join(json.dumps(r) for r in reversed(rows)))
                got = game_metrics(root)
                self.assertEqual(got['demon_by_day'], {1: .7, 2: .9})
                self.assertAlmostEqual(got['demon'], .8)

    def test_missing_or_ambiguous_living_demon_is_not_guessed(self):
        characters = {'Alex': 'Imp', 'Blair': 'Imp', 'Casey': 'Chef'}
        self.assertIsNone(living_demon(characters, []))
        self.assertIsNone(living_demon(characters, ['Alex', 'Blair']))
        self.assertEqual(living_demon(characters, ['Blair', 'Blair', 'Casey']), 'Blair')

    def test_missing_demon_record_leaves_metric_unscored(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'config.json').write_text('{"model": "test"}')
            (root / 'result.json').write_text('{}')
            row = {'day': 1, 'point': 'nominations', 'player': 'Casey', 'alive': True, 'team': 'good',
                   'truth': {'Alex': {'character': 'Imp', 'evil': True}},
                   'players': {'Alex': {'demon': .8, 'evil': .9}}}
            (root / 'probes.jsonl').write_text(json.dumps(row))
            self.assertIsNone(game_metrics(root)['demon'])
