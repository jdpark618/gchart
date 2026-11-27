"""예약 실행 관문 (11/10): 하루 2번(원래 + 예비 7시간 뒤) 예약을 걸고, 오늘 이미 성공한 수집이면 뒤에 온 예약은 바로 끝낸다.
사용: python gate.py <카테고리...> [--after HH:MM]   예) python gate.py popular sales · 마케팅 추적은 ads ads_rot ads_fill (세 단계 모두 성공해야 건너뜀)
기준: data/runlog/<KST 오늘>/<카테고리>.json 의 마지막 시도가 성공(ok) — 주어진 카테고리가 모두 성공이면 skip=true.
--after HH:MM (11/27): KST 그 시각 전이면 건너뜀. GitHub 안전망 예약이 몇 시간 밀려 자정을 넘기면 다음 날 새벽에 돌아
                       그날 몫을 정기 실행(Cloud)보다 먼저 가져가 버림 (11/27 신작 01:48) → 워크플로가 schedule 일 때만 붙임
마케팅 (11/27): TOP5 · 순번이 Google 오류로만 실패했고 그 뒤 보충(ads_fill)이 성공했으면 그 단계도 끝난 것으로 봄
                (11/26: 15:46 보충으로 다 채웠는데 19:01 순위 완료 연결로 전체를 다시 돌아 Google 미리보기 244건을 더 엶)
수동 실행(workflow_dispatch) 은 워크플로 쪽에서 이 관문을 거치지 않음 (항상 실행)."""
import json, os, sys
from datetime import datetime, timedelta, timezone

now = datetime.now(timezone(timedelta(hours=9)))
day = now.strftime('%Y-%m-%d')
root = os.path.dirname(os.path.abspath(__file__))


def last(cat):
    try:
        return json.load(open(os.path.join(root, 'data', 'runlog', day, f'{cat}.json'), encoding='utf-8'))['attempts'][-1]
    except Exception:
        return None


def done(cat, cats):
    a = last(cat)
    if not a: return False
    if a.get('ok'): return True
    # Google 오류만 남은 TOP5 · 순번 → 뒤에 성공한 보충이 메웠으면 끝난 것
    if cat in ('ads', 'ads_rot') and 'ads_fill' in cats:
        f = last('ads_fill')
        xs = [m[1] for m in a.get('msgs', []) if m[0] == 'x']
        return bool(f and f.get('ok') and f.get('start', '') > a.get('start', '') and xs and all('Google' in x for x in xs))
    return False


args = [a for a in sys.argv[1:] if a]
after = args[args.index('--after') + 1] if '--after' in args else ''
cats = [c for i, c in enumerate(args) if not c.startswith('--') and (i == 0 or args[i - 1] != '--after')]
early = bool(after) and now.strftime('%H:%M') < after
state = {c: done(c, cats) for c in cats}
skip = early or (bool(cats) and all(state.values()))
why = f'{now:%H:%M} — {after} 전이라 건너뜀 (밀린 안전망 예약)' if early else ('완료 → 이번 예약은 건너뜀' if skip else '전 또는 실패 → 수집 진행')
print(f'{day} 오늘 수집 {why} ({", ".join(f"{c}:{v}" for c, v in state.items())})')
out = os.environ.get('GITHUB_OUTPUT')
if out:
    with open(out, 'a') as f:
        f.write(f'skip={"true" if skip else "false"}\n')
