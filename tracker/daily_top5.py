"""매일 마케팅 추적 (11/09 → 11/19 세 단계로 나눔). 그날 순위 스냅샷(Google Play · 한국) 기준, 단계마다 다른 GitHub 서버(다른 IP)에서 실행.
  python tracker/daily_top5.py top5      1단계 TOP5 추적: 인기 TOP5 + 매출 TOP5 를 매일 모두 조회 (수집 로그 ads)
  python tracker/daily_top5.py rotation  2단계 순번 추적: 6위 아래(인기 20위 · 매출 30위까지)에서 차트마다 하루 5개 (수집 로그 ads_rot)
                                         10위 안의 처음 보는 게임이 먼저, 그다음 순위 순 · 한 번 조회한 게임은 7일 뒤에 다시
  3단계 보충 추적은 tracker/catchup.py (1 · 2단계에서 못 받은 Google 소재만)
처음 보는 게임은 ad_track.py 가 광고 계정을 자동으로 찾아 data/ads/accounts.json 에 저장 (순번: 하루 새 게임 매칭 4개 · 차트마다 2개 먼저).
TOP5 게임에는 '--ranks popular:2,sales:3' 처럼 그날 순위를 넘겨 사이트 TODAY 가 인기/매출로 나눠 보여 줌. 순번 게임은 지난 추적 목록에 쌓임.
같은 날 다시 돌면 data/ads/rotation.json 에 남긴 그날 목록을 그대로 쓰고, 6시간 안에 조회한 게임은 건너뜀."""
import json, os, subprocess, sys, time, calendar
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import runlog

STAGE = sys.argv[1] if len(sys.argv) > 1 else 'top5'
CAT = {'top5': 'ads', 'rotation': 'ads_rot'}[STAGE]
LABEL = {'top5': 'TOP5 추적', 'rotation': '순번 추적'}[STAGE]
START = runlog.now()
START_UTC = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
POOL = {'popular': 20, 'sales': 30}   # 순번 추적 범위
SLOTS, WAIT_REST = 5, 7   # 순번: 차트별 하루 조회 수 · 다시 조회까지 기다리는 날
NEW_MAX, REMATCH_MAX, PREVIEW_MAX = 4, 3, 600   # 광고 투명성 센터 차단 방지 (11/20 새 매칭 2→4): 하루 새 게임 매칭 · 재매칭 수 · 매칭 미리보기 합계
G_CAP, G_BUDGET = 300, 800   # Google 미리보기: 게임당 · 이번 단계 전체 (11/19)
msgs, games = [], {}
load = lambda p, d: json.load(open(p, encoding='utf-8')) if os.path.exists(p) else d
try:
    snap = json.load(open(os.path.join(ROOT, 'data', 'snapshot.json'), encoding='utf-8'))
    charts = snap['charts']['gp']['kr']
    for chart in ('popular', 'sales'):
        for r in charts[chart]['rows'][:5]:
            g = games.setdefault(r[4], {'name': r[3], 'pkg': r[4], 'publisher': r[6], 'ranks': {}, 'date': r[0], 'chart': chart})
            g['ranks'][chart] = r[1]
except Exception as e:
    runlog.add(CAT, '05:30', START, False, [['x', f'순위 스냅샷을 읽지 못함: {str(e)[:120]}']])
    raise
# 오늘 순위가 아직 없으면 끝냄 (11/10): 기록을 남기지 않아 예비 예약이 다시 시도함
rank_day = charts['popular'].get('date') or snap.get('today', '')
if rank_day != START.strftime('%Y-%m-%d'):
    print(f'오늘({START:%Y-%m-%d}) 순위가 아직 없음 (최신 {rank_day}) — 이번에는 추적하지 않고 끝냄', flush=True)
    sys.exit(0)
ip = os.path.join(ROOT, 'data', 'ads', 'index.json'); ap = os.path.join(ROOT, 'data', 'ads', 'accounts.json'); plan_p = os.path.join(ROOT, 'data', 'ads', 'rotation.json')
index0 = load(ip, [])
stamp = lambda s: calendar.timegm(time.strptime(s[:19], '%Y-%m-%dT%H:%M:%S'))
recent = {x['pkg'] for x in index0 if time.time() - stamp(x['tracked_at']) < 6 * 3600}   # 같은 날 다시 돌 때 중복 조회 방지


def choose_rotation():
    """순번: 6위 아래에서 차트마다 5개. 10위 안의 처음 보는 게임 → 그 밖 (새 게임 매칭 자리는 차트마다 1개 먼저, 남으면 다른 차트가 씀)"""
    today_n = calendar.timegm(time.strptime(rank_day, '%Y-%m-%d')) // 86400
    last = {x['pkg']: (stamp(x['tracked_at']) + 9 * 3600) // 86400 for x in index0}   # 마지막 조회일 (한국 시간)
    known, picks, cnt, new = load(ap, {}), {}, dict.fromkeys(POOL, 0), dict.fromkeys(POOL, 0)
    for share, only10 in ((NEW_MAX // len(POOL), True), (NEW_MAX, True), (NEW_MAX // len(POOL), False), (NEW_MAX, False)):
        for chart, depth in POOL.items():
            for r in charts[chart]['rows'][:depth]:
                pkg = r[4]
                if r[1] <= 5 or pkg in games or cnt[chart] >= SLOTS or pkg in picks: continue
                if only10 and (r[1] > 10 or pkg in known): continue
                if pkg in last and today_n - last[pkg] < WAIT_REST: continue
                if pkg not in known:
                    if sum(new.values()) >= NEW_MAX or new[chart] >= share: continue
                    new[chart] += 1
                cnt[chart] += 1
                picks[pkg] = {'name': r[3], 'pkg': pkg, 'publisher': r[6], 'ranks': {}, 'date': r[0], 'chart': chart}
    return picks


# 그날 목록: 단계별로 한 번 정하고 같은 날 다시 돌면 그대로 (예전 형식 {day, picks} 는 버림)
plan = load(plan_p, {})
plan = {k: v for k, v in plan.items() if k in ('top5', 'rotation')} if plan.get('day') == rank_day else {}
plan['day'] = rank_day
if STAGE not in plan: plan[STAGE] = dict(games) if STAGE == 'top5' else choose_rotation()
picks = plan[STAGE]
print(f"{LABEL} {len(picks)}개:", ', '.join(f"{g['name']}({g['chart']})" for g in picks.values()), flush=True)
if os.environ.get('DRY_RUN'): sys.exit(0)
json.dump(plan, open(plan_p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def rank_only(g):
    """소재는 조회하지 않고 순위 정보만 갱신 → 사이트 TODAY 는 매일 그날 TOP5"""
    idx = load(ip, [])
    for x in idx:
        if x['pkg'] == g['pkg']:
            if x.get('rank_date') != g['date']: x['days'] = x.get('days', 0) + 1   # TOP5 에 든 날 수
            x['charts'] = g['ranks']; x['rank_date'] = g['date']
    json.dump(idx, open(ip, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


skipped = ran = rematched = previews = used = last_open = 0
for g in picks.values():
    before = load(ap, {}).get(g['pkg'])
    if g['pkg'] in recent:
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
    if ran > 1: time.sleep(10 if last_open < 100 else 30)   # 게임 사이 쉬기: 미리보기를 많이 연 게임 뒤에는 더 길게
    rc = subprocess.run(cmd, check=False).returncode
    after = load(ap, {}).get(g['pkg']) or {}
    if before and after.get('rematched_at') != before.get('rematched_at'): rematched += 1
    if after.get('google_log') != (before or {}).get('google_log'): previews += (after.get('google_log') or {}).get('previews') or 0
    # 게임별 결과 → 수집 로그 (문제 있는 것만 줄로 남김)
    n = g['name']
    res = load(os.path.join(ROOT, 'data', 'ads', f"{g['pkg']}.json"), {})
    if rc != 0 or str(res.get('tracked_at', ''))[:19] < START_UTC:
        msgs.append(['x', f'{n}: 추적 실패 (종료 코드 {rc})']); last_open = 0; continue
    m, gg = res.get('meta', {}), res.get('google', {})
    if m.get('error'): msgs.append(['x', f"{n}: Meta 오류 {m['error'][:80]}"])
    elif any(l.get('skip') for l in m.get('log', [])): msgs.append(['w', f'{n}: Meta 계정 없음'])
    rt = next((l for l in m.get('log', []) if l.get('retry')), None)
    if rt and rt.get('got', 1): msgs.append(['r', f"{n}: Meta 0건 → 30초 뒤 재시도 → {rt.get('got', len(m.get('ads', [])))}건"])
    elif rt: msgs.append(['w', f"{n}: Meta 재시도도 0건 → 저장된 소재 유지"])   # 11/25: 전엔 저장된 수를 재시도 결과처럼 보여 줌
    # 다른 게임 소재 안전장치 (11/25): 뺀 소재 · 게임 사이 중복 · 페이지 공유 · 계정 새 매칭
    if m.get('dup'): msgs.append(['w', f"{n}: Meta 다른 게임과 겹친 소재 {m['dup']}건 제외"])
    if m.get('dropped', 0) - m.get('dup', 0) > 0: msgs.append(['m', f"{n}: Meta 다른 게임 소재 {m['dropped'] - m.get('dup', 0)}건 제외"])
    if m.get('shared_with'): msgs.append(['w', f"{n}: Meta 페이지가 다른 게임과 같음 ({len(m['shared_with'])}개) → 확인 필요"])
    sig = lambda x: (sorted((x or {}).get('meta_pages') or []), sorted(g.get('id', '') for g in (x or {}).get('google') or []))
    if after and sig(after) != sig(before):   # 처음 매칭 · 7일 재매칭에서 계정이 실제로 바뀐 때만
        nm = [p['name'] for p in m.get('pages', []) if p.get('name')] + [x.get('name', '') for x in gg.get('advertisers', []) if x.get('name')]
        msgs.append(['w' if before else 'm', f"{n}: 광고 계정 {'바뀜' if before else '첫 매칭'} → 확인 ({', '.join(nm[:3]) or '없음'})"])
    if gg.get('error'): msgs.append(['x', f"{n}: Google 오류 {gg['error'][:80]}"])
    elif not gg.get('advertisers'): msgs.append(['w', f'{n}: Google 계정 없음'])
    cs = gg.get('candidates') or []
    left, first = sum(c.get('failed') or 0 for c in cs), sum(c.get('failedFirst') or 0 for c in cs)
    last_open = sum(c.get('opened') or 0 for c in cs); used += last_open
    deferred = sum(c.get('deferred') or 0 for c in cs)
    if any(c.get('blocked') for c in cs): msgs.append(['w', f"{n}: Google 요청 제한 감지 → 미리보기 중단 · 보충 추적에서 다른 서버로 이어서"])
    elif deferred: msgs.append(['m', f'{n}: Google 미리보기 한도로 {deferred}건은 보충 추적에서 이어서'])
    if left:
        codes = {}
        for c in cs:
            for k, v in (c.get('codes') or {}).items(): codes[k] = codes.get(k, 0) + v
        msgs.append(['w', f"{n}: Google 미리보기 {left}건 실패 (응답 " + ', '.join(f'{k}×{v}' for k, v in codes.items()) + ') → 보충 추적에서 이어서'])
    elif first: msgs.append(['r', f'{n}: Google 미리보기 {first}건 실패 → 재시도로 복구'])
    if gg.get('retry'): msgs.append(['r', f"{n}: Google 0건 → 30초 뒤 재시도 → {len(gg.get('ads', []))}건"])
ok = not any(t == 'x' for t, _ in msgs)
head = ['o' if ok else 'x', f'{LABEL} {ran}개' + (f' · 6시간 안에 조회해 건너뜀 {skipped}' if skipped else '') + f" · Google 미리보기 {used}건 · 서버 IP {os.environ.get('RUNNER_IP', '알 수 없음')}"]
order = {'x': 0, 'r': 1, 'w': 2, 'm': 3}
runlog.add(CAT, '05:30' if STAGE == 'top5' else '이어서', START, ok, [head] + sorted(msgs, key=lambda x: order.get(x[0], 3)))
