"""Extract per-game facts for the lab comparison (no API calls).
Run from the repo root: python docs/findings/why-openai-wins/scripts/extract.py OUT.json
Games: v16 f2 (minus Opus 4.8's v16 games, as scripts/ratings2026.py) + v17 f3, f3b, f4, f5."""
import json, glob, os, re, sys, collections

OUT = sys.argv[1]
SKEPTIC = re.compile(r"(doesn'?t|does not|didn'?t|isn'?t|is not|not) (prove|confirm|clear|mean)|not proof|"
                     r"\bspy\b|\bdrunk\b|poison|red herring|independent|misregister|could be lying|unverif|can'?t (be )?verif", re.I)
CLAIM = re.compile(r"\bI(?:'m| am)(?: the| a| an)? (Washerwoman|Librarian|Investigator|Chef|Empath|Fortune Teller|"
                   r"Undertaker|Monk|Ravenkeeper|Virgin|Slayer|Soldier|Mayor|Butler|Drunk|Recluse|Saint)\b", re.I)
ASKFIRST = re.compile(r"(your (role|claim|character)|what are you|what('?s| is) your|claim first|before I (share|tell|give))", re.I)

def lab(m): return 'Anthropic' if m.startswith('claude') else 'OpenAI'
def words(t): return len(t.split())

def games():
    ds = (glob.glob('games/v16/f2-*/game-*') + glob.glob('games/v17/f3*-*/game-*') +
          glob.glob('games/v17/f4-*/game-*') + glob.glob('games/v17/f5-*/game-*'))
    for d in sorted(ds):
        if not os.path.exists(d + '/result.json') or not os.path.exists(d + '/events.jsonl'): continue
        r = json.load(open(d + '/result.json')); c = json.load(open(d + '/config.json'))
        if r.get('winner') not in ('good', 'evil'): continue
        if '/v16/' in d and 'claude-opus-4-8' in (c['good_model'], c['evil_model']): continue
        yield d, r, c

rows = []
for d, res, cfg in games():
    ev = [json.loads(l) for l in open(d + '/events.jsonl')]
    setup = next(e for e in ev if e['kind'] == 'setup')
    char = {s['name']: s['character'] for s in setup['data']['seats']}
    EVIL = {'Imp', 'Poisoner', 'Spy', 'Baron', 'Scarlet Woman'}
    evil = {n: ch in EVIL for n, ch in char.items()}
    names = list(char)
    g = dict(dir=d, pair=d.split('/')[2] + '/' + d.split('/')[3].replace('-swap', ''), good=cfg['good_model'],
             evil=cfg['evil_model'], glab=lab(cfg['good_model']), elab=lab(cfg['evil_model']),
             goodwin=res['winner'] == 'good', reason=res.get('reason'), days=res.get('days'), players=len(names),
             minion=cfg.get('minion'), char=char, evilset=[n for n in names if evil[n]])
    # --- public talk, whispers, nominations, votes, executions
    say, wh, noms, execs, votes = [], [], [], [], []
    seg = None; cur_nom = None; tally = None
    sq_turns = collections.Counter()  # (day, beat) -> number of say events in a square beat
    for e in ev:
        k, dt = e['kind'], e.get('data', {})
        if k == 'header':
            seg = dt['text']
        elif k == 'say':
            sp = dt['speaker']
            insq = seg and 'town square' in seg and e['phase'] == 'day'
            say.append(dict(day=e['day'], who=sp, ev=evil[sp], w=words(dt['text']), sq=bool(insq),
                            dead=sp in e.get('dead', []), t=dt['text']))
        elif k == 'whisper':
            f = dt['from']
            wh.append(dict(day=e['day'], f=f, ev=evil[f], to=dt['to'], w=words(dt['text']), t=dt['text'],
                           beat=seg))
        elif k == 'nomination':
            cur_nom = dict(day=e['day'], nr=dt['nominator'], ne=dt['nominee'], nr_ev=evil[dt['nominator']],
                           ne_ev=evil[dt['nominee']], ne_char=char[dt['nominee']], votes=None, needed=None,
                           outcome=None, w=words(dt.get('text', '')), t=dt.get('text', ''))
            noms.append(cur_nom); tally = 0; order = 0
        elif k == 'vote':
            v = dt['voter']
            if cur_nom is None or dt['nominee'] != cur_nom['ne']: continue
            votes.append(dict(day=e['day'], voter=v, v_ev=evil[v], ne=dt['nominee'], ne_ev=evil[dt['nominee']],
                              yes=bool(dt['yes']), ghost=bool(dt.get('ghost')), prior_yes=tally, order=order,
                              nr_ev=cur_nom['nr_ev']))
            order += 1
            if dt['yes']: tally += 1
        elif k == 'vote_result':
            if cur_nom is not None and dt['nominee'] == cur_nom['ne']:
                cur_nom.update(votes=dt['votes'], needed=dt['needed'], outcome=dt['outcome'])
                for v in votes:
                    if v['day'] == cur_nom['day'] and v['ne'] == cur_nom['ne'] and 'needed' not in v:
                        v['needed'] = dt['needed']
        elif k == 'execution':
            p = dt['player']
            nr = next((n['nr'] for n in reversed(noms) if n['ne'] == p and n['day'] == e['day']), None)
            execs.append(dict(day=e['day'], p=p, ev=evil[p], char=char[p], nr=nr, nr_ev=evil.get(nr),
                              died=dt.get('died', True)))
    g['kills'] = [dict(day=e['day'], p=e['data']['player'], char=char[e['data']['player']])
                  for e in ev if e['kind'] == 'death' and e['data'].get('cause') == 'demon']
    g.update(say=[{k: v for k, v in s.items() if k != 't'} for s in say], noms=noms, execs=execs, votes=votes)
    # text-derived per-side stats
    def side_text(isevil):
        S = [s for s in say if s['ev'] == isevil]; W = [w for w in wh if w['ev'] == isevil]
        out = dict(n_say=len(S), say_words=sum(s['w'] for s in S), n_wh=len(W), wh_words=sum(w['w'] for w in W),
                   sq_say=sum(s['sq'] for s in S),
                   skeptic=sum(bool(SKEPTIC.search(s['t'])) for s in S) + sum(bool(SKEPTIC.search(w['t'])) for w in W),
                   n_text=len(S) + len(W), wh_two=sum(len(w['to']) >= 2 for w in W))
        return out
    g['text_good'] = side_text(False); g['text_evil'] = side_text(True)
    nalive_turns = 0
    # square turns available: every player (alive or dead) gets one turn per square beat
    sq_beats = sum(1 for e in ev if e['kind'] == 'header' and 'town square' in e['data']['text'])
    g['sq_slots_good'] = sq_beats * sum(not evil[n] for n in names)
    g['sq_slots_evil'] = sq_beats * sum(evil[n] for n in names)
    # day-1 public claims by good seats; first whisper reveals own role; asks for claim
    d1claim = {n for s in say if s['day'] == 1 and not s['ev'] and s['sq'] for n in [s['who']] if CLAIM.search(s['t'])}
    g['d1_public_claim_good'] = len(d1claim) / max(1, sum(not evil[n] for n in names))
    first = {}
    for w in wh:
        if w['day'] == 1 and not w['ev'] and w['f'] not in first: first[w['f']] = w['t']
    g['firstwh_claim_good'] = sum(bool(CLAIM.search(t)) for t in first.values()) / max(1, len(first))
    g['d1_askfirst_good'] = sum(bool(ASKFIRST.search(w['t'])) for w in wh if w['day'] == 1 and not w['ev']) / \
        max(1, sum(1 for w in wh if w['day'] == 1 and not w['ev']))
    # mentions for belief-shift analysis: per (day, receiver, target): whispers received from evil / good naming target
    ment = collections.defaultdict(lambda: [0, 0, 0, 0])  # evil-whisper, good-whisper, evil-public, good-public
    for w in wh:
        for n in names:
            if n != w['f'] and re.search(r'\b' + n + r'\b', w['t']):
                for rcv in w['to']:
                    ment[(w['day'], rcv, n)][0 if w['ev'] else 1] += 1
    pubm = collections.defaultdict(lambda: [0, 0])
    for s in say:
        if not s['sq']: continue
        for n in names:
            if n != s['who'] and re.search(r'\b' + n + r'\b', s['t']):
                pubm[(s['day'], n)][0 if s['ev'] else 1] += 1
    # --- probes
    pr = []
    if os.path.exists(d + '/probes.jsonl'):
        for l in open(d + '/probes.jsonl'):
            p = json.loads(l)
            # Failed replies still identify living seats, needed to locate the current Demon after a transfer.
            pr.append(dict(day=p['day'], pt=p['point'], who=p['player'], team=p['team'], alive=p['alive'],
                           claim=(p.get('claim') or ''), P={k: [v.get('evil'), v.get('demon'), v.get('character')]
                                                             for k, v in p.get('players', {}).items()},
                           truth={k: [t['character'], t['evil']] for k, t in p['truth'].items()}))
    # belief-shift rows (good seats, dawn -> nominations same day)
    shift = []
    byk = {(p['day'], p['pt'], p['who']): p for p in pr}
    for (day, pt, who), q in byk.items():
        if pt != 'nominations' or q['team'] != 'good': continue
        p = byk.get((day, 'dawn', who)) or byk.get((day - 1, 'nominations', who))  # previous probe of this seat
        if not p: continue
        for n, (pe, pd, _) in p['P'].items():
            if n == who or n not in q['P'] or pe is None or q['P'][n][0] is None: continue
            m = ment.get((day, who, n), [0, 0, 0, 0]); pm = pubm.get((day, n), [0, 0])
            shift.append([day, who, n, q['truth'][n][1], pe, q['P'][n][0], m[0], m[1], pm[0], pm[1]])
    g['probes'] = pr; g['shift'] = shift
    # --- calls: tokens, ceiling hits, lost turns, by side
    cs = {'good': collections.Counter(), 'evil': collections.Counter()}
    if os.path.exists(d + '/calls.jsonl'):
        last = {}
        for l in open(d + '/calls.jsonl'):
            c = json.loads(l)
            if c['label'] not in evil: continue
            side = 'evil' if evil[c['label']] else 'good'
            u = c.get('usage', {}); thk = (u.get('output_tokens_details') or {}).get('thinking_tokens', 0) or 0
            K = cs[side]; probe = c['kind'] == 'probe'
            pre = 'p_' if probe else ''
            K[pre + 'calls'] += 1; K[pre + 'out'] += u.get('output_tokens', 0); K[pre + 'think'] += thk
            if c['stop'] in ('max_tokens', 'incomplete'):
                K[pre + 'ceiling'] += 1
                if c.get('attempt', 0) >= 1 and c.get('effort') == 'low': K[pre + 'lost'] += 1
            if c.get('attempt', 0) == 0: K[pre + 'turns'] += 1
            if not probe and c.get('attempt', 0) == 0 and c['kind'] == 'beat':
                K['beat_turns'] += 1
                try:
                    o = json.loads(c['output'])
                    if not o.get('say') and not o.get('whispers'): K['beat_pass'] += 1
                except Exception:
                    pass
    g['calls'] = {k: dict(v) for k, v in cs.items()}
    # thinking tokens on each vote turn (k-th vote call of a voter on a day <-> k-th vote event), last attempt
    vt = collections.defaultdict(list)
    if os.path.exists(d + '/calls.jsonl'):
        for l in open(d + '/calls.jsonl'):
            c = json.loads(l)
            if c['kind'] != 'vote': continue
            t = (c.get('usage', {}).get('output_tokens_details') or {}).get('thinking_tokens', 0) or 0
            key = (c['day'], c['label'])
            if c.get('attempt', 0) == 0: vt[key].append(t)
            elif vt[key]: vt[key][-1] = t
    seen = collections.Counter()
    for v in votes:
        key = (v['day'], v['voter']); i = seen[key]; seen[key] += 1
        v['think'] = vt[key][i] if i < len(vt[key]) else None
    # zero-thinking share by side and turn kind
    zt = {'good': collections.Counter(), 'evil': collections.Counter()}
    if os.path.exists(d + '/calls.jsonl'):
        for l in open(d + '/calls.jsonl'):
            c = json.loads(l)
            if c['label'] not in evil or c.get('attempt', 0) != 0 or c['kind'] == 'probe': continue
            t = (c.get('usage', {}).get('output_tokens_details') or {}).get('thinking_tokens', 0) or 0
            side = 'evil' if evil[c['label']] else 'good'
            zt[side][c['kind'] + '_n'] += 1; zt[side][c['kind'] + '_zero'] += (t == 0)
    g['zero'] = {k: dict(v) for k, v in zt.items()}
    rows.append(g)
json.dump(rows, open(OUT, 'w'))
print(len(rows), 'games')
