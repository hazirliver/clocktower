"""Lab comparison: per-game metrics, opponent-adjusted, bootstrap over side-swapped pairs.
python docs/findings/why-openai-wins/scripts/analyze.py GAMES.json [OUT.json]"""
import json, sys, collections, statistics as st
import numpy as np
from clocktower.probe import living_demon

R = json.load(open(sys.argv[1]))
OUT = sys.argv[2] if len(sys.argv) > 2 else None
SHORT = {'claude-opus-4-6': 'Opus 4.6', 'claude-opus-4-7': 'Opus 4.7', 'claude-opus-4-8': 'Opus 4.8',
         'claude-fable-5': 'Fable 5', 'claude-fable-5-1': 'Fable 5.1', 'claude-opus-5-5': 'Opus 5.5',
         'gpt-5.4': 'GPT-5.4', 'gpt-5.5': 'GPT-5.5', 'gpt-5.6-sol': 'GPT-5.6 Sol', 'gpt-6-astra': 'Astra',
         'gpt-6.1-sol': 'Sol 6.1'}
GROUPS = {
    'A all': lambda m: m.startswith('claude'),
    'O all': lambda m: not m.startswith('claude'),
    'A new (F5,F5.1,O5.5)': lambda m: m in ('claude-fable-5', 'claude-fable-5-1', 'claude-opus-5-5'),
    'O new (5.6,Astra,6.1)': lambda m: m in ('gpt-5.6-sol', 'gpt-6-astra', 'gpt-6.1-sol'),
    'O 5.6 Sol only': lambda m: m == 'gpt-5.6-sol',
    'O no GPT-6': lambda m: m in ('gpt-5.4', 'gpt-5.5', 'gpt-5.6-sol'),
    'A old (4.6-4.8)': lambda m: m in ('claude-opus-4-6', 'claude-opus-4-7', 'claude-opus-4-8'),
    'O old (5.4,5.5)': lambda m: m in ('gpt-5.4', 'gpt-5.5'),
}
CONTRASTS = [('A all', 'O all'), ('A new (F5,F5.1,O5.5)', 'O new (5.6,Astra,6.1)'),
             ('A new (F5,F5.1,O5.5)', 'O 5.6 Sol only'), ('A old (4.6-4.8)', 'O old (5.4,5.5)')]
UNCHECK = {'Saint', 'Mayor', 'Ravenkeeper', 'Virgin', 'Soldier', 'Monk', 'Undertaker', 'Slayer', 'Butler', 'Recluse', 'Drunk'}

def town(g, day, pt='nominations'):
    """Mean P(evil)/P(demon) over living good seats at a probe point, for living players (excluding self)."""
    ps = [p for p in g['probes'] if p['day'] == day and p['pt'] == pt and p['team'] == 'good' and p['alive']]
    if not ps: return None
    agg = collections.defaultdict(lambda: [[], []])
    for p in ps:
        for n, (pe, pd, _) in p['P'].items():
            if n == p['who'] or pe is None: continue
            agg[n][0].append(pe); agg[n][1].append(pd if pd is not None else 0)
    dead = set()
    for e in g['execs']:
        if e['day'] < day and e['died']: dead.add(e['p'])
    return {n: (st.mean(v[0]), st.mean(v[1])) for n, v in agg.items()}, ps

# ---- metrics: each returns (num, den) for one game, side given by the metric's 'side'
def m_win(g): return (g['goodwin'], 1)
def m_exec_evil(g):
    x = [e for e in g['execs'] if e['died']]; return (sum(e['ev'] for e in x), len(x))
def m_d1_exec_evil(g):
    x = [e for e in g['execs'] if e['died'] and e['day'] == 1]; return (sum(e['ev'] for e in x), len(x))
def m_good_exec(g): return (sum(not e['ev'] for e in g['execs'] if e['died']), 1)
def m_good_exec_uncheck(g): return (sum((not e['ev']) and e['char'] in UNCHECK for e in g['execs'] if e['died']), 1)
def m_saint(g): return (sum(e['char'] == 'Saint' for e in g['execs'] if e['died']), 1)
def m_yes_good(g):
    v = [x for x in g['votes'] if not x['v_ev'] and not x['ne_ev']]; return (sum(x['yes'] for x in v), len(v))
def m_yes_evil(g):
    v = [x for x in g['votes'] if not x['v_ev'] and x['ne_ev']]; return (sum(x['yes'] for x in v), len(v))
def herd(majority):
    def f(g):
        v = [x for x in g['votes'] if not x['v_ev'] and not x['ne_ev'] and x.get('needed') and x['order'] >= 3
             and ((x['prior_yes'] / x['order'] >= 0.5) == majority)]
        return (sum(x['yes'] for x in v), len(v))
    return f
def m_top_evil(g):
    n = d = 0
    for day in range(1, 16):
        t = town(g, day)
        if not t: continue
        T, ps = t
        alive = {p['who'] for p in g['probes'] if p['day'] == day and p['pt'] == 'nominations' and p['alive']}
        cand = {k: v for k, v in T.items() if k in alive}
        if not cand: continue
        top = max(cand, key=lambda k: cand[k][0])
        n += top in g['evilset']; d += 1
    return (n, d)
def top_from(g, lo, hi=99):
    n = d = 0
    for day in range(lo, min(hi, 15) + 1):
        t = town(g, day)
        if not t: continue
        T, ps = t
        alive = {p['who'] for p in g['probes'] if p['day'] == day and p['pt'] == 'nominations' and p['alive']}
        cand = {k: v for k, v in T.items() if k in alive}
        if not cand: continue
        top = max(cand, key=lambda k: cand[k][0]); n += top in g['evilset']; d += 1
    return (n, d)
def m_top_evil_d1(g):
    t = town(g, 1)
    if not t: return (0, 0)
    T, _ = t; top = max(T, key=lambda k: T[k][0]); return (top in g['evilset'], 1)
def m_exec_is_top(g):
    n = d = 0
    for e in g['execs']:
        if not e['died']: continue
        t = town(g, e['day'])
        if not t: continue
        T, _ = t
        alive = {p['who'] for p in g['probes'] if p['day'] == e['day'] and p['pt'] == 'nominations' and p['alive']}
        cand = {k: v for k, v in T.items() if k in alive} or T
        top = max(cand, key=lambda k: cand[k][0]); n += top == e['p']; d += 1
    return (n, d)
def exec_top_split(follow):
    def f(g):
        n = d = 0
        for e in g['execs']:
            if not e['died']: continue
            t = town(g, e['day'])
            if not t: continue
            T, _ = t
            alive = {p['who'] for p in g['probes'] if p['day'] == e['day'] and p['pt'] == 'nominations' and p['alive']}
            cand = {k: v for k, v in T.items() if k in alive} or T
            top = max(cand, key=lambda k: cand[k][0])
            if (top == e['p']) == follow: n += e['ev']; d += 1
        return (n, d)
    return f
def m_agree(g):
    """Share of living good seats whose own top P(evil) equals the town's top (consensus), nomination probes."""
    n = d = 0
    for day in range(1, 16):
        t = town(g, day)
        if not t: continue
        T, ps = t; top = max(T, key=lambda k: T[k][0])
        for p in ps:
            own = {k: v[0] for k, v in p['P'].items() if k != p['who'] and v[0] is not None}
            if own: n += max(own, key=own.get) == top; d += 1
    return (n, d)
def m_brier(g):
    s = c = 0
    for p in g['probes']:
        if p['team'] != 'good' or p['pt'] != 'nominations': continue
        for k, (pe, _, _) in p['P'].items():
            if k == p['who'] or pe is None: continue
            s += (pe - p['truth'][k][1]) ** 2; c += 1
    return (s, c)
def m_imp_d(day):
    def f(g):
        s = c = 0
        for p in g['probes']:
            if p['team'] != 'good' or p['pt'] != 'nominations' or p['day'] != day: continue
            alive = (q['who'] for q in g['probes'] if q['day'] == day and q['pt'] == p['pt'] and q['alive'])
            imp = living_demon({k: t[0] for k, t in p['truth'].items()}, alive)
            if imp and imp != p['who'] and imp in p['P'] and p['P'][imp][1] is not None:
                s += p['P'][imp][1]; c += 1
        return (s, c)
    return f
GOODROLES = {'Washerwoman', 'Librarian', 'Investigator', 'Chef', 'Empath', 'Fortune Teller', 'Undertaker', 'Monk',
             'Ravenkeeper', 'Virgin', 'Slayer', 'Soldier', 'Mayor', 'Butler', 'Drunk', 'Recluse', 'Saint'}
import re
EVILW = re.compile(r"\b(imp|demon|poisoner|spy|baron|scarlet|minion|evil)\b", re.I)
GOODW = re.compile(r"\b(" + "|".join(sorted(GOODROLES)) + r")\b", re.I)
def believed(ch):
    """'good' if the seat's best guess for a player's character is a good character, 'evil' if evil, else None."""
    if not ch: return None
    if EVILW.search(ch): return 'evil'
    if GOODW.search(ch): return 'good'
    return None
def believed_good_evil(mind, maxd=99):
    """Share of (good seat, living evil player) pairs where the seat's guess for the evil player is a good character."""
    def f(g):
        n = d = 0
        for p in g['probes']:
            if p['team'] != 'good' or p['pt'] != 'nominations' or not (mind <= p['day'] <= maxd): continue
            for k, (pe, _, ch) in p['P'].items():
                if k == p['who'] or not p['truth'][k][1]: continue
                b = believed(ch)
                if b: n += b == 'good'; d += 1
        return (n, d)
    return f
def claimant(evil_target):
    """Mean P(evil) a good seat gives players it lists with a specific good character (it records their claim)."""
    def f(g):
        s = c = 0
        for p in g['probes']:
            if p['team'] != 'good' or p['pt'] != 'nominations': continue
            for k, (pe, _, ch) in p['P'].items():
                if k == p['who'] or pe is None or not ch: continue
                if believed(ch) == 'good' and p['truth'][k][1] == evil_target:
                    s += pe; c += 1
        return (s, c)
    return f
def shift(kind, target_evil):
    """Mean change in a good seat's P(evil) on a target since its previous probe, for targets named to it in
    whispers by evil only ('evil'), by good only ('good'), or by no one ('none')."""
    def f(g):
        s = c = 0
        for day, who, n, tev, pe, qe, ew, gw, ep, gp in g['shift']:
            if tev != target_evil: continue
            k = 'evil' if ew > 0 and gw == 0 else 'good' if gw > 0 and ew == 0 else 'none' if ew == gw == 0 else None
            if k == kind: s += qe - pe; c += 1
        return (s, c)
    return f
def tx(side, a, b): return lambda g: (g['text_' + side][a], g['text_' + side][b])
def m_speak(side): return lambda g: (g['text_' + side]['sq_say'], g['sq_slots_' + side])
def m_d1claim(g): return (g['d1_public_claim_good'], 1)
def m_firstwh(g): return (g['firstwh_claim_good'], 1)
def m_ask(g): return (g['d1_askfirst_good'], 1)
def cl(side, a, b): return lambda g: (g['calls'].get(side, {}).get(a, 0), g['calls'].get(side, {}).get(b, 0))
def m_lost(side): return lambda g: (g['calls'].get(side, {}).get('lost', 0), 1)
# evil-side
def m_evilwin(g): return (not g['goodwin'], 1)
def m_evil_word_share(g):
    tot = g['text_good']['say_words'] + g['text_evil']['say_words']
    seat = len(g['evilset']) / g['players']
    return (g['text_evil']['say_words'] / tot / seat if tot else 0, 1)
def m_evil_nom_share(g):
    return (sum(n['nr_ev'] for n in g['noms']), len(g['noms']))
def m_goodexec_by_evil(g):
    x = [e for e in g['execs'] if e['died'] and not e['ev'] and e['nr'] is not None]
    return (sum(bool(e['nr_ev']) for e in x), len(x))
def m_town_imp_d2_evil(g): return m_imp_d(2)(g)

INFO = {'Washerwoman', 'Librarian', 'Investigator', 'Chef', 'Empath', 'Fortune Teller', 'Undertaker', 'Ravenkeeper'}
def m_kill_info(g):
    k = [x for x in g['kills'] if x['char'] not in ('Imp',)]; return (sum(x['char'] in INFO for x in k), len(k))
def m_zero_vote(side):
    return lambda g: (g['zero'].get(side, {}).get('vote_zero', 0), g['zero'].get(side, {}).get('vote_n', 0))
def m_zero_beat(side):
    return lambda g: (g['zero'].get(side, {}).get('beat_zero', 0), g['zero'].get(side, {}).get('beat_n', 0))
def yes_good_think(zero):
    def f(g):
        v = [x for x in g['votes'] if not x['v_ev'] and not x['ne_ev'] and x.get('think') is not None
             and (x['think'] == 0) == zero]
        return (sum(x['yes'] for x in v), len(v))
    return f
GOOD = [
    ('Good wins', m_win), ('Executions that hit evil', m_exec_evil), ('Day-1 executions that hit evil', m_d1_exec_evil),
    ('Day-2+ executions that hit evil', lambda g: (sum(e['ev'] for e in g['execs'] if e['died'] and e['day'] >= 2),
                                                   sum(1 for e in g['execs'] if e['died'] and e['day'] >= 2))),
    ('Games with 2+ good players executed', lambda g: (sum(1 for e in g['execs'] if e['died'] and not e['ev']) >= 2, 1)),
    ('Good players executed / game', m_good_exec), ('  of which unverifiable roles', m_good_exec_uncheck),
    ('Saint executions / game', m_saint),
    ('Good yes votes on good nominees', m_yes_good), ('Good yes votes on evil nominees', m_yes_evil),
    ('  good-nominee yes when prior votes mostly yes', herd(True)), ('  good-nominee yes when prior votes mostly no', herd(False)),
    ('Town top suspect is evil (all days)', m_top_evil), ('Town top suspect is evil (day 1)', m_top_evil_d1),
    ('Execution = town top suspect', m_exec_is_top), ('  evil hit rate when execution follows consensus', exec_top_split(True)),
    ('  evil hit rate when it does not', exec_top_split(False)), ('Seats agreeing with town top', m_agree),
    ('Brier on evil (lower better)', m_brier), ('P(Demon) on Imp, day 1', m_imp_d(1)), ('P(Demon) on Imp, day 2', m_imp_d(2)),
    ('P(Demon) on Imp, day 3', m_imp_d(3)),
    ('P(evil) on evil players listed as a good role', claimant(True)), ('P(evil) on good players listed as a good role', claimant(False)),
    ('Evil players believed a good character, day 1', believed_good_evil(1, 1)),
    ('Evil players believed a good character, day 2', believed_good_evil(2, 2)),
    ('Evil players believed a good character, day 3+', believed_good_evil(3)),
    ('Town top suspect is evil, day 3+', lambda g: top_from(g, 3)),
    ('Town top suspect is evil, day 2', lambda g: top_from(g, 2, 2)),
    ('dP(evil) on good target named only by evil whispers', shift('evil', False)),
    ('dP(evil) on good target named only by good whispers', shift('good', False)),
    ('dP(evil) on good target named by no whisper', shift('none', False)),
    ('dP(evil) on evil target named only by evil whispers', shift('evil', True)),
    ('dP(evil) on evil target named only by good whispers', shift('good', True)),
    ('Words per public message (good)', tx('good', 'say_words', 'n_say')), ('Words per whisper (good)', tx('good', 'wh_words', 'n_wh')),
    ('Square turns spoken (good)', m_speak('good')), ('Beats passed (good)', cl('good', 'beat_pass', 'beat_turns')),
    ('Day-1 public role claims (share of good seats)', m_d1claim), ('First whisper names own role (good)', m_firstwh),
    ('Day-1 good whispers asking for a claim', m_ask), ('Skeptical phrasing per message (good)', tx('good', 'skeptic', 'n_text')),
    ('Whispers to two players (good)', tx('good', 'wh_two', 'n_wh')),
    ('Demon kills that hit an info role', m_kill_info),
    ('Vote turns with zero thinking (good)', m_zero_vote('good')), ('Beat turns with zero thinking (good)', m_zero_beat('good')),
    ('  yes on good nominee, vote turn with no thinking', yes_good_think(True)),
    ('  yes on good nominee, vote turn with thinking', yes_good_think(False)),
    ('Output tokens / call (good)', cl('good', 'out', 'calls')), ('Thinking tokens / call (good)', cl('good', 'think', 'calls')),
    ('Ceiling hits / call (good)', cl('good', 'ceiling', 'calls')), ('Lost turns / game (good)', m_lost('good')),
]
EVIL = [
    ('Evil wins', m_evilwin), ('Town executions that hit evil', m_exec_evil), ('Town top suspect is evil', m_top_evil),
    ('Town P(Demon) on Imp, day 2', m_imp_d(2)), ('Evil share of public words / seat share', m_evil_word_share),
    ('Words per public message (evil)', tx('evil', 'say_words', 'n_say')), ('Square turns spoken (evil)', m_speak('evil')),
    ('Beats passed (evil)', cl('evil', 'beat_pass', 'beat_turns')),
    ('Nominations made by evil', m_evil_nom_share), ('Good executions nominated by evil', m_goodexec_by_evil),
    ('Skeptical phrasing per message (evil)', tx('evil', 'skeptic', 'n_text')),
    ('Output tokens / call (evil)', cl('evil', 'out', 'calls')), ('Thinking tokens / call (evil)', cl('evil', 'think', 'calls')),
    ('Ceiling hits / call (evil)', cl('evil', 'ceiling', 'calls')), ('Lost turns / game (evil)', m_lost('evil')),
]

pairs = sorted({g['pair'] for g in R}); pidx = {p: i for i, p in enumerate(pairs)}
P = np.array([pidx[g['pair']] for g in R])
rng = np.random.default_rng(1)
B = 1000
W = np.stack([np.bincount(rng.integers(0, len(pairs), len(pairs)), minlength=len(pairs)) for _ in range(B)])  # B x pairs
WG = W[:, P]  # B x games

def run(metrics, side):
    me_key, op_key = ('good', 'evil') if side == 'good' else ('evil', 'good')
    res = {}
    for name, f in metrics:
        nd = np.array([f(g) for g in R], float); num, den = nd[:, 0], nd[:, 1]
        me = np.array([g[me_key] for g in R]); op = np.array([g[op_key] for g in R])
        # opponent baseline: pooled ratio vs the same opponent, excluding this model's own games
        base = np.zeros(len(R))
        for i in range(len(R)):
            m = (op == op[i]) & (me != me[i])
            base[i] = num[m].sum() / den[m].sum() if den[m].sum() else np.nan
        adj = num - den * np.nan_to_num(base)
        out = {}
        for gname, sel in GROUPS.items():
            s = np.array([sel(m) for m in me])
            raw = num[s].sum() / den[s].sum() if den[s].sum() else np.nan
            a = adj[s].sum() / den[s].sum() if den[s].sum() else np.nan
            bw = WG[:, s]
            braw = (bw * num[s]).sum(1) / np.maximum((bw * den[s]).sum(1), 1e-9)
            badj = (bw * adj[s]).sum(1) / np.maximum((bw * den[s]).sum(1), 1e-9)
            out[gname] = dict(raw=raw, adj=a, n=int(s.sum()), braw=braw, badj=badj)
        for gname in sorted(set(me)):
            s = me == gname
            out['m:' + gname] = dict(raw=num[s].sum() / den[s].sum() if den[s].sum() else np.nan,
                                     adj=adj[s].sum() / den[s].sum() if den[s].sum() else np.nan, n=int(s.sum()))
        res[name] = out
    return res

def fmt(x): return 'nan' if x != x else (f'{x:.2f}' if abs(x) < 10 else f'{x:.0f}')
def report(res, title):
    print('\n=====', title)
    for name, out in res.items():
        line = f'{name:52s}'
        for a, o in CONTRASTS:
            A, O = out[a], out[o]
            d = A['braw'] - O['braw']; da = A['badj'] - O['badj']
            lo, hi = np.nanpercentile(d, [5, 95]); alo, ahi = np.nanpercentile(da, [5, 95])
            line += f' | {fmt(A["raw"])} v {fmt(O["raw"])} adjD {fmt(A["adj"]-O["adj"])} [{fmt(alo)},{fmt(ahi)}]'
        print(line)
    print('per model (raw / opponent-adjusted):')
    for name, out in res.items():
        ms = sorted([k for k in out if k.startswith('m:')])
        print(f'  {name:50s} ' + '  '.join(f'{SHORT[k[2:]]} {fmt(out[k]["raw"])}/{fmt(out[k]["adj"])}' for k in ms))

print('games', len(R), 'pairs', len(pairs))
print('contrasts:', CONTRASTS)
rg = run(GOOD, 'good'); report(rg, 'GOOD SIDE (by good model; adjusted for evil opponent)')
re_ = run(EVIL, 'evil'); report(re_, 'EVIL SIDE (by evil model; adjusted for good opponent)')
if OUT:
    def clean(res):
        o = {}
        for name, out in res.items():
            o[name] = {}
            for k, v in out.items():
                d = {kk: (None if (isinstance(vv, float) and vv != vv) else vv) for kk, vv in v.items() if kk in ('raw', 'adj', 'n')}
                if 'braw' in v:
                    d['adj90'] = [float(x) for x in np.nanpercentile(v['badj'], [5, 95])]
                o[name][k] = d
        return o
    json.dump({'good': clean(rg), 'evil': clean(re_)}, open(OUT, 'w'), indent=1)
