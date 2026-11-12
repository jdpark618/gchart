"""매일 마케팅 추적 대상 (11/09): 그날 순위 스냅샷(Google Play · 한국)의 인기 1~5위 + 매출 1~5위.
TOP5 에서 빠진 게임은 더 추적하지 않음 (사이트 목록에는 마지막 추적일 기준으로 남음).
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
recent = {x['pkg'] for x in (json.load(open(ip, encoding='utf-8')) if os.path.exists(ip) else []) if time.time() - calendar.timegm(time.strptime(x['tracked_at'][:19], '%Y-%m-%dT%H:%M:%S')) < 6 * 3600}
skipped = ran = 0
for g in games.values():
    if g['pkg'] in recent:
        print(f"건너뜀 (6시간 안에 추적함): {g['name']}", flush=True)
        skipped += 1
        # 순위 정보만 갱신
        idx = json.load(open(ip, encoding='utf-8'))
        for x in idx:
            if x['pkg'] == g['pkg']:
                if x.get('rank_date') != g['date']: x['days'] = x.get('days', 0) + 1   # TOP5 에 든 날 수
                x['charts'] = g['ranks']; x['rank_date'] = g['date']
        json.dump(idx, open(ip, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        continue
    ran += 1
    rc = subprocess.run([sys.executable, os.path.join(ROOT, 'tracker', 'ad_track.py'), '--name', g['name'], '--pkg', g['pkg'], '--publisher', g['publisher'] or '',
                         '--ranks', ','.join(f'{k}:{v}' for k, v in g['ranks'].items()), '--rank-date', g['date']], check=False).returncode
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
    if gg.get('retry'): msgs.append(['r', f"{n}: Google 0건 → 30초 뒤 재시도 → {len(gg.get('ads', []))}건"])
ok = not any(t == 'x' for t, _ in msgs)
head = ['o' if ok else 'x', f'TOP5 {len(games)}개 게임 · 추적 {ran} · 순위만 갱신 {skipped} (6시간 안에 추적함)' if skipped else f'TOP5 {len(games)}개 게임 추적']
order = {'x': 0, 'r': 1, 'w': 2}
runlog.add('ads', '05:30', START, ok, [head] + sorted(msgs, key=lambda x: order.get(x[0], 3)))
