"""마케팅 추적 수집기 (11/06, 2단계 ①).
게임 하나의 KR 광고를 Meta 광고 라이브러리 · Google 광고 투명성 센터에서 모아 data/ads/ 에 저장 (사이트가 Pages 로 읽음).

방식: 패키지명 검색은 두 매체 모두 안 되므로 '패키지로 걸러 광고 계정을 찾고 → 그 계정으로 수집'
  · Meta  : 게임명 키워드 검색 → 연결 링크에 패키지가 들어간 광고의 '페이지' → 그 페이지 광고 전체 (페이지 ID 를 주면 바로)
  · Google: 광고주 이름 검색 → 후보 계정의 KR 소재 미리보기에 패키지가 든 것만 (광고주 ID 를 주면 그 계정만)
저장: data/ads/<pkg>.json (최신 1회분만, 덮어씀) · data/ads/img/<pkg>/*.jpg (320px) · data/ads/index.json (추적한 앱 목록)
실행: python tracker/ad_track.py --name 탕탕에이스 --pkg com.survivor.ace [--google-adv Habby] [--meta-page 123] [--google-adv-id AR...]"""
import argparse, asyncio, hashlib, io, json, os, re, shutil, time, urllib.request
from urllib.parse import quote
from PIL import Image
from playwright.async_api import async_playwright

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
clean = lambda v: re.sub(r"^[\s'\"`]+|[\s'\"`]+$", '', v or '')   # 입력값 앞뒤 따옴표·공백 제거

def cut_json(html, anchor):
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

async def meta_query(pg, qs):
    """광고 라이브러리 한 번 조회 → (전체 건수, 광고 목록). 첫 응답이 403(보안 확인)이어도 풀릴 때까지 기다림"""
    url = f'https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=KR&is_targeted_country=false&media_type=all{qs}'
    await pg.goto(url, wait_until='domcontentloaded', timeout=60000)
    for _ in range(15):
        conn = cut_json(await pg.content(), 'search_results_connection')
        if conn: return conn['count'], [a for e in conn['edges'] for a in e['node']['collated_results']]
        await pg.wait_for_timeout(1500)
    return None, []

def meta_norm(a):
    s = a['snapshot']; card = (s.get('cards') or [{}])[0] if s.get('cards') else {}
    v = (s.get('videos') or [{}])[0] if s.get('videos') else {}
    img = (s.get('images') or [{}])[0] if s.get('images') else {}
    link = s.get('link_url') or card.get('link_url') or ''
    return {'id': a['ad_archive_id'], 'page': s.get('page_name'), 'page_id': a.get('page_id'), 'start': a.get('start_date'), 'end': a.get('end_date'),
            'active': a.get('is_active'), 'plat': [p[:2] for p in (a.get('publisher_platform') or [])], 'fmt': s.get('display_format'),
            'body': ((s.get('body') or {}).get('text') or card.get('body') or '')[:300], 'title': (s.get('title') or card.get('title') or '')[:150],
            'store': 'iOS' if 'apple.com' in link else 'Play' if 'play.google' in link else '기타', 'link': link.split('?')[0][:200],
            'variants': a.get('collation_count') or 1,
            'thumb': v.get('video_preview_image_url') or img.get('resized_image_url') or img.get('original_image_url') or card.get('resized_image_url') or ''}

async def meta(ctx, keys, pkg, page_id):
    pg = await ctx.new_page(); out = {'query': ', '.join(keys), 'pages': [], 'count': 0, 'ads': [], 'log': []}
    pages = {}
    if page_id:
        pages[page_id] = '(지정)'
    else:
        # 1) 게임명 키워드(쉼표로 여러 개 · 각각 콜론 앞/뒤 · 콜론 뺀 형태) → 패키지가 링크에 든 광고의 페이지
        ads, seen = [], []
        names = list(dict.fromkeys(v for k in keys for v in [k, k.split(':')[0].strip(), k.split(':')[-1].strip(), k.replace(':', ' ')]))
        for q in names:
            if len(q) < 2: continue
            cnt, ads = await meta_query(pg, f'&q={quote(q)}&search_type=keyword_unordered')
            seen += ads
            hit = [a for a in ads if pkg in json.dumps(a['snapshot'], ensure_ascii=False)]
            out['log'].append({'keyword': q, 'count': cnt, 'got': len(ads), 'pkg_hit': len(hit)})
            for a in hit: pages[a.get('page_id')] = a['snapshot'].get('page_name')
            if pages: break
            await pg.wait_for_timeout(1200)
        # 패키지가 걸린 광고가 없으면(전부 iOS 링크 등) 페이지 이름이 게임명과 같은 페이지로
        if not pages and seen:
            nks = {re.sub(r'\W', '', k).lower() for k in keys}
            for a in seen:
                if re.sub(r'\W', '', a['snapshot'].get('page_name') or '').lower() in nks: pages[a.get('page_id')] = a['snapshot'].get('page_name')
            if pages: out['log'].append({'fallback': 'page_name'})
    # 2) 페이지 광고 전체 (비로그인 1회 30개 → 정렬·형식을 바꿔 여러 번)
    ads = {}
    for pid in list(pages)[:2]:
        for v in ['', '&sort_data[mode]=total_impressions&sort_data[direction]=desc', '&media_type=video', '&media_type=image', '&active_status=active']:
            cnt, got = await meta_query(pg, f'&search_type=page&view_all_page_id={pid}{v}')
            if v == '' and cnt: out['count'] += cnt
            for a in got: ads[a['ad_archive_id']] = a
            await pg.wait_for_timeout(1000)
        out['pages'].append({'id': pid, 'name': pages[pid] or next((a['snapshot'].get('page_name') for a in ads.values() if a.get('page_id') == pid), '')})
    out['ads'] = sorted((meta_norm(a) for a in ads.values()), key=lambda x: -(x['start'] or 0))
    return out

GOOGLE_JS = r"""
async ([q, advId, pkg]) => {
  const rpc = async (name, req) => (await fetch(`/anji/_/rpc/${name}?authuser=`, { method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: 'f.req=' + encodeURIComponent(JSON.stringify(req)) })).json();
  let advs = advId ? [{ id: advId, name: '(지정)' }] : [];
  if (!advId && q) { const sg = await rpc('SearchService/SearchSuggestions', { 1: q, 2: 10, 3: 10 }); advs = (sg[1] || []).filter(x => x[1]).map(x => ({ id: x[1][2], name: (x[1][1] || '').trim(), region: x[1][3] })).slice(0, 5); }
  const res = { candidates: [], ads: [] };
  for (const a of advs) {
    let all = [], tok = null;
    for (let p = 0; p < 10; p++) {
      const req = { 2: 100, 3: { 12: { 1: '', 2: true }, 13: { 1: [a.id] }, 8: [2410] }, 7: { 1: 1, 2: 0, 3: 2410 } };
      if (tok) req[4] = tok;
      const j = await rpc('SearchService/SearchCreatives', req); all = all.concat(j[1] || []); tok = j[2]; if (!tok) break;
    }
    const pk = {}; let hit = 0;
    for (let i = 0; i < all.length; i += 15) {
      await Promise.all(all.slice(i, i + 15).map(async c => {
        const u = c[3]?.[1]?.[4]; if (!u) return;
        let t = ''; try { t = await (await fetch(u)).text(); } catch (e) { return; }
        new Set((t.match(/(?:com|jp|kr|net|io|co)\.[a-z0-9_]+\.[a-z0-9_.]+/gi) || []).map(x => x.replace(/\.+$/, '')).filter(x => !/^com\.(google|android)\./i.test(x))).forEach(x => { pk[x] = (pk[x] || 0) + 1; });
        if (!t.includes(pkg)) return; hit++;
        const yt = (t.match(/ytimg\.com\/vi\/([\w-]+)/) || [])[1] || '';
        const img = ((t.match(/https:\\?\/\\?\/tpc\.googlesyndication\.com\\?\/(?:archive\\?\/)?simgad[^"'\\\s)]+/) || [])[0] || '').replace(/\\\//g, '/');
        const lines = [...new Set((t.match(/[가-힣A-Za-z0-9][^"\\<>{}]{3,80}/g) || []).map(s => s.replace(/^x(27|3e)/, '').trim()).filter(s => /[가-힣]/.test(s) && !/광고|의견|사용자|검토|종료|감사|설치|계속|문제|다운로드 수|카테고리|앱 관련|신규 앱|평점|건너뛰기|초 후|이 광고|닫기|스폰서|개발자|받기|앱 열기/.test(s)))].slice(0, 4);
        res.ads.push({ id: c[2], adv: a.id, fmt: c[4], first: +c[6]?.[1], last: +c[7]?.[1], days: c[13], yt, img, lines });
      }));
    }
    res.candidates.push({ ...a, creativesKR: all.length, matched: hit, topPkgs: Object.entries(pk).sort((x, y) => y[1] - x[1]).slice(0, 5) });
  }
  return res;
}
"""

async def google(ctx, adv_q, adv_id, pkg):
    pg = await ctx.new_page(); out = {'query': adv_q, 'advertisers': [], 'candidates': [], 'ads': []}
    await pg.goto('https://adstransparency.google.com/?region=KR&hl=ko', wait_until='domcontentloaded', timeout=60000)
    await pg.wait_for_timeout(2500)
    r = await pg.evaluate(GOOGLE_JS, [adv_q, adv_id, pkg])
    out['candidates'] = r['candidates']; out['ads'] = sorted(r['ads'], key=lambda x: -(x['last'] or 0))
    out['advertisers'] = [{'id': c['id'], 'name': c['name']} for c in r['candidates'] if c['matched']]
    return out

def save_thumb(url, d):
    if not url: return ''
    key = hashlib.md5(url.split('?')[0].encode()).hexdigest()[:12]; path = os.path.join(d, key + '.jpg')
    if not os.path.exists(path):
        try:
            b = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=30).read()
            im = Image.open(io.BytesIO(b)).convert('RGB'); im.thumbnail((320, 320)); im.save(path, quality=78)
        except Exception as e:
            print('thumb fail', url[:70], e); return ''
    return key + '.jpg'

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True); ap.add_argument('--pkg', required=True)
    ap.add_argument('--google-adv', default=''); ap.add_argument('--meta-page', default=''); ap.add_argument('--google-adv-id', default='')
    a = ap.parse_args(); pkg = clean(a.pkg)
    keys = [clean(k) for k in a.name.split(',') if clean(k)]; name = keys[0]   # '먼작귀 포켓, 먼작귀' → 검색어 여러 개, 첫 값이 표시 이름
    t0 = time.time(); res = {'name': name, 'pkg': pkg, 'country': 'KR', 'tracked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(locale='ko-KR', user_agent=UA, viewport={'width': 1366, 'height': 900})
        try: res['meta'] = await meta(ctx, keys, pkg, clean(a.meta_page))
        except Exception as e: res['meta'] = {'error': str(e)[:300], 'ads': []}
        try: res['google'] = await google(ctx, clean(a.google_adv), clean(a.google_adv_id), pkg)
        except Exception as e: res['google'] = {'error': str(e)[:300], 'ads': []}
        await b.close()
    # 썸네일: 앱별 폴더를 새로 (최신 1회분만 보관)
    d = os.path.join(ROOT, 'data', 'ads', 'img', pkg); shutil.rmtree(d, ignore_errors=True); os.makedirs(d, exist_ok=True)
    for x in res['meta'].get('ads', []): x['thumb'] = save_thumb(x.pop('thumb', ''), d)
    for x in res['google'].get('ads', []): x['thumb'] = save_thumb(f"https://i.ytimg.com/vi/{x['yt']}/hqdefault.jpg" if x.get('yt') else x.get('img'), d)
    res['sec'] = round(time.time() - t0, 1)
    os.makedirs(os.path.join(ROOT, 'data', 'ads'), exist_ok=True)
    json.dump(res, open(os.path.join(ROOT, 'data', 'ads', f'{pkg}.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    ip = os.path.join(ROOT, 'data', 'ads', 'index.json')
    idx = json.load(open(ip, encoding='utf-8')) if os.path.exists(ip) else []
    idx = [x for x in idx if x['pkg'] != pkg] + [{'pkg': pkg, 'name': name, 'tracked_at': res['tracked_at'],
           'meta': len(res['meta'].get('ads', [])), 'meta_total': res['meta'].get('count', 0), 'google': len(res['google'].get('ads', []))}]
    json.dump(sorted(idx, key=lambda x: x['tracked_at'], reverse=True), open(ip, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    summary = {'name': name, 'pkg': pkg, 'sec': res['sec'],
               'meta': {k: res['meta'].get(k) for k in ('pages', 'count', 'log', 'error')} | {'ads': len(res['meta'].get('ads', []))},
               'google': {'advertisers': res['google'].get('advertisers'), 'candidates': res['google'].get('candidates'), 'ads': len(res['google'].get('ads', [])), 'error': res['google'].get('error')}}
    print(json.dumps(summary, ensure_ascii=False, indent=1))

asyncio.run(main())
