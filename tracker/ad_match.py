"""마케팅 추적: 게임 ↔ 광고 계정 자동 매칭 (11/06 시험).
대상: 오늘 한국 구글 인기 TOP5 + 매출 TOP5 (data/snapshot.json). 광고 수집은 하지 않고 '어느 계정이 이 게임 광고를 하나'만 찾는다.
  1) 기본 정보: 순위 데이터(이름·패키지·퍼블리셔) + Play 스토어(개발자 법인명·웹사이트) + App Store 검색 API(iOS 앱 ID·판매자·URL)
  2) Meta: 게임명 변형·퍼블리셔명으로 검색 → 광고 링크에 Play 패키지 또는 iOS 앱 ID 가 든 광고의 '페이지'를 확정
  3) Google: 이름(퍼블리셔 · 법인 · 판매자 · Meta 페이지 — 법인 표기를 뗀 알맹이 포함) + 도메인(웹사이트 · 문의 메일 · 패키지명으로 추천받은 것)으로 광고주 후보
     → KR 소재에 패키지/iOS ID 가 든 계정만 확정 (ad_terms.py · ad_google_match.js)
결과: data/ads/match.json (게임별 매칭 결과 + 무엇으로 찾았는지 + 시도한 후보)
실행: python tracker/ad_match.py"""
import asyncio, json, os, re, time, urllib.request
from urllib.parse import quote, urlparse
from playwright.async_api import async_playwright
from ad_track import UA, meta_query, nz
from ad_terms import JUNK, name_terms, domains_of, pkg_hints

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

async def meta_match(pg, g, ios_id):
    keys = name_variants(g['name']) + ([g['publisher']] if g.get('publisher') else [])
    ids = [g['pkg']] + ([f'id{ios_id}'] if ios_id else [])
    tried = []
    for q in keys:
        cnt, ads = await meta_query(pg, f'&q={quote(q)}&search_type=keyword_unordered')
        hit = {}
        for a in ads:
            sj = json.dumps(a['snapshot'], ensure_ascii=False)
            for i in ids:
                if i in sj: hit[a.get('page_id')] = (a['snapshot'].get('page_name'), 'Play 패키지' if i == g['pkg'] else 'iOS 앱 ID')
        tried.append({'q': q, 'count': cnt, 'hit': len(hit)})
        if hit: return {'pages': [{'id': k, 'name': v[0]} for k, v in hit.items()], 'by': f"검색어 '{q}' → 광고 링크의 {next(iter(hit.values()))[1]}", 'tried': tried}
        await pg.wait_for_timeout(1200)
    return {'pages': [], 'by': '', 'tried': tried}

# Google 광고주 찾기 (11/13 고도화): 페이지 안에서 도는 부분은 ad_google_match.js, 검색어 만들기는 ad_terms.py
_js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ad_google_match.js'), encoding='utf-8').read()
GOOGLE_MATCH_JS = _js[_js.index('async ('):]

async def match_game(ctx, mpg, gpg, g, days=90):
    """게임 1개 매칭: Meta 페이지 · Google 광고주 (Google 은 최근 days 일 소재 기준)"""
    s0 = time.time()
    pi = await play_info(ctx, g['pkg'])
    ap = appstore(g['name'], g.get('publisher', ''))
    ios = ap.get('ios_id', '')
    try: m = await meta_match(mpg, g, ios)
    except Exception as e: m = {'pages': [], 'by': '', 'error': str(e)[:160]}
    # 이름은 출처를 가리지 않고 모두: 순위 데이터의 퍼블리셔 · Play 법인명 · App Store 판매자/개발자 · 찾은 Meta 페이지 이름 (한글 · 영문이 섞여 들어옴)
    names = name_terms([g.get('publisher'), pi.get('legal'), ap.get('seller'), ap.get('artist')] + [x.get('name') for x in m.get('pages', [])])
    domains = domains_of([pi.get('site', ''), ap.get('seller_url', '')], [pi.get('email', '')])
    hints = pkg_hints(g['pkg'])
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
