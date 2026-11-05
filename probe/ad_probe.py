"""광고 추적 1단계 검증 (11/06): GitHub Actions 서버에서 Meta 광고 라이브러리 · Google 광고 투명성 센터가 수집되는지만 확인.
결과는 probe/result.json 으로 저장 (워크플로가 ad-probe 브랜치에 올림). 화면·데이터 저장 기능 없음.
실행: python probe/ad_probe.py "<Meta 검색어>" "<Google 광고주 검색어>" "<Google 패키지명>" """
import asyncio, json, sys, time
from urllib.parse import quote
from playwright.async_api import async_playwright

META_Q, G_ADV, G_PKG = (sys.argv[1:4] + ['탕탕에이스', '카카오게임즈', 'com.kakaogames.dokkaebi'][len(sys.argv) - 1:])[:3]
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

def cut_json(html, anchor):
    """페이지에 박힌 JSON에서 anchor 다음 첫 '{"count"' 객체를 괄호 짝으로 잘라 냄"""
    i = html.find(anchor)
    if i < 0: return None
    k = html.find('{"count"', i)
    if k < 0: return None
    d = 0
    for j in range(k, len(html)):
        if html[j] == '{': d += 1
        elif html[j] == '}':
            d -= 1
            if d == 0: return json.loads(html[k:j + 1])
    return None

async def meta(ctx):
    out = {'query': META_Q, 'tries': []}
    pg = await ctx.new_page()
    variants = ['', '&sort_data[mode]=total_impressions&sort_data[direction]=desc', '&media_type=video', '&media_type=image']
    ads = {}
    for v in variants:
        url = f'https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=KR&is_targeted_country=false&media_type=all&q={quote(META_Q)}&search_type=keyword_unordered{v}'
        t0 = time.time(); rec = {'variant': v or 'default'}
        try:
            r = await pg.goto(url, wait_until='domcontentloaded', timeout=60000)
            rec['status'] = r.status if r else None
            conn = None
            for _ in range(15):   # 첫 응답이 403(Client challenge)이어도 브라우저가 풀고 다시 그리는지 기다림
                html = await pg.content()
                conn = cut_json(html, 'search_results_connection')
                if conn: break
                await pg.wait_for_timeout(1500)
            rec['title'] = (await pg.title())[:60]
            if conn:
                items = [a for e in conn['edges'] for a in e['node']['collated_results']]
                rec['count'] = conn['count']; rec['got'] = len(items)
                for a in items: ads[a['ad_archive_id']] = a
            else:
                rec['got'] = 0; rec['login_wall'] = 'login' in html.lower() and 'ads/library' not in pg.url
        except Exception as e:
            rec['error'] = str(e)[:200]
        rec['sec'] = round(time.time() - t0, 1)
        out['tries'].append(rec)
        await pg.wait_for_timeout(1500)
    out['unique_ads'] = len(ads)
    out['sample'] = [{'id': a['ad_archive_id'], 'page': a['snapshot'].get('page_name'), 'start': a.get('start_date'),
                      'active': a.get('is_active'), 'body': ((a['snapshot'].get('body') or {}).get('text') or '')[:60]} for a in list(ads.values())[:5]]
    return out

GOOGLE_JS = r"""
async ([adv, pkg]) => {
  const rpc = async (name, req) => (await fetch(`/anji/_/rpc/${name}?authuser=`, { method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: 'f.req=' + encodeURIComponent(JSON.stringify(req)) })).json();
  const sg = await rpc('SearchService/SearchSuggestions', { 1: adv, 2: 10, 3: 10 });
  const advs = (sg[1] || []).filter(x => x[1]).map(x => ({ name: x[1][1], id: x[1][2], region: x[1][3] })).slice(0, 4);
  const res = { advertisers: advs, perAdv: [] };
  for (const a of advs) {
    let all = [], tok = null;
    for (let p = 0; p < 6; p++) {
      const req = { 2: 100, 3: { 12: { 1: '', 2: true }, 13: { 1: [a.id] }, 8: [2410] }, 7: { 1: 1, 2: 0, 3: 2410 } };
      if (tok) req[4] = tok;
      const j = await rpc('SearchService/SearchCreatives', req);
      all = all.concat(j[1] || []); tok = j[2]; if (!tok) break;
    }
    let match = 0, checked = 0, sample = [];
    for (let i = 0; i < all.length; i += 15) {
      await Promise.all(all.slice(i, i + 15).map(async c => {
        const u = c[3]?.[1]?.[4]; if (!u) return; checked++;
        try { const t = await (await fetch(u)).text(); if (t.includes(pkg)) { match++; if (sample.length < 3) sample.push({ id: c[2], first: c[6]?.[1], last: c[7]?.[1], days: c[13] }); } } catch (e) {}
      }));
    }
    res.perAdv.push({ ...a, creativesKR: all.length, previewsChecked: checked, matchPkg: match, sample });
  }
  return res;
}
"""

async def google(ctx):
    out = {'advertiser_query': G_ADV, 'pkg': G_PKG}
    pg = await ctx.new_page(); t0 = time.time()
    try:
        r = await pg.goto('https://adstransparency.google.com/?region=KR&hl=ko', wait_until='domcontentloaded', timeout=60000)
        out['status'] = r.status if r else None
        await pg.wait_for_timeout(2500)
        out.update(await pg.evaluate(GOOGLE_JS, [G_ADV, G_PKG]))
    except Exception as e:
        out['error'] = str(e)[:300]
    out['sec'] = round(time.time() - t0, 1)
    return out

async def main():
    res = {'when_utc': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(locale='ko-KR', user_agent=UA, viewport={'width': 1366, 'height': 900})
        res['meta'] = await meta(ctx)
        res['google'] = await google(ctx)
        await b.close()
    open('probe/result.json', 'w', encoding='utf-8').write(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False)[:3000])

asyncio.run(main())
