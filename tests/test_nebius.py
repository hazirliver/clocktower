"""Token Factory wire contract and a complete deterministic fake-provider game, without network access."""
import asyncio
import copy
import hashlib
import io
import json
import os
import random
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

from clocktower import cli, nebius, tournament
from clocktower.agents import ModelSeat, RandomSeat
from clocktower.analyze import game_metrics
from clocktower.game import Game
from clocktower.llm import Ledger, NoLimit, SeatFailed, conversation
from clocktower.memory_check import SCHEMA, strip_reasoning, run as memory_run
from clocktower.schema import player_schema

MODEL = 'nebius:test/model'
SETTINGS = {'window': 32000, 'max_output': 8000, 'prices': {'input': 1, 'output': 3, 'read': .5}}


def response(action=None, stop='stop', **message):
    return {'choices': [{'finish_reason': stop, 'message': {'role': 'assistant',
            'content': json.dumps(action or {'answer': 'ok'}), **message}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20,
                      'prompt_tokens_details': {'cached_tokens': 40},
                      'completion_tokens_details': {'reasoning_tokens': 5}}}


class NebiusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {'TOKENFACTORY_API_KEY': 'test-key'}, clear=True)
        env.start(); self.addCleanup(env.stop)
        models = patch.dict(nebius.MODELS, {MODEL: copy.deepcopy(SETTINGS)}, clear=True)
        models.start(); self.addCleanup(models.stop)
        self.calls = []
        self.ledger = Ledger(self.root / 'ledger.jsonl', cap=1)

    def conv(self, schema=SCHEMA):
        return conversation(None, 'system', schema, model=MODEL, label='Alex', ledger=self.ledger,
                            semaphore=NoLimit(), on_call=self.calls.append, ttl='5m', flex=True)

    def test_wire_memory_usage_and_probe_isolation(self):
        c = self.conv()
        async def check():
            with patch.object(c, '_post', return_value=response(reasoning_content='private')) as post:
                await c.send('setup')
                saved = copy.deepcopy((c.messages, c.last_input, c.trimmed))
                await asyncio.gather(c.send('probe1', commit=False), c.send('probe2', commit=False))
                self.assertEqual(saved, (c.messages, c.last_input, c.trimmed))
                await c.send('next')
                body = post.call_args.args[0]
                self.assertEqual(set(body), {'model', 'messages', 'max_tokens', 'response_format'})
                self.assertEqual(body['model'], 'test/model')
                self.assertEqual(body['response_format']['json_schema']['schema'], SCHEMA)
                self.assertEqual([m['role'] for m in body['messages']], ['system', 'user', 'assistant', 'user'])
                self.assertNotIn('private', json.dumps(c.messages))
        asyncio.run(check())
        self.assertAlmostEqual(self.ledger.spent, 4 * .00014)
        self.assertEqual(self.calls[0]['usage']['output_tokens'], 20)  # reasoning is already included
        self.assertEqual(self.calls[0]['thinking'], 'private')
        self.assertEqual(self.calls[0]['effort'], 'provider-default')

    def test_verified_reasoning_and_control(self):
        nebius.MODELS[MODEL].update(memory='reasoning', reasoning_field='reasoning_content', reasoning_effort=True)
        c = self.conv()
        with patch.object(c, '_post', return_value=response(reasoning_content='secret')):
            asyncio.run(c.send('first'))
        self.assertEqual(c.messages[-1]['reasoning_content'], 'secret')
        stripped = strip_reasoning(c.messages)
        self.assertNotIn('reasoning_content', stripped[-1])
        self.assertIn('reasoning_content', c.messages[-1])

    def test_malformed_schema_and_usage(self):
        for bad in ({'answer': 1}, {'answer': 'ok', 'extra': 2}, [], 'not-json'):
            c = self.conv()
            r = response(); r['choices'][0]['message']['content'] = json.dumps(bad)
            with self.subTest(bad=bad), patch.object(c, '_post', return_value=r) as post:
                with self.assertRaises(SeatFailed):
                    asyncio.run(c.send('turn'))
                self.assertEqual(post.call_count, 4)
                self.assertEqual(len(c.messages), 1)
        for usage in ({}, {'prompt_tokens': 2, 'completion_tokens': 1, 'prompt_tokens_details': {'cached_tokens': 3}}):
            c = self.conv(); r = response(); r['usage'] = usage
            with patch.object(c, '_post', return_value=r) as post:
                with self.assertRaisesRegex(SeatFailed, 'usage'):
                    asyncio.run(c.send('turn'))
                self.assertEqual(post.call_count, 1)

    def test_nested_player_schema(self):
        schema = player_schema(['Alex'])
        action = {'say': '', 'whispers': [], 'nominate': None, 'vote': None, 'targets': [], 'slayer_shot': None}
        self.assertTrue(nebius.matches(action, schema))
        self.assertFalse(nebius.matches({**action, 'targets': ['Unknown']}, schema))
        self.assertFalse(nebius.matches({**action, 'vote': 1}, schema))
        self.assertFalse(nebius.matches({**action, 'whispers': [{'to': ['Alex'], 'text': 3}]}, schema))

    def test_ceiling_and_refusal_retries_are_billed(self):
        nebius.MODELS[MODEL]['reasoning_effort'] = True
        for stop in ('length', 'content_filter'):
            c = self.conv()
            with patch.object(c, '_post', return_value=response(stop=stop)) as post:
                self.assertEqual(asyncio.run(c.send('turn')).action, {})
                self.assertEqual(post.call_count, 2)
                if stop == 'length':
                    self.assertEqual(post.call_args.args[0]['reasoning_effort'], 'low')
            self.assertEqual(c.messages[-1]['content'], 'turn')
        self.assertAlmostEqual(self.ledger.spent, 4 * .00014)

    def test_http_retries_and_redacted_permanent_errors(self):
        def err(code):
            return urllib.error.HTTPError('https://example.test', code, 'error', {}, io.BytesIO(b'test-key'))
        c = self.conv()
        with patch.object(c, '_post', side_effect=[err(429), err(503), response()]), patch(
                'clocktower.nebius.asyncio.sleep', new_callable=AsyncMock) as sleep:
            asyncio.run(c.send('turn'))
            self.assertEqual(sleep.await_count, 2)
        for code in (400, 401, 403):
            with patch.object(c, '_post', side_effect=err(code)) as post:
                with self.assertRaises(SeatFailed) as caught:
                    asyncio.run(c.send('turn'))
                self.assertNotIn('test-key', str(caught.exception))
                self.assertEqual(post.call_count, 1)
        with patch.object(c, '_post', side_effect=[err(402), response()]), patch(
                'clocktower.nebius.asyncio.sleep', new_callable=AsyncMock) as sleep:
            asyncio.run(c.send('turn'))
            sleep.assert_awaited_once_with(30)

    def test_config_and_endpoint_fail_before_a_call(self):
        path = self.root / 'models.json'
        path.write_text(json.dumps({MODEL: SETTINGS}))
        nebius.configure(path)
        with patch.dict(os.environ, {'TOKENFACTORY_BASE_URL': 'https://inference.example.test/v1/'}):
            self.assertEqual(self.conv().url, 'https://inference.example.test/v1/chat/completions')
        with patch.dict(os.environ, {'TOKENFACTORY_BASE_URL': 'https://secret@example.test/v1'}):
            with self.assertRaises(ValueError): self.conv()
        for bad in ({}, {**SETTINGS, 'prices': {'input': 1}}, {**SETTINGS, 'api_key': 'bad'}):
            path.write_text(json.dumps({MODEL: bad}))
            with self.assertRaises(ValueError): nebius.configure(path)
        with self.assertRaises(ValueError):
            nebius.model_spec('nebius:unknown')

    def test_memory_check_requires_no_anthropic_key(self):
        with patch.object(nebius.NebiusConversation, '_post', return_value=response()), patch(
                'clocktower.llm.make_client', side_effect=AssertionError('unrelated client')):
            results = asyncio.run(memory_run(MODEL, 1, 'high', self.root / 'memory-ledger'))
        self.assertEqual(len(results), 2)

    def test_full_game_resume_replay_analysis_and_seat_overrides(self):
        path = self.root / 'models.json'; path.write_text(json.dumps({MODEL: SETTINGS}))
        actions = {}
        original = ModelSeat.act

        async def act(seat, text, request):
            names = seat.conversation.schema['properties']['targets']['items']['enum']
            seed = int(hashlib.sha256(text.encode()).hexdigest(), 16)
            bot = RandomSeat(seat.conversation.label, random.Random(seed), names)
            actions[(seat.conversation.label, text)] = (await bot.act(text, request)).action
            return await original(seat, text, request)

        def post(c, body):
            text = body['messages'][-1]['content']
            action = actions.get((c.label, text))
            if action is None:  # belief probe: same fixed schema as game actions
                names = c.schema['properties']['targets']['items']['enum']
                action = {'say': '\n'.join(f'{n} | 30% | 10% | Chef' for n in names),
                          'whispers': [], 'nominate': None, 'vote': None, 'targets': [], 'slayer_shot': None}
            return response(action)

        def play(folder):
            cli.main(['run', '--model', MODEL, '--seed', '7', '--out', str(self.root / folder),
                      '--nebius-config', str(path), '--ledger', str(self.root / 'game-ledger'),
                      '--seat-model', f'good:{MODEL}:2', '--budget', '5'])

        with patch.object(ModelSeat, 'act', act), patch.object(nebius.NebiusConversation, '_post', post), patch(
                'clocktower.cli.make_client', side_effect=AssertionError('unrelated client')), redirect_stdout(io.StringIO()):
            play('whole')
            night = Game.night
            async def crash(game, n):
                if n == 2:
                    raise RuntimeError('injected interruption')
                return await night(game, n)
            with patch.object(Game, 'night', crash), self.assertRaisesRegex(RuntimeError, 'injected'):
                play('resumed')
            checkpoint = self.root / 'resumed/checkpoint.pkl'
            self.assertTrue(checkpoint.exists())
            original_config = (self.root / 'resumed/config.json').read_text()
            path.write_text(json.dumps({MODEL: {**SETTINGS, 'memory': 'reasoning', 'reasoning_field': 'reasoning'}}))
            with self.assertRaisesRegex(ValueError, 'Resume configuration'):
                play('resumed')
            self.assertEqual((self.root / 'resumed/config.json').read_text(), original_config)
            path.write_text(json.dumps({MODEL: SETTINGS}))
            play('resumed')
            cli.main(['replay', str(self.root / 'resumed'), '--view', 'public'])
            other = 'nebius:other/model'
            path.write_text(json.dumps({MODEL: SETTINGS, other: SETTINGS}))
            cli.main(['run', '--good-model', MODEL, '--evil-model', other, '--seed', '7', '--swap',
                      '--out', str(self.root / 'pair-test'), '--nebius-config', str(path),
                      '--ledger', str(self.root / 'game-ledger')])
            models = self.root / 'rating-models.json'
            models.write_text(json.dumps([{'id': m, 'released': '2026-01-01', 'lab': 'test'} for m in (MODEL, other)]))
            cli.main(['ratings', '--games', str(self.root), '--batches', 'pair-*', '--models', str(models),
                      '--out', str(self.root / 'ratings.json'), '--boot', '5', '--no-page'])
            rated = json.loads((self.root / 'ratings.json').read_text())
            self.assertEqual(rated['games'], 2)
            self.assertEqual(rated['trend_per_year'], {})
        def events(folder):
            return [(e['day'], e['kind'], e['data']) for e in map(json.loads,
                    (self.root / folder / 'events.jsonl').read_text().splitlines())]
        self.assertEqual(events('whole'), events('resumed'))
        self.assertFalse(checkpoint.exists())
        for conv in (self.root / 'whole/conversations').glob('*.json'):
            self.assertEqual(json.loads(conv.read_text()),
                             json.loads((self.root / 'resumed/conversations' / conv.name).read_text()))
        result = json.loads((self.root / 'resumed/result.json').read_text())
        self.assertIn(result['winner'], ('good', 'evil'))
        self.assertGreater(result['calls'], 100)
        self.assertGreater(result['cost'], 0)
        self.assertIsNotNone(game_metrics(self.root / 'resumed')['demon'])
        self.assertTrue((self.root / 'resumed/conversations').is_dir())

    def test_trimming_keeps_setup_and_does_not_change_the_live_seat_for_a_probe(self):
        c = self.conv()
        c.messages += [{'role': role, 'content': text} for role, text in (
            ('user', 'setup'), ('assistant', '{"answer":"ok"}'),
            ('user', 'old'), ('assistant', 'x' * 1000), ('user', 'recent'), ('assistant', '{"answer":"ok"}'))]
        c.last_input = 100000
        before = copy.deepcopy(c.messages)
        with patch.object(c, '_post', return_value=response()) as post:
            asyncio.run(c.send('probe', commit=False))
            self.assertEqual(c.messages, before)
            self.assertEqual(c.trimmed, 0)
            sent = post.call_args.args[0]['messages']
            self.assertEqual(sent[1]['content'], 'setup')
            self.assertNotIn('old', [m['content'] for m in sent])
            asyncio.run(c.send('next'))
            self.assertEqual(c.trimmed, 1)

    def test_generated_plan_names_keep_prefixed_ids_out_of_paths(self):
        plan = tournament.make_plan([MODEL, 'nebius:other/model'])
        match = plan['matchups'][0]
        self.assertNotIn('/', match['name'])
        self.assertEqual(match['good'], MODEL)
        self.assertEqual(len(list(tournament.commands(plan, per_game=True))), 2)

    def test_plan_passes_configuration_and_does_not_upload_with_token(self):
        plan = {'seed': 10, 'out': str(self.root / 'experiment'), 'nebius_config': 'models.json',
                'ledger': 'spend.jsonl', 'matchups': [{'name': 'a-v-b', 'good': MODEL, 'evil': MODEL, 'pairs': 1}]}
        path = self.root / 'plan.json'; path.write_text(json.dumps(plan))
        cmd = list(tournament.commands(plan))[0][1]
        self.assertIn('--nebius-config', cmd)
        self.assertIn('--ledger', cmd)
        self.assertIn('--swap', cmd)
        with patch.dict(os.environ, {'HF_TOKEN': 'test'}), patch.object(tournament, 'upload') as upload, patch(
                'clocktower.tournament.subprocess.Popen') as proc, redirect_stdout(io.StringIO()):
            proc.return_value.wait.return_value = 0
            self.assertEqual(tournament.run(path), 0)
            upload.assert_not_called()
        from clocktower.ratings import main
        with self.assertRaisesRegex(ValueError, 'Custom batches'):
            main(batches=['experiment-*'])
