import json,glob,collections,statistics as st,math
from clocktower.probe import living_demon
R=json.load(open('docs/plans/f2-ratings.json'))
elo={m['id']:m for m in R['models']}
games=sorted(glob.glob('games/v16/f2-*/game-*')+glob.glob('games/v17/f3*/game-*'))
G=collections.defaultdict(lambda: collections.defaultdict(list))  # good model -> metric -> list
E=collections.defaultdict(lambda: collections.defaultdict(list))  # evil model
gamerows=[]
def auc(pos,neg):
    if not pos or not neg: return None
    return sum((a>b)+0.5*(a==b) for a in pos for b in neg)/len(pos)/len(neg)
for g in games:
    cfg=json.load(open(g+'/config.json')); res=json.load(open(g+'/result.json'))
    gm,em=cfg['good_model'],cfg['evil_model']; goodwin=res['winner']=='good'
    pts=collections.defaultdict(list)
    for l in open(g+'/probes.jsonl'):
        r=json.loads(l)
        pts[(r['day'],r['point'])].append(r)  # failed probes still record whether their seat is alive
    d1=None
    for (day,pt),rs in sorted(pts.items()):
        truth=rs[0]['truth']
        imp=living_demon({n:t['character'] for n,t in truth.items()}, (r['player'] for r in rs if r.get('alive')))
        if not imp: continue
        n=len(truth)
        goods=[r for r in rs if r['team']=='good' and r.get('players')]
        for r in goods:
            P=r['players']; others=[k for k in P if k!=r['player']]
            dem={k:P[k].get('demon') for k in others if P[k].get('demon') is not None}
            if imp not in dem or len(dem)<n-2: continue
            top=max(dem,key=dem.get); tied=[k for k in dem if dem[k]==dem[top]]
            key=f'{pt}{min(day,4)}'
            G[gm]['imp_p_'+key].append(dem[imp])
            G[gm]['imp_top_'+key].append((imp in tied)/len(tied))
            ev=[P[k]['evil'] for k in others if P[k].get('evil') is not None and truth[k]['evil']]
            gd=[P[k]['evil'] for k in others if P[k].get('evil') is not None and not truth[k]['evil']]
            a=auc(ev,gd)
            if a is not None: G[gm]['evil_auc_'+key].append(a)
            if pt=='nominations':
                G[gm]['imp_p'].append(dem[imp]); G[gm]['imp_top'].append((imp in tied)/len(tied))
                if a is not None: G[gm]['evil_auc'].append(a)
                G[gm]['base'].append(1/(n-1))
                E[em]['hid_p'].append(dem[imp])
                if a is not None: E[em]['hid_auc'].append(a)
        # town consensus on day 1 nominations
        if (day,pt)==(1,'nominations') and goods:
            town={k:st.mean([r['players'][k]['demon'] for r in goods if k in r['players'] and k!=r['player'] and r['players'][k].get('demon') is not None] or [0]) for k in truth}
            rank=sorted(town,key=lambda k:-town[k]).index(imp)+1
            d1=(town[imp],rank)
    gamerows.append(dict(g=g,gm=gm,em=em,goodwin=goodwin,d1=d1))
json.dump(gamerows,open('probegames.json','w'))
print('AS GOOD (detection), nominations probes, all days pooled')
print(f"{'model':18s} {'P(Imp)':>7s} {'base':>5s} {'Imp=top':>7s} {'evilAUC':>7s} | d1 P(Imp) d2 d3 | goodWin  detElo")
rows=[]
for m,v in G.items():
    gw=[x['goodwin'] for x in gamerows if x['gm']==m]
    e=elo.get(m,{})
    rows.append((m,st.mean(v['imp_p']),st.mean(v['base']),st.mean(v['imp_top']),st.mean(v['evil_auc']),
       [round(st.mean(v[f'imp_p_nominations{d}']),2) if v[f'imp_p_nominations{d}'] else None for d in (1,2,3)],sum(gw)/len(gw),e.get('detection'),len(gw)))
rows.sort(key=lambda r:-r[1])
for r in rows: print(f"{r[0]:18s} {r[1]:7.2f} {r[2]:5.2f} {r[3]:7.2f} {r[4]:7.2f} | {r[5]} | {r[6]:.2f} (n={r[8]})  {r[7]}")
def corr(x,y):
    mx,my=st.mean(x),st.mean(y); return sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt(sum((a-mx)**2 for a in x)*sum((b-my)**2 for b in y))
print('corr P(Imp) vs good win rate:',round(corr([r[1] for r in rows],[r[6] for r in rows]),2),
      '| vs detection Elo:',round(corr([r[1] for r in rows],[r[7] for r in rows]),2),
      '| Imp-top vs win:',round(corr([r[3] for r in rows],[r[6] for r in rows]),2),
      '| evilAUC vs Elo:',round(corr([r[4] for r in rows],[r[7] for r in rows]),2))
print('\nAS EVIL (hiding): P(Imp) the opposing town gives this model\'s Imp, lower=better')
er=[]
for m,v in E.items():
    ew=[not x['goodwin'] for x in gamerows if x['em']==m]
    er.append((m,st.mean(v['hid_p']),st.mean(v['hid_auc']),sum(ew)/len(ew),elo.get(m,{}).get('deception')))
er.sort(key=lambda r:r[1])
for r in er: print(f"{r[0]:18s} town P(Imp) {r[1]:.2f}  town evilAUC {r[2]:.2f}  evilWin {r[3]:.2f}  decElo {r[4]}")
print('corr hide vs evil win:',round(corr([r[1] for r in er],[r[3] for r in er]),2),'| vs deception Elo:',round(corr([r[1] for r in er],[r[4] for r in er]),2))
# per-game: does day-1 consensus predict outcome
print('\nPer game: town rank of the true Imp at day-1 nominations -> good win rate')
b=collections.defaultdict(list)
for x in gamerows:
    if x['d1']: b[min(x['d1'][1],4)].append(x['goodwin'])
for k in sorted(b): print(f"  rank {k if k<4 else '4+'}: {len(b[k])} games, good wins {sum(b[k])/len(b[k]):.0%}")
