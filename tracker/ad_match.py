"""마케팅 추적: 게임 ↔ 광고 계정 자동 매칭 (11/06 시험).
대상: 오늘 한국 구글 인기 TOP5 + 매출 TOP5 (data/snapshot.json). 광고 수집은 하지 않고 '어느 계정이 이 게임 광고를 하나'만 찾는다.
  1) 기본 정보: 순위 데이터(이름·패키지·퍼블리셔) + Play 스토어(개발자 법인명·웹사이트) + App Store 검색 API(iOS 앱 ID·판매자·URL)
  2) Meta: 게임명 변형·퍼블리셔명으로 검색 → 광고 링크에 Play 패키지 또는 iOS 앱 ID 가 든 광고의 '페이지'를 확정
     (없으면 게임 공식 사이트로 보내는 광고 → 추적 링크 + 페이지 · 문구의 이름 일치 순으로 — ad_terms.meta_evidence)
  3) Google: 이름(퍼블리셔 · 법인 · 판매자 · Meta 페이지 — 법인 표기를 뗀 알맹이 포함) + 도메인(웹사이트 · 문의 메일 · 패키지명으로 추천받은 것)으로 광고주 후보
     → KR 소재에 패키지/iOS ID 가 든 계정만 확정 (ad_terms.py · ad_google_match.js)
결과: data/ads/match.json (게임별 매칭 결과 + 무엇으로 찾았는지 + 시도한 후보)
실행: python tracker/ad_match.py"""
import asyncio, json, os, re, time, urllib.request
from urllib.parse import quote, urlparse
from playwright.async_api import async_playwright
from ad_track import UA, meta_query, nz
from ad_terms import JUNK, name_terms, domains_of, pkg_hints, core, nzk, game_queries, game_keys, hosts_of, meta_evidence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def targets():
    s = json.load(open(os.path.join(ROOT, 'data', 'snapshot.json'), encoding='utf-8'))['charts']['gp']['kr']
    out = {}
    for chart, lab in (('sales', '매출'), ('popular', '인기')):
        for r in s[chart]['rows'][:5]:
            out.setdefault(r[4], {'name': r[3], 'pkg': r[4], 'publisher': r[6], 'ranks': []})['ranks'].append(f'{lab} {r[1]}위')
    return list(out.values())

def name_variants(n):
    base = [n, re.sub(r'\s*\(.*?\)', '', n), n.split(':')[0], n.split(':')[-1], re.sub(r'[:：].*$', '', n), re.sub(r'\s*[-–]\s.*$', '', n)]
    return list(dict.fromkeys(v.strip() for v in base if len(v.strip()) >= 2))

def appstore(name, publisher):
    """App Store 검색 API: 이름이 가장 비슷한 앱의 iOS ID · 판매자 · URL"""
    url = f'https://itunes.apple.com/search?term={quote(name)}&country=kr&entity=software&limit=8'
    try: res = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=20))['results']
    except Exception: return {}
    k, pk = nz(re.sub(r'\(.*?\)', '', name)), nz(publisher)
    def score(x):
        t, a = nz(x.get('trackName')), nz(x.get('artistName')) + nz(x.get('sellerName'))
        return (3 if t == k else 2 if k and (k in t or t in k) else 0) + (1 if pk and (pk[:6] in a) else 0)
    best = max(res, key=score, default=None)
    if not best or score(best) < 2: return {}
    return {'ios_id': str(best['trackId']), 'ios_name': best.get('trackName'), 'seller': best.get('sellerName'), 'artist': best.get('artistName'), 'seller_url': best.get('sellerUrl') or ''}

async def play_info(ctx, pkg):
    """Play 스토어: 개발자 표시명 · 법인명(개발자 소개) · 웹사이트"""
    pg = await ctx.new_page(); info = {}
    try:
        await pg.goto(f'https://play.google.com/store/apps/details?id={pkg}&hl=ko&gl=KR', wait_until='domcontentloaded', timeout=45000)
        h = await pg.content(); i = h.find('개발자 소개')
        toks = [x.strip() for x in re.sub(r'<[^>]+>', '\n', h[i:i + 3000]).split('\n') if x.strip()] if i >= 0 else []
        info['legal'] = toks[1] if len(toks) > 1 else ''   # '개발자 소개' 다음 첫 줄 = 법인명 (클릭 없이 HTML에 있음)
        info['email'] = next((t for t in toks[1:6] if re.fullmatch(r'[\w.+-]+@[\w.-]+\.\w+', t)), '')   # 문의 메일 → 회사 도메인 (무료 메일은 ad_terms 가 버림)
        hrefs = await pg.eval_on_selector_all('a[href^="http"]', 'es => es.map(e => e.href)')
        info['site'] = next((u for u in hrefs if not JUNK.search(u)), '')
    except Exception as e: info['error'] = str(e)[:120]
    await pg.close(); return info

async def meta_match(pg, g, ios_id, names=(), hosts=(), cores=()):
    """Meta 페이지 찾기 (11/13 고도화 — 근거 A/B/C 는 ad_terms.meta_evidence).
    names = 게임 이름들(순위 데이터 · App Store 표기) · hosts = 게임 공식 사이트 호스트 · cores = 회사 이름 알맹이"""
    gn = [g['name']] + [n for n in names if n]
    qs = game_queries(gn)[:7] + ([g['publisher']] if g.get('publisher') else [])
    keys = game_keys(gn); cores = set(cores); hosts = set(hosts)
    IMP = '&active_status=active&sort_data[mode]=total_impressions&sort_data[direction]=desc'
    tried, weak = [], {}   # weak: 페이지 ID → {'name', 'B': n, 'C': n, 'q', 'why'}
    for q in qs:
        cnt, ads = await meta_query(pg, f'&q={quote(q)}&search_type=keyword_unordered')
        # 결과가 한 번에 30개까지만 보이므로, 더 있으면 '집행 중 · 노출순'으로 한 번 더 (흔한 낱말이라 남의 광고에 묻힌 경우)
        if cnt and cnt > len(ads) and len(ads) >= 25:
            await pg.wait_for_timeout(1000)
            _, more = await meta_query(pg, f'&q={quote(q)}&search_type=keyword_unordered{IMP}')
            got = {a.get('ad_archive_id') for a in ads}; ads = ads + [a for a in more if a.get('ad_archive_id') not in got]
        hit = {}
        for a in ads:
            ev = meta_evidence(a['snapshot'], g['pkg'], ios_id, hosts, keys, cores)
            if not ev: continue
            pid, pn = a.get('page_id'), a['snapshot'].get('page_name')
            if ev[0] == 'A': hit[pid] = (pn, ev[1])
            else:
                w = weak.setdefault(pid, {'name': pn, 'B': 0, 'C': 0, 'q': q, 'why': ev[1]}); w[ev[0]] += 1
                if ev[0] == 'B': w['why'] = ev[1]
        tried.append({'q': q, 'count': cnt, 'got': len(ads), 'hit': len(hit), 'weak': len(weak)})
        if hit: return {'pages': [{'id': k, 'name': v[0]} for k, v in hit.items()], 'by': f"검색어 '{q}' → 광고 링크의 {next(iter(hit.values()))[1]}", 'tried': tried}
        await pg.wait_for_timeout(1200)
    # 링크에 패키지가 없는 경우: 게임 사이트로 보내는 광고(B) → 없으면 추적 링크 + 이름 일치(C, 광고 2개 이상)
    b = {k: v for k, v in weak.items() if v['B']}
    c = {k: v for k, v in weak.items() if v['C'] >= 2}
    pick, label = (b, '광고 링크가 ') if b else (c, '추적 링크 광고 · ') if c else ({}, '')
    if pick:
        first = next(iter(pick.values()))
        return {'pages': [{'id': k, 'name': v['name']} for k, v in list(pick.items())[:3]], 'by': f"검색어 '{first['q']}' → {label}{first['why']}", 'tried': tried}
    return {'pages': [], 'by': '', 'tried': tried}

# Google 광고주 찾기 (11/13 고도화): 페이지 안에서 도는 부분은 ad_google_match.js, 검색어 만들기는 ad_terms.py
_js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ad_google_match.js'), encoding='utf-8').read()
GOOGLE_MATCH_JS = _js[_js.index('async ('):]

async def match_game(ctx, mpg, gpg, g, days=90, want=('meta', 'google')):
    """게임 1개 매칭: Meta 페이지 · Google 광고주 (Google 은 최근 days 일 소재 기준). want = 찾을 매체 (재매칭 때는 빠진 쪽만, 11/13)"""
    s0 = time.time()
    pi = await play_info(ctx, g['pkg'])
    ap = appstore(g['name'], g.get('publisher', ''))
    ios = ap.get('ios_id', '')
    company = [g.get('publisher'), pi.get('legal'), ap.get('seller'), ap.get('artist')]
    if 'meta' not in want: m = {'pages': [], 'by': '', 'skipped': True}
    else:
     try: m = await meta_match(mpg, g, ios, names=[ap.get('ios_name')], hosts=hosts_of([pi.get('site', ''), ap.get('seller_url', '')]), cores={nzk(core(n)) for n in company if n and len(nzk(core(n))) >= 2})
     except Exception as e: m = {'pages': [], 'by': '', 'error': str(e)[:160]}
    # 이름은 출처를 가리지 않고 모두: 순위 데이터의 퍼블리셔 · Play 법인명 · App Store 판매자/개발자 · 찾은 Meta 페이지 이름 (한글 · 영문이 섞여 들어옴)
    names = name_terms(company + [x.get('name') for x in m.get('pages', [])])
    domains = domains_of([pi.get('site', ''), ap.get('seller_url', '')], [pi.get('email', '')])
    hints = pkg_hints(g['pkg'])
    if 'google' not in want: gm = {'matched': [], 'checked': [], 'skipped': True}
    else:
     try: gm = await gpg.evaluate(GOOGLE_MATCH_JS, [names, hints, domains, [g['pkg']] + ([f'id{ios}'] if ios else []), int(time.time()) - days * 86400])
     except Exception as e: gm = {'matched': [], 'checked': [], 'error': str(e)[:160]}
    return {**g, 'play': pi, 'appstore': ap, 'meta': m, 'google': {**gm, 'names': names, 'hints': hints, 'domains': domains}, 'sec': round(time.time() - s0, 1)}

async def main():
    T = targets(); t0 = time.time(); results = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(locale='ko-KR', user_agent=UA, viewport={'width': 1366, 'height': 900})
        mpg = await ctx.new_page(); gpg = await ctx.new_page()
        await gpg.goto('https://adstransparency.google.com/?region=KR&hl=ko', wait_until='domcontentloaded', timeout=60000); await gpg.wait_for_timeout(2500)
        for g in T:
            r = await match_game(ctx, mpg, gpg, g)
            results.append(r)
            print(f"{g['name'][:20]} | Meta {'O' if r['meta']['pages'] else 'X'} | Google {'O' if r['google'].get('matched') else 'X'} | {round(r['sec'])}s", flush=True)
        await b.close()
    os.makedirs(os.path.join(ROOT, 'data', 'ads'), exist_ok=True)
    json.dump({'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'sec': round(time.time() - t0), 'games': results},
              open(os.path.join(ROOT, 'data', 'ads', 'match.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

if __name__ == '__main__':
    asyncio.run(main())
