"""The multiplayer table (web.py): lobby rules, seat assignment, whole games played by people through the
API against practice bots, persistence across a restart, spend caps, own-key handling, rate limits and the
HTTP layer. No API calls: AI seats are `random` bots and costs are faked."""
import argparse
import json
import os
import random
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import clocktower.web as web
from clocktower.llm import BudgetExceeded
from clocktower.state import Grimoire
from clocktower.web import Hub, PlayLedger, RateLimited, Settings, Table, assign, clean, evil_seats, redact, run_game

# No test may reach a paid API: the house keys are taken out of the environment for this module (a test
# that needs the server to "have" a key patches in a fake one, with a runner that plays no model).
_SAVED = {}


def setUpModule():
    for k in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'MY_ANTHROPIC_API_KEY', 'OPENROUTER_API_KEY'):
        if k in os.environ:
            _SAVED[k] = os.environ.pop(k)


def tearDownModule():
    os.environ.update(_SAVED)


KEY = 'sk-ant-api03-' + 'Zq7' * 20  # a fake own key: it must never reach a file


def args(out=None, model='random'):
    return argparse.Namespace(model=model, good_model=None, evil_model=None, seed=7, private_beats=6,
                              public_beats=3, waves=3, days=15, effort='high', good_effort=None, evil_effort=None,
                              max_output=8000, flex=False, concurrency=0, workspace='x', out=out, cap=None,
                              minions=None, players='10', host='127.0.0.1', port=0)


def wait(cond, timeout=60, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def play(t, tokens, rng, until=lambda: False, timeout=90):
    """Answer every pending turn for these people until the game ends or `until()`; returns turns taken."""
    seen, turns, end = {tok: 0 for tok in tokens}, 0, time.time() + timeout
    while t.status == 'playing' and time.time() < end and not until():
        for tok in tokens:
            s = t.state(tok, seen[tok])
            if not s.get('seated'):
                continue
            seen[tok] = s['next']
            for ev in s['events']:  # a seat never sees another's night info or a private whisper
                assert not ev['private'] or ev['kind'] in ('intro', 'info', 'notice', 'whisper'), ev
            p = s['pending']
            if p:
                act = ({'vote': False} if p['kind'] == 'vote' else  # random targets: an Imp stuck on one dead player never ends
                       {'targets': rng.sample(p['options'], min(p['n_targets'], len(p['options'])))})
                if p['kind'] == 'beat' and p['mode'] == 'private':
                    other = next(x['name'] for x in s['players'] if x['name'] != s['me']['seat'])
                    act = {'whispers': [{'to': [other], 'text': 'hello <b>there</b>'}]}
                try:
                    t.act(tok, {'turn': p['turn'], 'action': act})
                    turns += 1
                except ValueError:
                    pass  # the turn passed meanwhile
        time.sleep(0.01)
    return turns


class LobbyTest(unittest.TestCase):
    def test_assign_respects_teams(self):
        chars = ['Imp', 'Poisoner', 'Chef', 'Empath', 'Monk', 'Saint', 'Soldier']
        g = Grimoire([f'P{i}' for i in range(7)], chars)
        for k in range(30):
            seats = assign([('a', 'evil'), ('b', 'good'), ('c', 'any'), ('d', 'evil')], g.players, random.Random(k))
            self.assertEqual(len(set(seats.values())), 4)
            self.assertTrue(g[seats['a']].evil and g[seats['d']].evil)
            self.assertFalse(g[seats['b']].evil)

    def test_problems_count_only(self):
        t = Table(args())
        host = t.join('Host', 'evil', t.host_key)
        t.set_config(host, {'players': 10})
        for i in range(evil_seats(10)):
            t.join(f'E{i}', 'evil')
        self.assertIn('Too many people chose evil', t.problem())
        guest = t.join('Guest', 'good')
        with self.assertRaises(PermissionError):
            t.set_config(guest, {'players': 12})
        t.claim_host(guest)  # anyone may take the host's chair
        t.set_config(guest, {'players': 12})
        lobby = t.lobby(guest)
        self.assertNotIn('team', str(lobby['people']))
        self.assertNotIn(host, json.dumps(lobby))  # nobody sees another person's token (it would show their seat)

    def test_sizes_and_people(self):
        t = Table(args())
        a = t.join('A', 'any')
        for n in (9, 13, 5):
            with self.assertRaises(ValueError):
                t.set_config(a, {'players': n})
        for i in range(11):
            t.join(f'P{i}', 'any')
        with self.assertRaises(ValueError):
            t.join('Thirteenth', 'any')

    def test_input_cleaning(self):
        t = Table(args())
        tok = t.join('  <img src=x onerror=alert(1)>\u0000‮  long name that goes on and on ', 'good')
        name = t.people[tok]['name']
        self.assertLessEqual(len(name), web.NAME_LEN)
        self.assertNotIn('\u0000', name)
        with self.assertRaises(ValueError):
            t.join(' \u0000 ', 'good')
        a = clean({'say': 'x' * 10_000, 'whispers': [{'to': ['A', 'B', 'C', 'D'], 'text': 'hi\u0007'}, {'to': ['E']}],
                   'vote': 'yes', 'targets': 'Alex', 'nominate': ['x']})
        self.assertEqual(len(a['say']), web.TEXT_LEN)
        self.assertEqual(a['whispers'], [{'to': ['A', 'B', 'C'], 'text': 'hi'}])
        self.assertIsNone(a['vote'])
        self.assertEqual(a['targets'], [])
        self.assertIsNone(a['nominate'])
        self.assertEqual(clean('nonsense')['whispers'], [])


class GameTest(unittest.TestCase):
    def test_two_people_play_a_whole_game(self):
        with tempfile.TemporaryDirectory() as d:
            t = Table(args(d))
            a = t.join('Ann', 'good', t.host_key)
            b = t.join('Bo', 'evil')
            t.start(a)
            turns = play(t, [a, b], random.Random(3))
            self.assertEqual(t.status, 'over', t.error)
            final = t.state(a, 0)
            self.assertTrue(final['reveal']['winner'] in ('good', 'evil'))
            people = {s['player'] for s in final['reveal']['seats'] if s['player']}
            self.assertEqual(people, {'Ann', 'Bo'})
            by = {s['player']: s for s in final['reveal']['seats'] if s['player']}
            self.assertFalse(by['Ann']['evil'])
            self.assertTrue(by['Bo']['evil'])
            self.assertGreater(turns, 10)
            self.assertIn('Night 1', t.transcript(a))
            self.assertEqual(len(final['players']), 10)


class StallTest(unittest.TestCase):
    def test_absent_player_and_host_end(self):
        """A player whose page never checks in passes (no stall); the host can end the game early."""
        with tempfile.TemporaryDirectory() as d:
            old, web.AWAY = web.AWAY, 1
            try:
                t = Table(args(d))
                a = t.join('Ann', 'good')
                t.join('Ghost', 'evil')  # never polls
                t.start(a)
                deadline, turns = time.time() + 60, 0
                while t.status == 'playing' and time.time() < deadline and turns < 30:
                    s = t.state(a, 0)
                    p = s.get('pending')
                    if p:
                        t.act(a, {'turn': p['turn'], 'action': {'vote': False} if p['kind'] == 'vote' else {}})
                        turns += 1
                    time.sleep(0.02)
                self.assertGreaterEqual(turns, 30, 'the absent player stalled the game')
                t.end(a)
                self.assertTrue(wait(lambda: t.status == 'over', 30))
                self.assertEqual(t.state(a, 0)['reveal']['reason'], 'The host ended the game.')
            finally:
                web.AWAY = old


def hub(d, runner=None, model='random', **kw):
    s = Settings(data_dir=Path(d), **{'probes': False, **kw})
    return Hub(args(model=model), s, runner=runner, check_key=lambda prov, key: None)


class PersistenceTest(unittest.TestCase):
    def test_game_survives_a_restart(self):
        """Tables, people and the game come back after a restart; the game continues from its last dusk
        and the same links still work."""
        with tempfile.TemporaryDirectory() as d:
            h1 = hub(d)
            t = h1.create('1.2.3.4')
            a, b = t.join('Ann', 'good'), t.join('Bo', 'any')
            t.start(a)
            rng = random.Random(5)
            # play into day 2, so the game has a checkpoint from a dusk after night 1
            play(t, [a, b], rng, until=lambda: t.game and t.game.day >= 2 and t.game.phase not in ('setup', 'night'))
            self.assertEqual(t.status, 'playing', t.error)
            seat_a, seat_b = t.people[a]['seat'], t.people[b]['seat']
            before = t.state(a, 0)
            self.assertTrue(before['seated'])
            out = Path(t.out)
            self.assertTrue((out / 'checkpoint.pkl').exists())

            # the process dies: halt the old game thread (in a real restart it is simply gone) without
            # letting it record an ending, then clear the halt so the checkpoint can resume
            h1.shutdown()
            (out / 'STOP').touch()
            for seat in list(t.seats.values()):
                seat.submit({})
            self.assertTrue(wait(lambda: not any(th.name == f'game-{t.id}' and th.is_alive() for th in threading.enumerate()), 30))
            (out / 'STOP').unlink()
            h1.store.db.close()

            h2 = hub(d).restore()
            t2 = h2.get(t.id)
            self.assertEqual(t2.status, 'playing')
            self.assertEqual(t2.run, 2)
            self.assertTrue(wait(lambda: t2.game is not None and t2.game.g is not None, 30))
            self.assertEqual(t2.people[a]['seat'], seat_a)  # same seats, same tokens
            self.assertEqual(t2.people[b]['seat'], seat_b)
            after = t2.state(a, 0)
            self.assertEqual(after['me']['character'], before['me']['character'])
            self.assertEqual(after['events'][0]['text'], before['events'][0]['text'])  # history kept
            play(t2, [a, b], rng)
            self.assertEqual(t2.status, 'over', t2.error)
            self.assertIn(t2.state(b, 0)['reveal']['winner'], ('good', 'evil'))
            h2.store.db.close()

            # a finished game still shows after another restart (from the saved reveal and the logs)
            h3 = hub(d).restore()
            t3 = h3.get(t.id)
            s = t3.state(a, 0)
            self.assertTrue(s['over'] and s['seated'])
            self.assertEqual({x['player'] for x in s['reveal']['seats'] if x['player']}, {'Ann', 'Bo'})
            self.assertGreater(len(s['events']), 50)
            self.assertIn('Night 1', t3.transcript(b))

    def test_own_key_game_pauses_after_restart(self):
        with tempfile.TemporaryDirectory() as d:
            h1 = hub(d, runner=lambda table, cfg: time.sleep(3600))
            t = h1.create()
            a = t.join('Ann', 'good')
            t.set_key(a, 'anthropic', KEY)
            t.set_config(a, {'good_model': 'claude-opus-5-5', 'evil_model': 'claude-opus-5-5'})
            t.start(a)
            self.assertEqual(t.paid_by, 'player')
            h1.store.db.close()
            h2 = hub(d).restore()
            t2 = h2.get(t.id)
            self.assertEqual(t2.status, 'paused')
            self.assertIn('API key again', t2.lobby(a)['error'])
            with self.assertRaises(ValueError):
                t2.resume(a)  # no key yet
            self.assertEqual(t2.keys, {})


class SpendTest(unittest.TestCase):
    def test_free_budget(self):
        """Free games reserve their whole per-game cap, so the day's spend can't pass the daily cap; own-key
        games ignore the free budget but not the cap on games at once."""
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-fake-house-key-000000000000'}):
            h = hub(d, runner=lambda table, cfg: time.sleep(3600), model=None, daily_cap=10.0, game_cap=4.0, max_games=3)
            t1, t2, t3, t4 = (h.create(ip) for ip in 'abcd')
            a, b, c, e = (t.join('P', 'good') for t in (t1, t2, t3, t4))
            self.assertEqual(t1.config['good_model'], 'gpt-6.1-sol')  # the free model by default
            self.assertEqual(t1.payer()[0], 'house')
            with self.assertRaises(ValueError):  # other models need the host's own key
                t1.set_config(a, {'good_model': 'claude-opus-5-5'})
            h.store.add_spend('old', 'g0', 1.0, 'house')    # spent earlier today
            h.store.add_spend('old', 'g0', 100.0, 'player')  # own keys don't count
            h.store.add_spend('old', 'g0', 50.0, 'house')
            h.store.db.execute('UPDATE spend SET time = time - 2 * 86400 WHERE cost = 50.0')  # an earlier day
            t1.start(a)  # 1 + 4 <= 10
            t2.start(b)  # 1 + 4 + 4 <= 10
            with self.assertRaisesRegex(ValueError, 'fully booked'):
                t3.start(c)  # 1 + 8 + 4 > 10
            t1.status = 'over'  # a free game ends: its reserve is released
            t3.start(c)
            t4.set_key(e, 'openai', 'sk-proj-' + 'k' * 40)
            self.assertEqual(t4.payer()[0], 'player')
            t1.status = 'playing'
            with self.assertRaisesRegex(ValueError, 'game slots'):
                t4.start(e)
            t1.status = 'over'
            h.store.add_spend('x', 'y', 9.0, 'house')  # the day's budget is used up
            with self.assertRaisesRegex(ValueError, 'used up'):
                h.admit(t1, 'house')
            self.assertFalse(h.info()['free']['open'])
            t4.start(e)  # an own key still plays

    def test_game_cap(self):
        with tempfile.TemporaryDirectory() as d:
            h = hub(d)
            led = PlayLedger('t', 'g1', 'house', 4.0, h.store)
            led.check()
            led.add(2.5, label='Alex')
            led.add(1.6, label='Alex')
            with self.assertRaises(BudgetExceeded):
                led.check()
            self.assertAlmostEqual(PlayLedger('t', 'g1', 'house', 4.0, h.store).spent, 4.1)  # still counted after a restart
            self.assertAlmostEqual(h.spent_today(), 4.1)
            t = h.create()
            t.paid_by, t.result = 'house', {'stopped': 'budget: this game has spent $4.10 of its $4.00 cap'}
            self.assertEqual(t.reason(None), 'The AI budget for this game ($5) ran out.')


class KeyTest(unittest.TestCase):
    def test_own_key_never_written(self):
        """A host's key lives in memory only: not in the database, the logs, the lobby or any reply."""
        with tempfile.TemporaryDirectory() as d:
            def runner(table, cfg):  # the game runs on the host's key, played here by free bots
                return run_game(table, {**cfg, 'good_model': 'random', 'evil_model': 'random'})
            h = hub(d, runner=runner)
            t = h.create()
            a, b = t.join('Ann', 'good'), t.join('Bo', 'evil')
            with self.assertRaises(PermissionError):
                t.set_key(b, 'anthropic', KEY)  # only the host
            t.set_key(a, 'anthropic', KEY)
            with self.assertRaises(ValueError):
                t.set_key(a, 'anthropic', 'not a key')
            t.set_config(a, {'good_model': 'claude-opus-5-5', 'evil_model': 'claude-fable-5-1'})
            self.assertEqual(t.lobby(a)['keys'], ['anthropic'])
            self.assertEqual(t.lobby(b)['keys'], [])
            t.start(a)
            self.assertEqual(t.paid_by, 'player')
            client = t.client()
            self.assertEqual(client.api_key, KEY)
            self.assertNotIn('anthropic-workspace-id', client.default_headers)  # the house's workspace isn't theirs
            replies = [t.lobby(a), t.lobby(b)]
            play(t, [a, b], random.Random(2))
            self.assertEqual(t.status, 'over', t.error)
            replies += [t.state(a, 0), t.state(b, 0)]
            self.assertNotIn(KEY, json.dumps(replies))
            # a provider's error echoing a masked key is scrubbed from the logs
            ev = Path(t.out) / 'events.jsonl'
            ev.write_text(ev.read_text() + json.dumps({'error': f'Incorrect API key provided: {KEY[:8]}***{KEY[-4:]}'}) + '\n')
            t.scrub()
            h.shutdown()
            h.store.db.close()
            leaks = []
            for f in Path(d).rglob('*'):
                if f.is_file():
                    data = f.read_bytes()
                    if KEY.encode() in data or KEY[:8].encode() + b'***' in data or KEY[-12:].encode() in data:
                        leaks.append(str(f))
            self.assertEqual(leaks, [])
            self.assertIn('[key]', ev.read_text())

    def test_redact(self):
        self.assertEqual(redact(f'bad key {KEY} here', [KEY]), 'bad key [key] here')
        self.assertNotIn('sk-', redact('Incorrect API key provided: sk-proj-abc***wxyz.'))

    def test_payer_rules(self):
        with mock.patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-fake-house-key-000000000000'}):
            t = Table(args(model=None), hub=hub(tempfile.mkdtemp(), model=None))
            a = t.join('A', 'good')
            self.assertEqual(t.payer()[0], 'house')
            t.set_key(a, 'anthropic', KEY)
            t.set_config(a, {'good_model': 'claude-opus-5-5'})  # Claude on the key, GPT-6.1 Sol free: no mixing
            self.assertIn('your own keys', t.problem())
            t.set_config(a, {'evil_model': 'random'})
            self.assertEqual(t.payer()[0], 'player')
            t.set_key(a, 'anthropic', '')  # removing the key puts the table back on what it can play
            self.assertTrue(t.ready(t.config['good_model']))
            self.assertEqual(t.payer()[0], 'house')


class ReadsTest(unittest.TestCase):
    def test_what_the_ais_believed(self):
        with tempfile.TemporaryDirectory() as d:
            rows = [{'player': 'Alex', 'team': 'good', 'day': 1, 'players': {'Bo': {'evil': 0.2, 'demon': 0.1, 'character': 'Chef'}}},
                    {'player': 'Alex', 'team': 'good', 'day': 2, 'players': {'Bo': {'evil': 0.6, 'demon': 0.4, 'character': 'Imp'}}},
                    {'player': 'Cy', 'team': 'good', 'day': 2, 'players': {'Bo': {'evil': 0.4, 'demon': 0.2, 'character': None}}},
                    {'player': 'Dee', 'team': 'evil', 'day': 2, 'players': {'Bo': {'evil': 0.9, 'demon': 0.9, 'character': 'x'}}},
                    {'player': 'Bo', 'team': 'good', 'day': 2, 'players': {'Alex': {'evil': 1.0}}}]
            (Path(d) / 'probes.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows) + 'not json\n')
            r = web.reads(d, {'Bo'})
            self.assertEqual(set(r), {'Bo'})
            self.assertAlmostEqual(r['Bo']['evil'], 0.5)  # Alex's last read and Cy's; evil seats and humans skipped
            self.assertEqual([b['seat'] for b in r['Bo']['by']], ['Alex', 'Cy'])
            self.assertEqual(web.reads(d, set()), {})


class LimitsTest(unittest.TestCase):
    def test_table_creation_rate_limit(self):
        with tempfile.TemporaryDirectory() as d:
            h = hub(d, tables_per_hour=3, max_tables=5)
            for _ in range(3):
                h.create('9.9.9.9')
            with self.assertRaises(RateLimited):
                h.create('9.9.9.9')
            h.create('8.8.8.8')
            h.create('7.7.7.7')
            with self.assertRaisesRegex(ValueError, 'full'):
                h.create('6.6.6.6')

    def test_bucket(self):
        b = web.Bucket(rate=1, burst=3)
        self.assertEqual([b.allow('ip', 100.0) for _ in range(4)], [True, True, True, False])
        self.assertTrue(b.allow('ip', 101.5))
        self.assertTrue(b.allow('other', 101.5))

    def test_tokens_unguessable(self):
        t = Table(args())
        tokens = {t.join(f'P{i}', 'any') for i in range(10)}
        self.assertEqual(len(tokens), 10)
        self.assertTrue(all(len(x) >= 24 for x in tokens))
        self.assertGreaterEqual(len(t.id), 12)

    def test_idle_tables_close(self):
        with tempfile.TemporaryDirectory() as d:
            h = hub(d, runner=lambda table, cfg: time.sleep(3600), idle_minutes=1, keep_days=1)
            lobby, game, done = h.create('a'), h.create('b'), h.create('c')
            done.status, done.finished = 'over', time.time() - 2 * 86400
            log = Path(d) / 'old-game'
            log.mkdir()
            done.games = [str(log)]
            lobby.join('A', 'good')
            g = game.join('B', 'good')
            game.start(g)
            game.game = mock.Mock(out=Path(d), STOP='STOP')
            later = time.time() + 120
            h.sweep(later)
            self.assertNotIn(lobby.id, h.tables)
            self.assertEqual(game.ended, 'idle')  # a game nobody watches is told to stop (it stops spending)
            self.assertTrue((Path(d) / 'STOP').exists())
            h.sweep(later)
            self.assertNotIn(done.id, h.tables)
            self.assertFalse(log.exists())
            self.assertEqual([x['id'] for x in h.store.load_tables()], [game.id])


class HTTPTest(unittest.TestCase):
    """The real server on a free port: routes, headers, limits, and that the page escapes what people type."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.hub = hub(cls.dir.name)
        cls.server = web.serve(cls.hub, '127.0.0.1', 0, sweep_every=3600)
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.dir.cleanup()

    def call(self, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_flow(self):
        code, _, body = self.call('/healthz')
        self.assertEqual((code, json.loads(body)['ok']), (200, True))
        code, h, page = self.call('/')
        self.assertEqual(code, 200)
        self.assertIn("script-src 'self'", h['Content-Security-Policy'])
        self.assertEqual(h['X-Content-Type-Options'], 'nosniff')
        code, _, page2 = self.call('/t/abcdefgh1234')
        self.assertEqual(page, page2)
        code, _, body = self.call('/api/new', {})
        tid = json.loads(body)['table']
        code, _, body = self.call(f'/api/t/{tid}/join', {'name': '<b onclick=x>hi</b>', 'team': 'good'})
        token = json.loads(body)['token']
        code, _, body = self.call(f'/api/t/{tid}/lobby', headers={'X-Token': token})
        lobby = json.loads(body)
        self.assertTrue(lobby['me']['host'])
        self.assertEqual(lobby['people'][0]['name'], '<b onclick=x>hi</b>')  # JSON: the page escapes it
        self.assertTrue(lobby['links']['friends'].endswith(f'/t/{tid}'))
        self.assertEqual(self.call('/api/t/nosuchtable1/lobby')[0], 404)
        self.assertEqual(self.call(f'/api/t/{tid}/state', headers={'X-Token': 'guess'})[0], 403)
        self.assertEqual(self.call(f'/api/t/{tid}/config', {'players': 15}, {'X-Token': token})[0], 400)
        self.assertEqual(self.call(f'/api/t/{tid}/join', raw=b'x' * 40_000)[0], 413)
        self.assertEqual(self.call(f'/api/t/{tid}/join', raw=b'[1, 2]')[0], 400)
        self.assertEqual(self.call(f'/api/t/{tid}/me', {'name': 'x'}, {'X-Token': token, 'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.call(f'/api/t/{tid}/me', {'name': 'x'}, {'X-Token': token, 'Origin': self.base})[0], 200)
        self.assertEqual(self.call('/../web.py')[0], 404)
        js = self.call('/table.js')[2].decode()
        self.assertIn('const esc', js)

    def test_self_hosted_assets_and_strict_csp(self):
        """The page loads nothing from other hosts and allows no inline styles; its fonts come from table/fonts."""
        _, h, page = self.call('/')
        csp = h['Content-Security-Policy']
        self.assertNotIn('unsafe-inline', csp)
        self.assertNotIn('http', csp)  # no external host (fonts used to come from Google)
        self.assertIn("font-src 'self'", csp)
        self.assertNotIn(b'googleapis', page)
        self.assertNotIn(b' style="', page + self.call('/table.js')[2])  # inline styles would be blocked by the CSP
        for name, ctype in [('/fonts/inter.woff2', 'font/woff2'), ('/fonts/eb-garamond.woff2', 'font/woff2'), ('/fonts/pirata-one.woff', 'font/woff')]:
            code, h, body = self.call(name)
            self.assertEqual((code, h['Content-Type']), (200, ctype), name)
            self.assertGreater(len(body), 1000)
        css = self.call('/table.css')[2].decode()
        for url in re.findall(r'url\((/[^)]+)\)', css):  # every file the stylesheet asks for is served
            self.assertEqual(self.call(url)[0], 200, url)
        self.assertEqual(self.call('/fonts/OFL.txt')[0], 404)  # only the listed types are served

    def test_post_rate_limit(self):
        old, self.hub.posts = self.hub.posts, web.Bucket(1, 5)
        try:
            codes = [self.call('/api/t/nosuchtable1/join', {'name': 'a', 'team': 'good'})[0] for _ in range(8)]
            self.assertIn(429, codes)
        finally:
            self.hub.posts = old


if __name__ == '__main__':
    unittest.main()
