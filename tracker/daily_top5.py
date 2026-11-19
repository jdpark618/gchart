"""매일 마케팅 추적 대상 (11/09, 순번 교체 11/18): 그날 순위 스냅샷(Google Play · 한국)에서 차트마다 하루 5개까지.
위에서부터 내려가며 '다시 조회할 때가 된' 게임을 고름 — 현재 TOP5 는 조회 후 3일, 그 아래는 7일이 지나야 다시 조회 · 인기는 20위, 매출은 30위까지만.
사이트 TODAY 는 그대로 그날 TOP5 (조회를 쉬는 날은 순위만 갱신) · 그 아래에서 조회한 게임은 지난 추적 목록에 쌓임.
처음 보는 게임은 ad_track.py 가 광고 계정을 자동으로 찾아 data/ads/accounts.json 에 저장.
각 게임에 '--ranks popular:2,sales:3' 처럼 그날 순위를 넘겨 사이트가 인기/매출 순위 추적으로 나눠 보여줌.
끝에 수집 로그(data/runlog/<날짜>/ads.json)를 남김 → 대시보드 /admin."""
import json, os, subprocess, sys, time, calendar
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import runlog

START = runlog.now()
START_UTC = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
msgs, games = [], {}
POOL = {'popular': 20, 'sales': 30}   # 차트별 조회 범위
SLOTS, WAIT_TOP, WAIT_REST = 5, 3, 7   # 차트별 하루 조회 수 · 다시 조회까지 기다리는 날 (TOP5 / 그 아래)
NEW_MAX, REMATCH_MAX, PREVIEW_MAX = 2, 3, 600
G_CAP, G_BUDGET = 300, 800   # Google 미리보기 (11/19): 게임당 · 한 번 실행에 · 매칭은 PREVIEW_MAX 따로   # 광고 투명성 센터 차단 방지: 하루 새 게임 매칭 · 재매칭 수 · 매칭 미리보기 합계 (한 번에 약 2000건부터 막힘)
try:
    snap = json.load(open(os.path.join(ROOT, 'data', 'snapshot.json'), encoding='utf-8'))
    charts = snap['charts']['gp']['kr']
    for chart in ('popular', 'sales'):
        for r in charts[chart]['rows'][:5]:
            g = games.setdefault(r[4], {'name': r[3], 'pkg': r[4], 'publisher': r[6], 'ranks': {}, 'date': r[0]})
            g['ranks'][chart] = r[1]
except Exception as e:
    runlog.add('ads', '05:30', START, False, [['x', f'순위 스냅샷을 읽지 못함: {str(e)[:120]}']])
    raise
# 오늘 순위가 아직 없으면 끝냄 (11/10): 예약이 엇갈려 순위보다 먼저 돌면 어제 TOP5 를 추적하게 되므로. 기록은 남기지 않아 예비 예약이 다시 시도함
rank_day = charts['popular'].get('date') or snap.get('today', '')
if rank_day != START.strftime('%Y-%m-%d'):
    print(f'오늘({START:%Y-%m-%d}) 순위가 아직 없음 (최신 {rank_day}) — 순위 수집 뒤에 다시 실행해야 함. 이번에는 추적하지 않고 끝냄', flush=True)
    sys.exit(0)
print(f"{snap.get('today', '')} 추적 대상 {len(games)}개:", ', '.join(f"{g['name']}({'/'.join(f'{k} {v}' for k, v in g['ranks'].items())})" for g in games.values()), flush=True)
# 최근 6시간 안에 이미 추적한 게임은 건너뜀 (같은 날 수동으로 다시 돌릴 때 중복 조회로 차단당하지 않게)
ip = os.path.join(ROOT, 'data', 'ads', 'index.json')
ap = os.path.join(ROOT, 'data', 'ads', 'accounts.json')
plan_p = os.path.join(ROOT, 'data', 'ads', 'rotation.json')
load = lambda p, d: json.load(open(p, encoding='utf-8')) if os.path.exists(p) else d
index0 = load(ip, [])
stamp = lambda s: calendar.timegm(time.strptime(s[:19], '%Y-%m-%dT%H:%M:%S'))
recent = {x['pkg'] for x in index0 if time.time() - stamp(x['tracked_at']) < 6 * 3600}

def choose():
    """오늘 조회할 게임: TOP5 먼저, 남은 자리는 6위 아래에서.
    새 게임 매칭 자리: TOP5 가 먼저 쓰고, 남은 자리는 차트마다 1개씩 → 한쪽에 새 게임이 없으면 다른 차트가 씀 (인기가 먼저 돌아 매출이 계속 밀리지 않게)"""
    today_n = calendar.timegm(time.strptime(rank_day, '%Y-%m-%d')) // 86400
    last = {x['pkg']: (stamp(x['tracked_at']) + 9 * 3600) // 86400 for x in index0}   # 마지막 조회일 (한국 시간)
    known, picks, cnt, new = load(ap, {}), {}, dict.fromkeys(POOL, 0), dict.fromkeys(POOL, 0)
    def wait_days(pkg):   # Google 미리보기 실패가 남은 게임은 다음 날 다시 조회 (11/18)
        res = load(os.path.join(ROOT, 'data', 'ads', f'{pkg}.json'), {})
        return 1 if any(c.get('failed') or c.get('deferred') for c in res.get('google', {}).get('candidates') or []) else WAIT_TOP if pkg in games else WAIT_REST
    for top, share in ((True, NEW_MAX), (False, NEW_MAX // len(POOL)), (False, NEW_MAX)):   # TOP5 → 차트별 몫 → 남은 자리
        for chart, depth in POOL.items():
            for r in charts[chart]['rows'][:depth]:
                pkg = r[4]
                if (r[1] <= 5) != top or cnt[chart] >= SLOTS or pkg in picks: continue
                if pkg in last and today_n - last[pkg] < wait_days(pkg): continue
                if pkg not in known:
                    if sum(new.values()) >= NEW_MAX or new[chart] >= share: continue
                    new[chart] += 1
                cnt[chart] += 1
                picks[pkg] = {'name': r[3], 'pkg': pkg, 'publisher': r[6], 'ranks': games.get(pkg, {}).get('ranks', {}), 'date': r[0], 'chart': chart}
    return picks

# 같은 날 다시 돌아도(수동 · 재시도) 다음 순번으로 넘어가지 않게 그날 고른 목록을 남겨 그대로 씀
plan = load(plan_p, {})
picks = plan['picks'] if plan.get('day') == rank_day else choose()
# 오후 재시도 (11/19): 오늘 Google 요청 제한으로 멈춘 게임만 다른 서버(예비 실행)에서 한 번 더. gate.py 가 이 파일을 보고 예비 실행을 진행시킴
retry_p = os.path.join(ROOT, 'data', 'ads', 'retry.json'); retry = load(retry_p, {})
RETRY = retry.get('day') == rank_day and not retry.get('done') and retry.get('pkgs')
if RETRY:
    picks = {p: g for p, g in picks.items() if p in retry['pkgs']}
    print('오후 재시도: Google 요청 제한으로 멈춘 게임', ', '.join(g['name'] for g in picks.values()), flush=True)
print(f"오늘 조회 {len(picks)}개:", ', '.join(f"{g['name']}({g['chart']})" for g in picks.values()), flush=True)
if os.environ.get('DRY_RUN'): sys.exit(0)
json.dump({'day': rank_day, 'picks': picks}, open(plan_p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

def rank_only(g):
    """소재는 조회하지 않고 순위 정보만 갱신 → 사이트 TODAY 는 매일 그날 TOP5"""
    idx = load(ip, [])
    for x in idx:
        if x['pkg'] == g['pkg']:
            if x.get('rank_date') != g['date']: x['days'] = x.get('days', 0) + 1   # TOP5 에 든 날 수
            x['charts'] = g['ranks']; x['rank_date'] = g['date']
    json.dump(idx, open(ip, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

for g in games.values():
    if g['pkg'] not in picks: rank_only(g)
skipped = ran = rematched = previews = used = 0; blocked_pk = []
last_open = 0
for g in picks.values():
    before = load(ap, {}).get(g['pkg'])
    if g['pkg'] in recent and not RETRY:
        print(f"건너뜀 (6시간 안에 추적함): {g['name']}", flush=True)
        skipped += 1
        if g['ranks']: rank_only(g)
        continue
    if not before and previews >= PREVIEW_MAX:
        msgs.append(['w', f"{g['name']}: 매칭 조회량이 많아 다음 날로 미룸"]); continue
    ran += 1
    cmd = [sys.executable, os.path.join(ROOT, 'tracker', 'ad_track.py'), '--name', g['name'], '--pkg', g['pkg'], '--publisher', g['publisher'] or '', '--pool', g['chart']]
    if g['ranks']: cmd += ['--ranks', ','.join(f'{k}:{v}' for k, v in g['ranks'].items()), '--rank-date', g['date']]
    if rematched >= REMATCH_MAX or previews >= PREVIEW_MAX: cmd.append('--no-rematch')
    cmd += ['--g-cap', str(G_CAP), '--g-budget', str(max(0, G_BUDGET - used))]
    if ran > 1: time.sleep(10 if last_open < 100 else 30)   # 게임 사이 쉬기 (11/19): 미리보기를 많이 연 게임 뒤에는 더 길게
    rc = subprocess.run(cmd, check=False).returncode
    after = load(ap, {}).get(g['pkg']) or {}
    if before and after.get('rematched_at') != before.get('rematched_at'): rematched += 1
    if after.get('google_log') != (before or {}).get('google_log'): previews += (after.get('google_log') or {}).get('previews') or 0
    # 게임별 결과 → 수집 로그 (문제 있는 것만 줄로 남김)
    n = g['name']
    try: res = json.load(open(os.path.join(ROOT, 'data', 'ads', f"{g['pkg']}.json"), encoding='utf-8'))
    except Exception: res = {}
    if rc != 0 or str(res.get('tracked_at', ''))[:19] < START_UTC:
        msgs.append(['x', f'{n}: 추적 실패 (종료 코드 {rc})']); continue
    m, gg = res.get('meta', {}), res.get('google', {})
    if m.get('error'): msgs.append(['x', f"{n}: Meta 오류 {m['error'][:80]}"])
    elif any(l.get('skip') for l in m.get('log', [])): msgs.append(['w', f'{n}: Meta 계정 없음'])
    if any(l.get('retry') for l in m.get('log', [])): msgs.append(['r', f"{n}: Meta 0건 → 30초 뒤 재시도 → {len(m.get('ads', []))}건"])
    if gg.get('error'): msgs.append(['x', f"{n}: Google 오류 {gg['error'][:80]}"])
    elif not gg.get('advertisers'): msgs.append(['w', f'{n}: Google 계정 없음'])
    cs = gg.get('candidates') or []
    left, first = sum(c.get('failed') or 0 for c in cs), sum(c.get('failedFirst') or 0 for c in cs)
    last_open = sum(c.get('opened') or 0 for c in cs); used += last_open
    deferred = sum(c.get('deferred') or 0 for c in cs)
    if any(c.get('blocked') for c in cs):
        blocked_pk.append(g['pkg']); msgs.append(['w', f"{n}: Google 요청 제한 감지 → 미리보기 중단" + (' (오후 재시도도 막힘 · 다음 수집에서)' if RETRY else ' · 12:30 예비 실행에서 다른 서버로 재시도')])
    elif deferred: msgs.append(['m', f'{n}: Google 미리보기 한도로 {deferred}건은 다음 수집에서 확인'])
    if left:
        codes = {}
        for c in cs:
            for k, v in (c.get('codes') or {}).items(): codes[k] = codes.get(k, 0) + v
        msgs.append(['w', f"{n}: Google 미리보기 {left}건 실패 (응답 " + ', '.join(f'{k}×{v}' for k, v in codes.items()) + ') → 다음 날 다시 조회'])
    elif first: msgs.append(['r', f'{n}: Google 미리보기 {first}건 실패 → 재시도로 복구'])
    if gg.get('retry'): msgs.append(['r', f"{n}: Google 0건 → 30초 뒤 재시도 → {len(gg.get('ads', []))}건"])
ok = not any(t == 'x' for t, _ in msgs)
json.dump({'day': rank_day, 'pkgs': blocked_pk if not RETRY else retry['pkgs'], 'done': bool(RETRY) or not blocked_pk}, open(retry_p, 'w', encoding='utf-8'), ensure_ascii=False)
top_n = sum(1 for g in picks.values() if g['ranks'])
head = ['o' if ok else 'x', (f'오후 재시도 {ran}개 (Google 요청 제한으로 멈췄던 게임)' if RETRY else f'조회 {ran}개 (TOP5 {top_n} · 순번 {len(picks) - top_n}) · 오늘 TOP5 {len(games)}개' + (f' · 6시간 안에 조회해 건너뜀 {skipped}' if skipped else '')) + f' · Google 미리보기 {used}건']
order = {'x': 0, 'r': 1, 'w': 2, 'm': 3}
runlog.add('ads', '05:30', START, ok, [head] + sorted(msgs, key=lambda x: order.get(x[0], 3)))
