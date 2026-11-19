"""상위권 분석 데이터 (11/18): 한국 Google Play 매출 TOP10 게임의 순위 · 업데이트 · 소재 투입을 묶어 data/top_analysis.json 으로 저장.
사이트 시장 분석 > 상위권 분석이 이 파일을 읽어 그림. 마케팅 추적이 저장하기 직전에 실행 (실패해도 이전 파일 유지).
- 업데이트: Play 기록 + App Store 과거 기록. 두 스토어에 며칠 차이로 올라온 같은 업데이트는 한 건으로 합침
- 업데이트 규모: 노트 기준(대 · 중 · 소)에 소재 투입량을 더해 판단 (그 게임 평소의 2배 이상 + 5개 이상이면 한 단계 위로 추정)
- 케이스(최근 14일): 업데이트 중심 · 마케팅 중심 · 복합 · 신호 없음"""
import collections, csv, datetime as D, io, json, os, re, statistics as st, sys, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
SHEET = '1CW7Xr3eWBUKBPC0DXRDsqqrx2itUlzZfXPVF2hUoMAw'
TOP_N, SHOW_DAYS, STRONG_MIN = 10, 60, 5
BIG = r'신규\s*서버|신서버|서버\s*오픈|신규\s*(캐릭터|영웅|직업|클래스|지역|맵|대륙|챕터|레이드)|신캐|대규모|대형|확장팩|리뉴얼|주년|그랜드|콜라보|시즌\s*\d'
MID = r'이벤트|시즌|콘텐츠|컨텐츠|추가|신규|업데이트|event|season|new'
SML = r'버그|오류|안정|수정|최적화|개선|bug|fix|optimi|improv'
LINE_CAT = [('서버', r'서버|server'), ('이벤트', r'이벤트|출석|콜라보|페스티벌|축제|주년|event|festival|collab'), ('BM', r'패키지|상품|뽑기|픽업|가챠|소환|패스|스킨|코스튬|의상|탈것|pass|skin|bundle|summon'),
            ('버그', r'버그|오류|현상\s*수정|안정|bug|fix'), ('개선', r'개선|밸런스|편의|최적화|조정|improv|optimi|balance'), ('콘텐츠', r'신규|추가|오픈|시즌|지역|던전|레이드|캐릭터|스킬|스토리|챕터|모드|new|add')]


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=120).read().decode('utf-8')
def load(*path, default=None):
    try: return json.load(open(os.path.join(DATA, *path), encoding='utf-8'))
    except Exception: return default
def kday(ts): return (D.datetime(1970, 1, 1) + D.timedelta(seconds=int(ts) + 9 * 3600)).strftime('%Y-%m-%d')
def gap_days(a, b): return (D.date.fromisoformat(b) - D.date.fromisoformat(a)).days


def rank_history():
    out, info = {}, {}
    for key, sheet in (('s', 'DB_매출순위'), ('p', 'DB_인기순위')):
        q = urllib.parse.urlencode({'tqx': 'out:csv', 'sheet': sheet, 'tq': 'select A,B,D,E,J where B <= 100 order by A'})
        for r in list(csv.reader(io.StringIO(get(f'https://docs.google.com/spreadsheets/d/{SHEET}/gviz/tq?{q}'))))[1:]:
            out.setdefault(key, {}).setdefault(r[3], {})[r[0]] = int(float(r[1]))
            if r[3]: info[r[3]] = {'name': r[2], 'icon': r[4].lstrip("'")}   # 날짜순이라 가장 최근 이름 · 아이콘이 남음
    return out, info
def app_lookup(ids):
    out = {}
    for i in range(0, len(ids), 50):
        try:
            for x in json.loads(get('https://itunes.apple.com/lookup?country=kr&id=' + ','.join(ids[i:i + 50]))).get('results', []):
                out[str(x.get('trackId'))] = {'bundle': x.get('bundleId'), 'rel': (x.get('releaseDate') or '')[:10]}
        except Exception as e: print('App Store 조회 실패:', str(e)[:100])
    return out


def note_lines(u): return {re.sub(r'[\s\W_]+', '', l) for l in (u.get('n') or '').split('\n') if len(l.strip()) > 3}
def same_update(a, b):
    x, y = note_lines(a), note_lines(b)
    if not x or not y: return bool(a.get('v')) and a.get('v') == b.get('v')
    return len(x & y) / max(1, min(len(x), len(y))) >= .6
def clean_notes(U):
    """게임마다 반복되는 양식 문구를 뺀 노트"""
    cnt = collections.Counter(l for u in U.values() for l in {x.strip() for x in (u.get('n') or '').split('\n') if x.strip()})
    lim = max(3, len(U) * .5)
    return {d: '\n'.join(l for l in (x.strip() for x in (u.get('n') or '').split('\n')) if l and cnt[l] < lim) for d, u in U.items()}
def note_size(t):
    if not t: return '불명'
    for k, rx in (('대', BIG), ('중', MID), ('소', SML)):
        if re.search(rx, t, re.I): return k
    return '불명'
def line_cat(l):
    l = l.strip()
    if len(l) < 4 or re.fullmatch(r'[\[【<(].*[\]】>)]', l) or re.search(r'감사|부탁드립니다|기대해|thank', l, re.I): return None
    return next((k for k, rx in LINE_CAT if re.search(rx, l, re.I)), None)
def final_size(note, strong):
    if note == '대': return '대'
    if note == '중': return '대' if strong else '중'
    return '중' if strong else '소'


def main():
    RK, info = rank_history()
    cnt = collections.Counter(d for m in RK['s'].values() for d in m)
    days = [d for d in sorted(cnt) if cnt[d] >= 90]; idx = {d: i for i, d in enumerate(days)}; last = days[-1]; i0 = len(days) - 1
    accounts, index = load('ads', 'accounts.json', default={}), {x['pkg']: x for x in load('ads', 'index.json', default=[])}
    gp, ios = load('updates', 'gp_kr.json', default={}), load('updates', 'ios_kr.json', default={})
    rows = load('snapshot.json', default={})['charts']['gp']['kr']['sales']['rows']; meta = {r[4]: r for r in rows}
    apps = app_lookup(sorted({v['ios'] for v in accounts.values() if v.get('ios')}))

    def updates(p):
        b = apps.get(str(accounts.get(p, {}).get('ios', '')), {}).get('bundle')
        U = {u['d']: u for u in ios.get(b or p, [])}; U.update({u['d']: u for u in gp.get(p, [])})
        out = {}
        for d, u in sorted(((d, u) for d, u in U.items() if days[0] <= d <= last), key=lambda x: x[0]):
            prev = next(reversed(out), None)
            if prev and gap_days(prev, d) <= 7 and same_update(out[prev], u):
                if len(u.get('n') or '') > len(out[prev].get('n') or ''): out[prev] = u
                continue
            out[d] = u
        return out
    def creatives(p):
        R = load('ads', f'{p}.json')
        if not R or (not R.get('meta', {}).get('ads') and not R.get('google', {}).get('ads')): return None
        c = collections.Counter(kday(a['start']) for a in R['meta'].get('ads', []) if a.get('start'))
        c.update(kday(a['first']) for a in R['google'].get('ads', []) if a.get('first')); return c
    def span(c, a, b): return sum(c.get(days[i], 0) for i in range(max(a, 0), min(b + 1, len(days))))
    def avg(p, ch, a, b):
        if a < 0 or b >= len(days): return None
        v = [RK[ch].get(p, {}).get(days[i]) for i in range(a, b + 1)]
        return None if None in v else sum(v) / len(v)
    def events(p, U, c):
        N = clean_notes(U); per = sum(c.get(d, 0) for d in days) / len(days) if c else 0; out = []
        for d in U:
            i = idx[d]; n = span(c, i - 3, i + 7) if c is not None else None; strong = c is not None and n >= STRONG_MIN and n >= 2 * per * 11
            ns = note_size(N[d]); size = final_size(ns, strong)
            b, a, pb, pa = avg(p, 's', i - 3, i - 1), avg(p, 's', i + 1, i + 3), avg(p, 'p', i - 3, i - 1), avg(p, 'p', i + 1, i + 3)
            dv = round(b - a, 1) if b is not None and a is not None else None
            first = next((l for l in N[d].split('\n') if len(l) > 3), '') or (U[d].get('n') or '').split('\n')[0]
            out.append(dict(d=d, size=size, est=size != ns and ns in ('대', '중', '소') or (ns == '불명' and strong), note=first.strip(' -·•')[:70], cr=n, strong=strong,
                            b=None if b is None else round(b, 1), a=None if a is None else round(a, 1), dv=dv, pdv=round(pb - pa, 1) if pb is not None and pa is not None else None,
                            res='측정 전' if dv is None else '상승' if dv >= 1 else '하락' if dv <= -1 else '방어'))
        return out

    games = []
    show = days[-SHOW_DAYS:]
    for p in sorted((p for p, m in RK['s'].items() if m.get(last, 999) <= TOP_N), key=lambda p: RK['s'][p][last]):
        r = meta.get(p)
        if not r: continue
        U = updates(p); c = creatives(p); L = list(U); ev = events(p, U, c)
        recent = [d for d in L if idx[d] > i0 - 14]; gaps = [gap_days(a, b) for a, b in zip(L, L[1:])]
        now14, prev14 = (span(c, i0 - 13, i0), span(c, i0 - 27, i0 - 14)) if c is not None else (None, None)
        s14 = RK['s'][p].get(days[-15]); dr = s14 - RK['s'][p][last] if s14 else None
        trend = '신규 진입' if s14 is None else '상승' if dr >= 2 else '하락' if dr <= -2 else '유지'
        strong = c is not None and now14 >= STRONG_MIN and now14 >= 2 * max(prev14, 1); upd = bool(recent)
        bands, on = [], None
        if c is not None:
            for i, d in enumerate(days):
                if on is None and span(c, i, i + 6) >= STRONG_MIN and c.get(d, 0) > 0: on = i
                elif on is not None and i >= on + 6 and span(c, i - 6, i) < STRONG_MIN: bands.append([days[on], d]); on = None
            if on is not None: bands.append([days[on], last])
        cats = collections.Counter(k for d in L for l in (U[d].get('n') or '').split('\n') for k in [line_cat(l)] if k)
        games.append(dict(pkg=p, name=r[3], pub=r[6], genre=r[5], icon=r[9] if len(r) > 9 else '', rank=RK['s'][p][last], s14=s14, dr=dr, trend=trend,
                          pop=RK['p'].get(p, {}).get(last), pop14=RK['p'].get(p, {}).get(days[-15]), case='복합' if strong and upd else '마케팅' if strong else '업데이트' if upd else '안정',
                          mk='ok' if c is not None else 'none' if p in index else 'wait', upd60=sum(1 for d in L if idx[d] > i0 - 60), upd14=len(recent), gap=st.median(gaps) if gaps else None, last=L[-1] if L else '',
                          cats=cats.most_common(6), sizes=collections.Counter(e['size'] for e in ev), now14=now14, prev14=prev14, rel=apps.get(str(accounts.get(p, {}).get('ios', '')), {}).get('rel', ''),
                          S=[RK['s'][p].get(d) for d in show], P=[RK['p'].get(p, {}).get(d) for d in show], cr=[c.get(d, 0) if c is not None else 0 for d in show], bands=bands, ev=ev[::-1]))
    # 패턴 누적: 마케팅 추적 게임의 업데이트 중 전후 순위를 잰 건수 (항목별 30건부터 결론 표시)
    size = collections.Counter()
    for p in index:
        c = creatives(p)
        if c is not None: size.update(e['size'] for e in events(p, updates(p), c) if e['dv'] is not None)
    # 주간 리포트 '업데이트 효과' 쪽 (11/19): 매출 TOP100 에 든 적 있는 모든 게임의 업데이트 · 전후 3일 매출 평균 (상위권 분석과 같은 규칙)
    allev = []
    for p in RK['s']:
        U = updates(p)
        if not U: continue
        N = clean_notes(U)
        for e in events(p, U, creatives(p)):
            cats = sorted({k for l in (U[e['d']].get('n') or '').splitlines() for k in [line_cat(l)] if k})
            allev.append(dict(p=p, n=info.get(p, {}).get('name', ''), cats=cats, nt=N[e['d']][:900], **{k: e[k] for k in ('d', 'size', 'est', 'note', 'b', 'a', 'dv', 'pdv', 'res', 'cr')}))
    json.dump(dict(base=last, since=days[0], ev=sorted(allev, key=lambda e: e['d'])), open(os.path.join(DATA, 'update_events.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'업데이트 효과 {len(allev)}건')
    if len(games) < TOP_N: raise RuntimeError(f'게임 {len(games)}개만 찾음')
    json.dump(dict(base=last, days=show, games=games, pattern=dict(total=sum(size.values()), size=size)), open(os.path.join(DATA, 'top_analysis.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'상위권 분석 {last}: 게임 {len(games)}개 · 패턴 표본 {sum(size.values())}건')
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import market_trend
        market_trend.build(RK, info, days, lambda p: set(updates(p)))
    except Exception as e: print('마케팅 동향 생성 실패 (이전 파일 유지):', str(e)[:300])


if __name__ == '__main__':
    try: main()
    except Exception as e:
        print('상위권 분석 생성 실패 (이전 파일 유지):', str(e)[:300]); sys.exit(0)
