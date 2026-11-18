"""마케팅 동향 · 신작 성과 데이터 (11/18): data/market_trend.json. top_analysis.py 가 순위 기록을 받은 뒤 이어서 호출.
- 마케팅 동향: 추적 게임의 최근 7일 새 소재, 집행 변화, 28일 달력, 소재 형식, 카피 키워드, 장기 집행 소재
- 신작 성과: 최근 8주 매출 TOP100 첫 진입 중 Play 출시 30일 안의 게임 (출시일은 data/release_dates.json 에 저장해 한 번만 읽음)"""
import collections, datetime as D, json, os, re, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
THUMB = 'https://jdpark618.github.io/gchart/data/ads/img'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'}
KEYWORDS = [('보상 · 쿠폰', r'쿠폰|보상|무료|증정|지급|선물|혜택'), ('뽑기 · 소환', r'뽑기|소환|연차|가챠|픽업|SSR|SSS'), ('신규 서버', r'신규\s*서버|신서버|서버\s*오픈'), ('사전예약 · 출시', r'사전\s*예약|정식\s*출시|그랜드\s*오픈|오픈!|출시'),
            ('콜라보 · IP', r'콜라보|원작|정식\s*후속|IP'), ('성장 · 방치', r'방치|키우기|성장|레벨|자동'), ('경쟁 · 전쟁', r'PvP|공성|전쟁|길드|랭킹|1위'), ('업데이트', r'업데이트|신규\s*(콘텐츠|캐릭터|지역|던전)|시즌')]


def load(path, default=None):
    try: return json.load(open(os.path.join(DATA, path), encoding='utf-8'))
    except Exception: return default
def kday(ts): return (D.datetime(1970, 1, 1) + D.timedelta(seconds=int(ts) + 9 * 3600)).strftime('%Y-%m-%d')
def fmt_of(media, a):
    if media == 'Meta': return '영상' if a.get('fmt') == 'VIDEO' else '카탈로그' if a.get('fmt') in ('DCO', 'DPA') else '이미지'
    return '영상' if a.get('yt') else '이미지' if a.get('fmt') == 2 else '앱 광고'
def one_line(t, n): return re.sub(r'\s+', ' ', t or '').strip()[:n]


def trend(RK, info, last, update_days):
    back = lambda n: (D.date.fromisoformat(last) - D.timedelta(days=n)).isoformat()
    d7, d14, cal = back(7), back(14), [back(n) for n in range(27, -1, -1)]
    end_ts = D.datetime.fromisoformat(last + 'T23:59:59').replace(tzinfo=D.timezone(D.timedelta(hours=9))).timestamp()
    rows, mix, prev_media, words, word_games, word_text, runs = [], collections.Counter(), collections.Counter(), collections.Counter(), collections.defaultdict(set), collections.defaultdict(collections.Counter), []
    for x in load('ads/index.json', []):
        p = x['pkg']; R = load(f'ads/{p}.json', {}); M, G = R.get('meta', {}).get('ads', []), R.get('google', {}).get('ads', [])
        if not M and not G: continue
        day, now, prev, texts = collections.Counter(), collections.Counter(), 0, set()
        for media, L, key in (('Meta', M, 'start'), ('Google', G, 'first')):
            for a in L:
                if not a.get(key): continue
                t = kday(a[key]); day[t] += 1
                if t > d7: now[media] += 1; mix[fmt_of(media, a)] += 1
                elif t > d14: prev += 1; prev_media[media] += 1
                if t > d14:
                    b = one_line(a.get('body') or ' '.join(a.get('lines') or []) or a.get('title'), 160)
                    if b and '{{' not in b: texts.add(b)
        for t in texts:
            for k, rx in KEYWORDS:
                if re.search(rx, t, re.I): words[k] += 1; word_games[k].add(p); word_text[k][t[:46]] += 1
        n = sum(now.values())
        state = '시작' if n >= 5 and prev == 0 else '급증' if n >= 5 and n >= 2 * prev else '중단' if prev >= 5 and n == 0 else '감소' if prev >= 5 and n <= prev / 2 else '유지' if n >= 5 else '소량'
        s, q, ud = RK['s'].get(p, {}), RK['p'].get(p, {}), update_days(p)
        rows.append(dict(pkg=p, name=x['name'], pub=x.get('publisher', ''), icon=info.get(p, {}).get('icon', ''), now=n, prev=prev, meta=now['Meta'], google=now['Google'], st=state, s=s.get(last), s7=s.get(back(7)), p=q.get(last), p7=q.get(back(7)),
                         live=(x.get('meta_live') or 0) + (x.get('google_live') or 0), cal=[day.get(d, 0) for d in cal], upd=[d in ud for d in cal], total28=sum(day.get(d, 0) for d in cal)))
        for a in M:
            text = one_line(a.get('body') or a.get('title'), 64)
            if a.get('active') and a.get('start') and a.get('thumb') and a.get('seen') and kday(a['seen']) >= back(4) and text and '{{' not in text:
                runs.append(dict(pkg=p, game=x['name'], media='Meta', days=int((end_ts - a['start']) // 86400), fmt=fmt_of('Meta', a), text=text, thumb=f"{THUMB}/{p}/{a['thumb']}"))
        for a in G:
            if a.get('first') and a.get('last') and a.get('thumb') and kday(a['last']) >= back(4):
                runs.append(dict(pkg=p, game=x['name'], media='Google', days=int((a['last'] - a['first']) // 86400), fmt=fmt_of('Google', a), text=one_line(' '.join(a.get('lines') or []), 64), thumb=f"{THUMB}/{p}/{a['thumb']}"))
    rows.sort(key=lambda r: (-r['now'], -r['live']))
    best, used = [], set()
    for a in sorted(runs, key=lambda a: -a['days']):
        if a['pkg'] not in used and len(best) < 8: used.add(a['pkg']); best.append(a)
    return dict(cal=cal, rows=rows, fmt=mix.most_common(), best=best, kw=[dict(k=k, n=v, games=len(word_games[k]), ex=word_text[k].most_common(1)[0][0]) for k, v in words.most_common()],
                kpi=dict(active=sum(r['now'] >= 5 for r in rows), total=sum(r['now'] for r in rows), prev=sum(r['prev'] for r in rows), rising=sum(r['st'] in ('시작', '급증') for r in rows), games=len(rows), meta=sum(r['meta'] for r in rows), google=sum(r['google'] for r in rows), metaPrev=prev_media['Meta'], googlePrev=prev_media['Google']))


def release_dates(pkgs):
    path = os.path.join(DATA, 'release_dates.json'); known = load('release_dates.json', {}); asked = 0
    for p in pkgs:
        if p in known or asked >= 40: continue
        asked += 1
        try:
            h = urllib.request.urlopen(urllib.request.Request(f'https://play.google.com/store/apps/details?id={p}&hl=ko&gl=KR', headers=UA), timeout=40).read().decode('utf-8', 'ignore')
            m = re.search(r'"(\d{4})\. (\d{1,2})\. (\d{1,2})\.",\[\d+,\d+\]\]', h)
            if m: known[p] = f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
        except Exception as e: print('출시일 조회 실패:', p, str(e)[:80])
        time.sleep(.8)
    json.dump(known, open(path, 'w', encoding='utf-8'), separators=(',', ':'))
    return known


def debuts(RK, info, days):
    last, idx = days[-1], {d: i for i, d in enumerate(days)}; start = (D.date.fromisoformat(last) - D.timedelta(days=56)).isoformat()
    cand = {p: min(m) for p, m in RK['s'].items() if min(m) > days[0] and min(m) >= start and min(m) in idx}
    rel = release_dates(sorted(cand)); out = []
    for p, f in cand.items():
        r = rel.get(p)
        age = (D.date.fromisoformat(f) - D.date.fromisoformat(r)).days if r else None
        if age is None or age > 30: continue
        m = RK['s'][p]; ser = [m.get(d) for d in days[idx[f]:]]; first14 = ser[:14]; in100 = sum(1 for v in first14 if v); t30 = sum(1 for v in first14 if v and v <= 30); now = m.get(last)
        state = '관찰 중' if len(ser) < 14 else '상위 안착' if t30 >= 11 else '즉시 이탈' if in100 < 7 else '중위 안착' if now else '진입 후 이탈'
        pop = min(RK['p'].get(p, {}).values(), default=None)
        out.append(dict(pkg=p, name=info.get(p, {}).get('name', p), icon=info.get(p, {}).get('icon', ''), rel=r, first=f, age=age, best=min(m.values()), now=now, pop=pop, st=state, ser=ser[:30]))
    order = ['상위 안착', '중위 안착', '관찰 중', '진입 후 이탈', '즉시 이탈']; out.sort(key=lambda x: (order.index(x['st']), x['best']))
    hit = [x for x in out if x['pop'] and x['pop'] <= 10]; miss = [x for x in out if not (x['pop'] and x['pop'] <= 10)]
    return dict(list=out, pop10=dict(n=len(hit), top30=sum(x['best'] <= 30 for x in hit)), other=dict(n=len(miss), top30=sum(x['best'] <= 30 for x in miss)))


def build(RK, info, days, update_days):
    json.dump(dict(base=days[-1], **trend(RK, info, days[-1], update_days), debut=debuts(RK, info, days)), open(os.path.join(DATA, 'market_trend.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('마케팅 동향 · 신작 성과 저장')
