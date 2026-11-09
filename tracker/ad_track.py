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
            'thumb': v.get('video_preview_image_url') or img.get('resized_image_url') or img.get('original_image_url') or card.get('resized_image_url')
                     or card.get('video_preview_image_url') or card.get('original_image_url')   # 동적 소재(DCO)는 이미지가 카드·추가 소재 칸에 있음
                     or ((s.get('extra_videos') or [{}])[0] or {}).get('video_preview_image_url') or ((s.get('extra_images') or [{}])[0] or {}).get('resized_image_url') or ''}

async def meta(ctx, keys, pkg, page_id):
    pg = await ctx.new_page(); out = {'query': ', '.join(keys), 'pages': [], 'count': 0, 'ads': [], 'log': []}
    pages = {}
    if page_id:
        for pid in page_id.split(','): pages[pid.strip()] = ''   # 매칭에서 찾은 페이지 ID (쉼표로 여러 개)
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
    # 2) 페이지의 한국어 광고 (비로그인 1회 30개 → 정렬·형식·상태를 바꿔 여러 번)
    #    국가 KR 조건은 '한국에 한 번이라도 노출된 광고'라 글로벌 공용(영어 등) 소재가 상위를 덮음 → 광고 언어 한국어로 한정 (11/06)
    ads = {}
    for pid in list(pages)[:3]:
        for v in ['', '&sort_data[mode]=total_impressions&sort_data[direction]=desc', '&media_type=video', '&media_type=image', '&active_status=active', '&active_status=inactive']:
            cnt, got = await meta_query(pg, f'&search_type=page&view_all_page_id={pid}&content_languages[0]=ko{v}')
            if v == '' and cnt: out['count'] += cnt
            for a in got: ads[a['ad_archive_id']] = a
            await pg.wait_for_timeout(1000)
        out['pages'].append({'id': pid, 'name': pages[pid] or next((a['snapshot'].get('page_name') for a in ads.values() if a.get('page_id') == pid), '')})
    out['ads'] = sorted((meta_norm(a) for a in ads.values()), key=lambda x: -(x['start'] or 0))
    out['lang'] = 'ko'
    return out

GOOGLE_JS = r"""
async ([q, advId, pkg, ios, since, known]) => {
  const knownSet = new Set(known || []);   // 이미 저장된 소재 ID → 미리보기 없이 날짜만 갱신
  const ids = [pkg].concat(ios ? ['id' + ios] : []);   // Play 패키지 또는 iOS 앱 ID 가 든 소재
  const rpc = async (name, req) => (await fetch(`/anji/_/rpc/${name}?authuser=`, { method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: 'f.req=' + encodeURIComponent(JSON.stringify(req)) })).json();
  let advs = advId ? advId.split(',').map(x => { const [id, name] = x.split('|'); return { id: id.trim(), name: (name || '').trim() }; }) : [];   // 'AR...|광고주명' 쉼표로 여러 개
  if (!advId && q) { const sg = await rpc('SearchService/SearchSuggestions', { 1: q, 2: 10, 3: 10 }); advs = (sg[1] || []).filter(x => x[1]).map(x => ({ id: x[1][2], name: (x[1][1] || '').trim(), region: x[1][3] })).slice(0, 5); }
  const res = { candidates: [], ads: [], seen: [] };
  for (const a of advs) {
    let all = [], tok = null;
    for (let p = 0; p < 12; p++) {
      const req = { 2: 100, 3: { 12: { 1: '', 2: true }, 13: { 1: [a.id] }, 8: [2410] }, 7: { 1: 1, 2: 0, 3: 2410 } };
      if (tok) req[4] = tok;
      const j = await rpc('SearchService/SearchCreatives', req); all = all.concat(j[1] || []); tok = j[2]; if (!tok) break;
    }
    const total = all.length;
    all = all.filter(c => +(c[7]?.[1] || 0) >= since);   // 최근 N일(기본 30일) 안에 노출된 소재만
    all.filter(c => knownSet.has(c[2])).forEach(c => res.seen.push({ id: c[2], first: +c[6]?.[1], last: +c[7]?.[1], days: c[13] }));
    all = all.filter(c => !knownSet.has(c[2]));   // 새 소재만 미리보기
    const pk = {}; let hit = 0, fail = 0;
    /* 미리보기 요청이 너무 많으면 구글이 막으므로 10건씩 간격을 두고, 계정당 1200건까지 */
    for (let i = 0; i < Math.min(all.length, 1200); i += 10) {
      await new Promise(r => setTimeout(r, 120));
      await Promise.all(all.slice(i, i + 10).map(async c => {
        const u = c[3]?.[1]?.[4]; if (!u) return;
        let t = ''; try { const r = await fetch(u); if (!r.ok) { fail++; return; } t = await r.text(); } catch (e) { fail++; return; }
        new Set((t.match(/(?:com|jp|kr|net|io|co)\.[a-z0-9_]+\.[a-z0-9_.]+/gi) || []).map(x => x.replace(/\.+$/, '')).filter(x => !/^com\.(google|android)\./i.test(x))).forEach(x => { pk[x] = (pk[x] || 0) + 1; });
        if (!ids.some(x => t.includes(x))) return; hit++;
        // 유튜브 영상 ID: 썸네일 주소(ytimg.com/vi/ID) 또는 앱 광고 영상 설정값(video_videoId: 'ID') — 후자는 영상형 앱 광고에서만 보임 (11/06)
        const yt = (t.match(/ytimg\.com\/vi\/([\w-]+)/) || t.match(/videoId(?:\\x27|\\x22|['":\s=])+([\w-]{11})/) || [])[1] || '';
        // 이미지: 배너(simgad) → 없으면 스토어 기반 앱 광고의 대표 이미지(coverImage) · 첫 스크린샷 (11/06: 영상 ID 도 배너도 없는 앱 광고 보완)
        const asset = k => { const m = t.match(new RegExp(k + '(?:\\\\x27|\\\\x22|[\'":\\s=])+((?:https:)?//lh3\\.googleusercontent\\.com/[\\w-]+)')); return m ? (m[1].startsWith('//') ? 'https:' + m[1] : m[1]) : ''; };
        const img = ((t.match(/https:\\?\/\\?\/tpc\.googlesyndication\.com\\?\/(?:archive\\?\/)?simgad[^"'\\\s)]+/) || [])[0] || '').replace(/\\\//g, '/') || (yt ? '' : asset('coverImage') || asset('screenshot1'));
        const lines = [...new Set((t.match(/[가-힣A-Za-z0-9][^"\\<>{}]{3,80}/g) || []).map(s => s.replace(/^x(27|3e)/, '').trim()).filter(s => /[가-힣]/.test(s) && !/광고|의견|사용자|검토|종료|감사|설치|계속|문제|다운로드 수|카테고리|앱 관련|신규 앱|평점|건너뛰기|초 후|이 광고|닫기|스폰서|개발자|받기|앱 열기/.test(s)))].slice(0, 4);
        res.ads.push({ id: c[2], adv: a.id, fmt: c[4], first: +c[6]?.[1], last: +c[7]?.[1], days: c[13], yt, img, lines });
      }));
    }
    res.candidates.push({ ...a, creativesKR: total, newRecent: all.length, matched: hit, failed: fail, topPkgs: Object.entries(pk).sort((x, y) => y[1] - x[1]).slice(0, 5) });
  }
  return res;
}
"""

async def google(ctx, adv_q, adv_id, pkg, ios='', since=0, known=()):
    pg = await ctx.new_page(); out = {'query': adv_q, 'advertisers': [], 'candidates': [], 'ads': []}
    await pg.goto('https://adstransparency.google.com/?region=KR&hl=ko', wait_until='domcontentloaded', timeout=60000)
    await pg.wait_for_timeout(2500)
    r = await pg.evaluate(GOOGLE_JS, [adv_q, adv_id, pkg, ios, since, list(known)])
    out['candidates'] = r['candidates']; out['ads'] = sorted(r['ads'], key=lambda x: -(x['last'] or 0)); out['seen'] = r.get('seen', [])
    out['advertisers'] = [{'id': c['id'], 'name': c['name']} for c in r['candidates'] if c['matched'] or adv_id]
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

nz = lambda v: re.sub(r'[\s:：·\-]', '', v or '').lower()   # 이름 비교용: 띄어쓰기·콜론 무시
STORE_LB = re.compile(r'^(출시 예정|속도감 있는 게임|등록|사전 등록|지금 예약|예약하기|설치|받기|열기|앱 열기|개발자)$')

async def play_title(ctx, pkg):
    """Google Play 정식 이름 (문구에서 게임 이름 줄을 거르는 데 씀)"""
    pg = await ctx.new_page()
    await pg.goto(f'https://play.google.com/store/apps/details?id={pkg}&hl=ko&gl=KR', wait_until='domcontentloaded', timeout=45000)
    t = await pg.title()
    return re.sub(r'\s*-\s*Google Play.*$', '', t).strip()

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True); ap.add_argument('--pkg', required=True)
    ap.add_argument('--google-adv', default=''); ap.add_argument('--meta-page', default=''); ap.add_argument('--google-adv-id', default=''); ap.add_argument('--ios', default='')
    ap.add_argument('--publisher', default=''); ap.add_argument('--rematch', action='store_true')
    ap.add_argument('--ranks', default=''); ap.add_argument('--rank-date', default='')   # 'popular:2,sales:3' — 매일 TOP5 추적(daily_top5.py)에서 넘김
    a = ap.parse_args(); pkg = clean(a.pkg)
    keys = [clean(k) for k in a.name.split(',') if clean(k)]; name = keys[0]   # '먼작귀 포켓, 먼작귀' → 검색어 여러 개, 첫 값이 표시 이름
    t0 = time.time(); now = int(time.time()); res = {'name': name, 'pkg': pkg, 'country': 'KR', 'tracked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    # 계정 목록(data/ads/accounts.json): 매칭된 게임은 저장된 계정으로 · 처음이면(또는 --rematch) 매칭 전체(최근 90일 기준)
    ACC = os.path.join(ROOT, 'data', 'ads', 'accounts.json')
    accounts = json.load(open(ACC, encoding='utf-8')) if os.path.exists(ACC) else {}
    acc = None if a.rematch else accounts.get(pkg)
    prev_p = os.path.join(ROOT, 'data', 'ads', f'{pkg}.json')
    prev = json.load(open(prev_p, encoding='utf-8')) if os.path.exists(prev_p) and acc else {}   # 처음 매칭이면 이전 결과는 버리고 새로
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(locale='ko-KR', user_agent=UA, viewport={'width': 1366, 'height': 900})
        if not acc and not (a.meta_page or a.google_adv_id):
            from ad_match import match_game
            mpg = await ctx.new_page(); gpg = await ctx.new_page()
            await gpg.goto('https://adstransparency.google.com/?region=KR&hl=ko', wait_until='domcontentloaded', timeout=60000); await gpg.wait_for_timeout(2500)
            pub = clean(a.publisher) or next((r[6] for c in ('sales', 'popular') for r in json.load(open(os.path.join(ROOT, 'data', 'snapshot.json'), encoding='utf-8'))['charts']['gp']['kr'][c]['rows'] if r[4] == pkg), '')
            m = await match_game(ctx, mpg, gpg, {'name': name, 'pkg': pkg, 'publisher': pub}, days=90)
            acc = {'meta_pages': [x['id'] for x in m['meta'].get('pages', [])], 'google': m['google'].get('matched', []), 'ios': m['appstore'].get('ios_id', ''),
                   'meta_by': m['meta'].get('by', ''), 'matched_at': res['tracked_at']}
            accounts[pkg] = acc; res['match'] = {'meta_by': acc['meta_by'], 'google_by': [g['by'] for g in acc['google']]}
        elif not acc:
            acc = {'meta_pages': [x for x in clean(a.meta_page).split(',') if x], 'google': [{'id': x.split('|')[0], 'name': (x.split('|') + [''])[1]} for x in clean(a.google_adv_id).split(',') if x],
                   'ios': clean(a.ios), 'matched_at': res['tracked_at']}
            accounts[pkg] = acc
        meta_page = ','.join(acc.get('meta_pages', []))
        gadv = ','.join(f"{g['id']}|{g.get('name', '')}" for g in acc.get('google', []))
        try: res['meta'] = await meta(ctx, keys, pkg, meta_page) if meta_page else {'pages': [], 'count': 0, 'ads': [], 'log': [{'skip': 'Meta 계정 없음'}]}
        except Exception as e: res['meta'] = {'error': str(e)[:300], 'ads': []}
        # 재시도 (11/06): 이전엔 광고가 있었는데 이번 조회가 비면 일시 차단일 수 있어 30초 쉬고 한 번 더
        if meta_page and not res['meta'].get('ads') and prev.get('meta', {}).get('ads'):
            await asyncio.sleep(30)
            try: res['meta'] = await meta(ctx, keys, pkg, meta_page); res['meta'].setdefault('log', []).append({'retry': True})
            except Exception as e: res['meta'] = {'error': str(e)[:300], 'ads': []}
        known = [x['id'] for x in prev.get('google', {}).get('ads', []) if x.get('yt') or x.get('img')]   # 썸네일 없는 소재는 다시 미리보기 (영상 ID 추출 보완분 반영)
        try: res['google'] = await google(ctx, '', gadv, pkg, acc.get('ios', '') or clean(a.ios), now - (30 if prev else 90) * 86400, known) if gadv else {'advertisers': [], 'candidates': [], 'ads': []}
        except Exception as e: res['google'] = {'error': str(e)[:300], 'ads': []}
        # 재시도: 광고주 조회가 전부 0건(또는 오류)이면 일시 차단일 수 있어 30초 쉬고 한 번 더 (11/06 WOS 0건 사례)
        if gadv and (res['google'].get('error') or not any(c.get('creativesKR') for c in res['google'].get('candidates', []))):
            await asyncio.sleep(30)
            try: res['google'] = await google(ctx, '', gadv, pkg, acc.get('ios', '') or clean(a.ios), now - (30 if prev else 90) * 86400, known); res['google']['retry'] = True
            except Exception as e: res['google'] = {'error': str(e)[:300], 'ads': []}
        try: res['title'] = await play_title(ctx, pkg)
        except Exception: res['title'] = ''
        await b.close()
    # Google 문구 정리: 게임 이름(검색어·Play 정식 이름)과 그 조각 · 스토어 카드 라벨 · 깨진 따옴표(x22 등) 제거 → 실제 광고 문구만
    names = [nz(v) for v in keys + [res['title']] if nz(v)]
    for x in res['google'].get('ads', []):
        x['lines'] = [l for l in (re.sub(r'^x(22|27|3e)', '', s).strip() for s in x.get('lines', []))
                      if nz(l) and not STORE_LB.match(l) and not any(nz(l) in n for n in names)]
    # 썸네일: 새 소재만 받음 (이미 받은 파일은 그대로 · 같은 소재면 같은 파일 이름)
    d = os.path.join(ROOT, 'data', 'ads', 'img', pkg)
    if not prev: shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    for x in res['meta'].get('ads', []): x['thumb'] = save_thumb(x.pop('thumb', ''), d)
    for x in res['google'].get('ads', []): x['thumb'] = save_thumb(f"https://i.ytimg.com/vi/{x['yt']}/hqdefault.jpg" if x.get('yt') else x.get('img'), d)
    # 이전 결과와 병합: Meta 는 ID 기준 최신 값으로, Google 은 저장된 소재의 날짜를 목록 값으로 갱신 + 새 소재 추가
    # Meta 마지막 확인 시각: 비로그인 30개 한도로 그날 안 걸린 소재는 예전 '집행 중' 상태가 남으므로, 화면은 이 값이 마지막 추적일과 같을 때만 집행 중으로 봄
    for x in res['meta'].get('ads', []): x['seen'] = now
    if prev:
        mm = {x['id']: x for x in prev.get('meta', {}).get('ads', [])} if prev.get('meta', {}).get('lang') == 'ko' else {}   # 한국어 조회 이전(전체 언어) 결과는 버림
        mm.update({x['id']: x for x in res['meta'].get('ads', [])})
        res['meta']['ads'] = sorted(mm.values(), key=lambda x: -(x.get('start') or 0))
        gm = {x['id']: x for x in prev.get('google', {}).get('ads', [])}
        for s in res['google'].get('seen', []):
            if s['id'] in gm: gm[s['id']].update(s)
        gm.update({x['id']: x for x in res['google'].get('ads', [])})
        res['google']['ads'] = sorted(gm.values(), key=lambda x: -(x.get('last') or 0))
        res['google'].pop('seen', None)
    # 더 이상 쓰지 않는 썸네일 파일 정리 (예: 한국어 조회로 바뀌며 빠진 소재)
    used = {x.get('thumb') for k in ('meta', 'google') for x in res[k].get('ads', [])}
    for f in os.listdir(d):
        if f not in used: os.remove(os.path.join(d, f))
    res['google']['advertisers'] = res['google'].get('advertisers') or [{'id': g['id'], 'name': g.get('name', '')} for g in acc.get('google', [])]
    json.dump(accounts, open(ACC, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    res['sec'] = round(time.time() - t0, 1)
    os.makedirs(os.path.join(ROOT, 'data', 'ads'), exist_ok=True)
    json.dump(res, open(os.path.join(ROOT, 'data', 'ads', f'{pkg}.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    ip = os.path.join(ROOT, 'data', 'ads', 'index.json')
    idx = json.load(open(ip, encoding='utf-8')) if os.path.exists(ip) else []
    # 개발사명 (사이트 목록 검색용, 11/06): 입력값 → 순위 스냅샷(Play KR 매출·인기) → 이전 목록 값 → Google 광고주명
    try: snap_pub = next((r[6] for c in ('sales', 'popular') for r in json.load(open(os.path.join(ROOT, 'data', 'snapshot.json'), encoding='utf-8'))['charts']['gp']['kr'][c]['rows'] if r[4] == pkg), '')
    except Exception: snap_pub = ''
    publisher = clean(a.publisher) or snap_pub or next((x.get('publisher', '') for x in idx if x['pkg'] == pkg), '') or ', '.join(g['name'] for g in res['google'].get('advertisers', []) if g.get('name'))
    # 순위 추적 구분 (11/09): 이번에 받은 순위가 있으면 그것, 없으면(수동 실행) 이전 목록 값 유지 → 사이트가 인기/매출 순위 추적으로 나눔
    prev_ent = next((x for x in idx if x['pkg'] == pkg), {})
    ranks = {k: int(v) for k, v in (kv.split(':') for kv in clean(a.ranks).split(',') if ':' in kv) if v.strip().isdigit()}
    charts, rank_date = (ranks, clean(a.rank_date)) if ranks else (prev_ent.get('charts', {}), prev_ent.get('rank_date', ''))
    idx = [x for x in idx if x['pkg'] != pkg] + [{'pkg': pkg, 'name': name, 'publisher': publisher, 'charts': charts, 'rank_date': rank_date,
           'aliases': sorted({g['name'] for g in res['google'].get('advertisers', []) if g.get('name')} | {p['name'] for p in res['meta'].get('pages', []) if p.get('name')}),   # 광고주 법인명(한글 포함) · Meta 페이지명 → 검색용
           'tracked_at': res['tracked_at'],
           'meta': len(res['meta'].get('ads', [])), 'meta_total': res['meta'].get('count', 0), 'google': len(res['google'].get('ads', []))}]
    json.dump(sorted(idx, key=lambda x: x['tracked_at'], reverse=True), open(ip, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    summary = {'name': name, 'pkg': pkg, 'sec': res['sec'],
               'meta': {k: res['meta'].get(k) for k in ('pages', 'count', 'log', 'error')} | {'ads': len(res['meta'].get('ads', []))},
               'google': {'advertisers': res['google'].get('advertisers'), 'candidates': res['google'].get('candidates'), 'ads': len(res['google'].get('ads', [])), 'error': res['google'].get('error')}}
    print(json.dumps(summary, ensure_ascii=False, indent=1))

if __name__ == '__main__':   # ad_match.py 가 함수만 가져다 씀
    asyncio.run(main())
