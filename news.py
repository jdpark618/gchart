"""게임 뉴스 수집 (11/20): 루리웹 플랫폼별 뉴스 섹션 → data/news/YYYY-MM.json (대시보드 '게임 뉴스' 메뉴가 읽음)
분류는 루리웹 편집부가 나눈 섹션을 그대로 씀 (모바일 531 · PC/온라인 529 · 비디오게임 523). 키워드로 분류하지 않음.
- 섹션마다 목록 앞 PAGES 쪽만 읽음 (한 쪽 15건). 같은 기사가 여러 섹션에 있으면 기사 번호로 하나로 묶고 플랫폼을 더함
- 목록 제목이 '...' 로 잘린 새 기사만 기사 쪽을 한 번 열어 전체 제목(og:title)을 받음
- 본문은 저장하지 않음: 번호 · 제목 · 목록 요약 · 시각 · 플랫폼 · 썸네일
- 바뀐 달 파일만 다시 씀 → 워크플로는 바뀐 경우에만 커밋"""
import html, json, os, re, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

SECTIONS = [('m', 531), ('pc', 529), ('cs', 523)]   # 모바일 · PC/온라인 · 비디오게임(콘솔)
PAGES = int(os.environ.get('NEWS_PAGES', '2'))
BASE = 'https://bbs.ruliweb.com'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36'
ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(ROOT, 'data', 'news')
KST = timezone(timedelta(hours=9))


def get(url):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'ko'}), timeout=20) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:
            err = e; time.sleep(3 * (i + 1))
    raise err


def txt(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', s))).strip()


def parse_list(h, sec):
    """center_list 의 기사들: 번호 · 썸네일 · 제목(잘릴 수 있음) · 요약 · 시각"""
    i = h.find('class="center_list"')
    if i < 0:
        return []
    h = h[i:h.find('paging_wrapper', i)]
    out = []
    for blk in re.split(r'<div id="news_', h)[1:]:
        m = re.match(r'(\d+)"', blk)
        t = re.search(r'<strong class="title">(.*?)</strong>', blk, re.S)
        tm = re.search(r'create_time">\s*(\d{4})\.(\d\d)\.(\d\d) \((\d\d:\d\d)', blk)
        if not (m and t and tm):
            continue
        th = re.search(r"url\((//img\.ruliweb\.com/[^)]+)\)", blk)
        d = re.search(r'<span class="desc">(.*?)</span>', blk, re.S)
        out.append({'id': int(m.group(1)), 't': txt(t.group(1)), 'd': txt(d.group(1)) if d else '',
                    'ts': f'{tm.group(1)}-{tm.group(2)}-{tm.group(3)} {tm.group(4)}', 'p': [sec],
                    'th': ('https:' + th.group(1)) if th else ''})
    return out


def full_title(aid):
    h = get(f'{BASE}/news/read/{aid}')
    m = re.search(r'<meta property="og:title" content="([^"]*)"', h)
    return html.unescape(m.group(1)).strip() if m else ''


def main():
    os.makedirs(DIR, exist_ok=True)
    got, fails, secn, added = {}, [], {}, {'m': 0, 'pc': 0, 'cs': 0}
    for sec, no in SECTIONS:
        n = 0
        for pg in range(1, PAGES + 1):
            try:
                items = parse_list(get(f'{BASE}/news/{no}' + (f'?page={pg}' if pg > 1 else '')), sec)
            except Exception as e:
                fails.append(f'{sec}:{pg} {e}'); break
            for it in items:
                if it['id'] in got:
                    got[it['id']]['p'] = sorted(set(got[it['id']]['p'] + [sec]), key=['m', 'pc', 'cs'].index)
                else:
                    got[it['id']] = it
            n += len(items); time.sleep(1.5)
        print(f'{sec}: {n}건'); secn[sec] = n
        if n == 0:
            fails.append(f'{sec}: 0건 (화면 구조 바뀜?)')
    # 달 파일에 합치기
    months = {}
    for it in got.values():
        months.setdefault(it['ts'][:7], []).append(it)
    fetched = 0
    for ym, items in months.items():
        path = os.path.join(DIR, f'{ym}.json')
        old = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {'items': []}
        cur = {x['id']: x for x in old['items']}
        before = json.dumps(old, ensure_ascii=False, sort_keys=True)
        for it in items:
            o = cur.get(it['id'])
            if not o:
                for p in it['p']: added[p] += 1
            if o:   # 이미 있는 기사: 플랫폼만 더함 (전체 제목은 유지) · 지난번에 한도로 못 채운 잘린 제목은 이번에 채움
                o['p'] = sorted(set(o['p'] + it['p']), key=['m', 'pc', 'cs'].index)
                it = o
                if not (it['t'].endswith('...') and fetched < 60):
                    continue
            if it['t'].endswith('...') and fetched < 60:
                try:
                    ft = full_title(it['id']); fetched += 1; time.sleep(1)
                    if ft: it['t'] = ft
                except Exception as e:
                    print('제목 실패', it['id'], e)
            cur[it['id']] = it
        new = {'items': sorted(cur.values(), key=lambda x: (x['ts'], x['id']), reverse=True)}
        if json.dumps(new, ensure_ascii=False, sort_keys=True) != before:
            new['updated'] = datetime.now(KST).strftime('%Y-%m-%dT%H:%M')
            json.dump(new, open(path, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
            print(f'{ym}: {len(new["items"])}건 저장')
    # 달 목록 (사이트가 어떤 달 파일이 있는지 앎)
    idx = sorted(f[:7] for f in os.listdir(DIR) if re.match(r'\d{4}-\d\d\.json$', f))
    ip = os.path.join(DIR, 'index.json')
    if not os.path.exists(ip) or json.load(open(ip, encoding='utf-8')).get('months') != idx:
        json.dump({'months': idx}, open(ip, 'w', encoding='utf-8'))
    print(f'제목 보충 {fetched}건')
    if fails:
        print('실패:', '; '.join(fails))
    return fails, secn, added, fetched


def run():
    """수집 로그 (11/25): /admin 뉴스 줄. 3시간마다 도는 실행이 하루 파일의 attempts 에 차례로 쌓임"""
    sys.path.insert(0, ROOT); import runlog
    start = runlog.now()
    try:
        fails, secn, added, fetched = main()
    except Exception as e:
        runlog.add('news', '3시간마다', start, False, [['x', f'수집 중단: {str(e)[:120]}']]); raise
    nm = {'m': '모바일', 'pc': 'PC', 'cs': '콘솔'}
    new = sum(added.values())
    msgs = [['o' if not fails else 'x', f'새 기사 {new}건' + (' (' + ' · '.join(f'{nm[k]} {v}' for k, v in added.items() if v) + ')' if new else '')]]
    msgs += [['x', f] for f in fails]
    msgs.append(['m', '목록 ' + ' · '.join(f'{nm[k]} {secn.get(k, 0)}' for k in nm) + (f' · 제목 보충 {fetched}' if fetched else '')])
    runlog.add('news', '3시간마다', start, not fails, msgs)
    if fails: sys.exit(1)


if __name__ == '__main__':
    run()
