"""3단계 보충 추적 (11/19): TOP5 · 순번 추적(daily_top5.py)이 끝난 뒤 다른 GitHub 서버(다른 IP)에서 Google 소재만 이어서 받는다.
대상 = 저장된 결과에 아직 열지 못한 Google 미리보기가 남은 게임 (한도로 넘긴 것 deferred · 실패 failed · 요청 제한으로 멈춘 blocked).
순서: 오늘 TOP5 · 순번에서 조회한 게임 → 그 밖의 게임 (매출 → 인기 순위 순). Meta 와 순위 정보는 건드리지 않음 (ad_track.py --google-only).
한도: 미리보기 게임당 300건 · 이번 실행 600건. 게임 사이 쉬기는 앞 단계와 같고, 요청 제한에 막히면 그날 보충은 거기서 멈춤 (남은 것은 다음 날 보충에서).
수집 로그: data/runlog/<날짜>/ads_fill.json (서버 IP 포함 → 앞 단계와 다른 IP 였는지 확인)."""
import json, os, subprocess, sys, time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import runlog

G_CAP, G_BUDGET = 300, 600
START = runlog.now(); START_UTC = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
load = lambda p, d: json.load(open(p, encoding='utf-8')) if os.path.exists(p) else d
ADS = os.path.join(ROOT, 'data', 'ads')
index = load(os.path.join(ADS, 'index.json'), []); accounts = load(os.path.join(ADS, 'accounts.json'), {})
plan = load(os.path.join(ADS, 'rotation.json'), {}); today = [p for k in ('top5', 'rotation') for p in plan.get(k, {})] if plan.get('day') == START.strftime('%Y-%m-%d') else []
snap = load(os.path.join(ROOT, 'data', 'snapshot.json'), {}).get('charts', {}).get('gp', {}).get('kr', {})
rank = lambda c, p: next((r[1] for r in snap.get(c, {}).get('rows', []) if r[4] == p), 999)


def left_of(p):
    """저장된 결과에서 아직 못 연 Google 미리보기 수"""
    R = load(os.path.join(ADS, f'{p}.json'), {})
    return sum((c.get('deferred') or 0) + (c.get('failed') or 0) for c in R.get('google', {}).get('candidates') or [])


# Google 조회 자체가 오류(차단 등)로 끝난 게임 (11/26): 남은 미리보기 수를 모르므로 따로 표시해 대상에 넣음
pending = lambda p: bool(load(os.path.join(ADS, f'{p}.json'), {}).get('google', {}).get('pending'))
todo = [x for x in index if accounts.get(x['pkg'], {}).get('google') and (left_of(x['pkg']) or pending(x['pkg']))]
todo.sort(key=lambda x: (x['pkg'] not in today, today.index(x['pkg']) if x['pkg'] in today else 0, min(rank('sales', x['pkg']), rank('popular', x['pkg']) + 1000)))
ip = os.environ.get('RUNNER_IP', '알 수 없음')
print(f'보충 추적 대상 {len(todo)}개 (서버 IP {ip}):', ', '.join(f"{x['name']}({'조회 실패' if pending(x['pkg']) else left_of(x['pkg'])})" for x in todo), flush=True)
msgs, used, ran, last_open, stopped = [], 0, 0, 0, False
for x in todo:
    if used >= G_BUDGET:
        msgs.append(['m', f"예산 {G_BUDGET}건을 다 써서 {len(todo) - ran}개 게임은 다음 보충에서"]); break
    if ran: time.sleep(10 if last_open < 100 else 30)
    ran += 1; before = left_of(x['pkg']); was_pending = pending(x['pkg'])
    rc = subprocess.run([sys.executable, os.path.join(ROOT, 'tracker', 'ad_track.py'), '--name', x['name'], '--pkg', x['pkg'], '--google-only',
                         '--g-cap', str(G_CAP), '--g-budget', str(G_BUDGET - used)], check=False).returncode
    R = load(os.path.join(ADS, f"{x['pkg']}.json"), {}); cs = R.get('google', {}).get('candidates') or []
    if rc != 0 or str(R.get('google_at', ''))[:19] < START_UTC:
        msgs.append(['x', f"{x['name']}: 보충 추적 실패 (종료 코드 {rc})"]); last_open = 0; continue
    if 'Google 차단' in str(R.get('google', {}).get('error', '')):   # 이 서버도 막힘 → 더 두드리지 않고 다음 보충에서
        msgs.append(['w', f"{x['name']}: Google 차단 · 보충 중단 (다음 보충에서)"]); stopped = True; break
    if R.get('google', {}).get('error'):
        msgs.append(['x', f"{x['name']}: Google 오류 {R['google']['error'][:80]}"]); last_open = 0; continue
    last_open = sum(c.get('opened') or 0 for c in cs); used += last_open; after = left_of(x['pkg'])
    new = sum(c.get('matched') or 0 for c in cs)
    line = f"{x['name']}: " + ('앞 단계 조회 실패분 · ' if was_pending else '') + f"미리보기 {last_open}건 · 새 소재 {new}개 · 남은 {before} → {after}건"
    if any(c.get('blocked') for c in cs):
        msgs.append(['w', line + ' · 요청 제한으로 보충 중단 (다음 보충에서)']); stopped = True; break
    msgs.append(['o' if not after else 'm', line])
ok = not any(t == 'x' for t, _ in msgs)
head = ['o' if ok else 'x', f'보충 추적 {ran}개 · Google 미리보기 {used}건 · 서버 IP {ip}' if todo else f'보충 추적 대상 없음 · 서버 IP {ip}']
runlog.add('ads_fill', '이어서', START, ok, [head] + msgs)
