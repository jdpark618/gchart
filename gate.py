"""예약 실행 관문 (11/10): 하루 2번(원래 + 예비 7시간 뒤) 예약을 걸고, 오늘 이미 성공한 수집이면 뒤에 온 예약은 바로 끝낸다.
사용: python gate.py <카테고리...>   예) python gate.py popular sales
기준: data/runlog/<KST 오늘>/<카테고리>.json 의 마지막 시도가 성공(ok) — 주어진 카테고리가 모두 성공이면 skip=true.
수동 실행(workflow_dispatch) 은 워크플로 쪽에서 이 관문을 거치지 않음 (항상 실행)."""
import json, os, sys
from datetime import datetime, timedelta, timezone

day = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d')
root = os.path.dirname(os.path.abspath(__file__))


def done(cat):
    try:
        return bool(json.load(open(os.path.join(root, 'data', 'runlog', day, f'{cat}.json'), encoding='utf-8'))['attempts'][-1]['ok'])
    except Exception:
        return False


cats = sys.argv[1:]
skip = bool(cats) and all(done(c) for c in cats)
print(f'{day} 오늘 수집 {"완료 → 이번 예약은 건너뜀" if skip else "전 또는 실패 → 수집 진행"} ({", ".join(f"{c}:{done(c)}" for c in cats)})')
out = os.environ.get('GITHUB_OUTPUT')
if out:
    with open(out, 'a') as f:
        f.write(f'skip={"true" if skip else "false"}\n')
