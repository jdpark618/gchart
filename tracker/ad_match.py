"""마케팅 추적: 게임 ↔ 광고 계정 자동 매칭 (11/06 시험).
대상: 오늘 한국 구글 인기 TOP5 + 매출 TOP5 (data/snapshot.json). 광고 수집은 하지 않고 '어느 계정이 이 게임 광고를 하나'만 찾는다.
  1) 기본 정보: 순위 데이터(이름·패키지·퍼블리셔) + Play 스토어(개발자 법인명·웹사이트) + App Store 검색 API(iOS 앱 ID·판매자·URL)
  2) Meta: 게임명 변형·퍼블리셔명으로 검색 → 광고 링크에 Play 패키지 또는 iOS 앱 ID 가 든 광고의 '페이지'를 확정
  3) Google: 퍼블리셔명·법인명·판매자명(이름 검색) + 웹사이트 도메인(도메인 검색)으로 광고주 후보 → KR 소재에 패키지/iOS ID 가 든 계정만 확정
결과: data/ads/match.json (게임별 매칭 결과 + 무엇으로 찾았는지 + 시도한 후보)
실행: python tracker/ad_match.py"""
import asyncio, json, os, re, time, urllib.request
from urllib.parse import quote, urlparse
from playwright.async_api import async_playwright
from ad_track import UA, meta_query, nz

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
    return {'ios_id': str(best['trackId']), 'ios_name': best.get('trackName'), 'seller': best.get('sellerName'), 'seller_url': best.get('sellerUrl') or ''}

async def play_info(ctx, pkg):
    """Play 스토어: 개발자 표시명 · 법인명(개발자 소개) · 웹사이트"""
    pg = await ctx.new_page(); info = {}
    try:
        await pg.goto(f'https://play.google.com/store/apps/details?id={pkg}&hl=ko&gl=KR', wait_until='domcontentloaded', timeout=45000)
        h = await pg.content(); i = h.find('개발자 소개')
        toks = [x.strip() for x in re.sub(r'<[^>]+>', '\n', h[i:i + 3000]).split('\n') if x.strip()] if i >= 0 else []
        info['legal'] = toks[1] if len(toks) > 1 else ''   # '개발자 소개' 다음 첫 줄 = 법인명 (클릭 없이 HTML에 있음)
        hrefs = await pg.eval_on_selector_all('a[href^="http"]', 'es => es.map(e => e.href)')
        info['site'] = next((u for u in hrefs if not JUNK.search(u)), '')
    except Exception as e: info['error'] = str(e)[:120]
    await pg.close(); return info

JUNK = re.compile(r'google\.|gstatic|youtube|android\.com|apple\.com|ftc\.go\.kr|akamai|cloudfront|cdn|policies|privacy|facebook|instagram', re.I)
def host(u):
    h = urlparse(u if '://' in u else 'http://' + u).hostname or ''
    return re.sub(r'^www\.', '', h)
def domains_of(urls):
    """주소 → 검색할 도메인: 원래 호스트 + 대표 도메인(dokkaebi.kakaogames.com → kakaogames.com), 공정위·CDN 등 엉뚱한 주소 제외"""
    out = []
    for u in urls:
        h = host(u)
        if not h or '.' not in h or JUNK.search(h): continue
        parts = h.split('.'); n = 3 if len(parts) > 2 and parts[-2] in ('co', 'com', 'or', 'ne', 'go') else 2
        out += [h, '.'.join(parts[-n:])]
    return list(dict.fromkeys(out))

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

GOOGLE_MATCH_JS = r"""
async ([names, domains, ids, since]) => {
  const rpc = async (name, req) => (await fetch(`/anji/_/rpc/${name}?authuser=`, { method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: 'f.req=' + encodeURIComponent(JSON.stringify(req)) })).json();
  const cand = new Map();   // 광고주 ID → {name, from}
  for (const n of names) {
    const sg = await rpc('SearchService/SearchSuggestions', { 1: n, 2: 10, 3: 10 });
    (sg[1] || []).filter(x => x[1]).slice(0, 4).forEach(x => { if (!cand.has(x[1][2])) cand.set(x[1][2], { name: (x[1][1] || '').trim(), from: `이름 '${n}'` }); });
  }
  const viaDomain = new Set();
  for (const d of domains) {
    const r = await rpc('SearchService/SearchCreatives', { 2: 100, 3: { 12: { 1: d, 2: true }, 8: [2410] }, 7: { 1: 1, 2: 0, 3: 2410 } });
    (r[1] || []).forEach(c => { viaDomain.add(c[12]); if (!cand.has(c[1])) cand.set(c[1], { name: c[12], from: `도메인 '${d}'` }); });
  }
  /* 2차: 도메인에서 나온 광고주 이름(예 '카카오게임즈')으로 다시 이름 검색 → 같은 회사의 다른 계정(예 '주식회사 카카오게임즈')까지 */
  for (const n of viaDomain) {
    const sg = await rpc('SearchService/SearchSuggestions', { 1: n, 2: 10, 3: 10 });
    (sg[1] || []).filter(x => x[1] && (x[1][3] === 'KR' || !x[1][3])).slice(0, 4).forEach(x => { if (!cand.has(x[1][2])) cand.set(x[1][2], { name: (x[1][1] || '').trim(), from: `도메인→이름 '${n}'` }); });
  }
  /* 확인 순서: 이름으로 찾은 계정(퍼블리셔·법인) → 도메인→이름 → 도메인만(대행사 가능성).
     미리보기 요청이 너무 많으면 구글이 막아(429) 뒤 계정이 모두 '없음'으로 나오므로: 게임당 미리보기 2000건 한도 · 10건씩 · 실패는 따로 셈 */
  const rank = f => f.startsWith('이름') ? 0 : f.startsWith('도메인→') ? 1 : 2;
  const order = [...cand.entries()].sort((x, y) => rank(x[1].from) - rank(y[1].from));
  const out = { matched: [], checked: [] }; let budget = 2000;
  for (const [id, a] of order) {
    if (budget <= 0) { out.checked.push({ id, name: a.name, from: a.from, skipped: '미리보기 한도' }); continue; }
    let all = [], tok = null;
    for (let p = 0; p < 15; p++) {   // 소재가 많은 계정(예 Proxima Beta)도 1500개까지 (최근 노출순)
      const req = { 2: 100, 3: { 12: { 1: '', 2: true }, 13: { 1: [id] }, 8: [2410] }, 7: { 1: 1, 2: 0, 3: 2410 } };
      if (tok) req[4] = tok;
      const j = await rpc('SearchService/SearchCreatives', req); all = all.concat(j[1] || []); tok = j[2]; if (!tok) break;
    }
    const total = all.length; all = all.filter(c => +(c[7]?.[1] || 0) >= since);   // 최근 N일(기본 90일) 안에 노출된 소재만 미리보기
    let hit = 0, seen = 0, fail = 0;
    for (let i = 0; i < all.length && !hit && budget > 0; i += 10) {
      const res = await Promise.all(all.slice(i, i + 10).map(async c => { const u = c[3]?.[1]?.[4]; if (!u) return 0; seen++; budget--;
        try { const r = await fetch(u); if (!r.ok) { fail++; return 0; } const t = await r.text(); return ids.some(x => t.includes(x)) ? 1 : 0; } catch (e) { fail++; return 0; } }));
      hit += res.reduce((s, v) => s + v, 0);
      await new Promise(r => setTimeout(r, 120));
    }
    out.checked.push({ id, name: a.name, from: a.from, creativesKR: total, recent: all.length, previews: seen, failed: fail, hit: hit > 0 });
    if (hit) out.matched.push({ id, name: a.name, by: `${a.from} → 소재에 패키지/iOS ID` });
  }
  return out;
}
"""

CORP = re.compile(r"\(주\)|㈜|주식회사|유한회사|\(유\)|[,\s]+(co\.?,?\s*ltd|corporation|corp|inc|ltd|limited|llc|pte|gmbh|company)\.?(?=[\s,.]|$)", re.I)
def corp_variants(names):
    """광고주 이름 검색어: 원래 이름 + 법인 표기를 뗀 이름 (11/13).
    같은 회사가 '(주)배드빈즈' · '배드빈즈' 두 계정으로 게임마다 따로 광고하는 경우, 뗀 이름으로 찾아야 둘 다 후보에 오름"""
    out = []
    for n in names:
        if not n: continue
        out.append(n); v = re.sub(r'\s+', ' ', CORP.sub(' ', n)).strip(' ,.')
        if len(v) >= 2: out.append(v)
    return list(dict.fromkeys(out))

async def match_game(ctx, mpg, gpg, g, days=90):
    """게임 1개 매칭: Meta 페이지 · Google 광고주 (Google 은 최근 days 일 소재 기준)"""
    s0 = time.time()
    pi = await play_info(ctx, g['pkg'])
    ap = appstore(g['name'], g.get('publisher', ''))
    ios = ap.get('ios_id', '')
    try: m = await meta_match(mpg, g, ios)
    except Exception as e: m = {'pages': [], 'by': '', 'error': str(e)[:160]}
    names = corp_variants([g.get('publisher'), pi.get('legal'), ap.get('seller')])
    domains = domains_of([pi.get('site', ''), ap.get('seller_url', '')])
    try: gm = await gpg.evaluate(GOOGLE_MATCH_JS, [names, domains, [g['pkg']] + ([f'id{ios}'] if ios else []), int(time.time()) - days * 86400])
    except Exception as e: gm = {'matched': [], 'checked': [], 'error': str(e)[:160]}
    return {**g, 'play': pi, 'appstore': ap, 'meta': m, 'google': {**gm, 'names': names, 'domains': domains}, 'sec': round(time.time() - s0, 1)}

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
