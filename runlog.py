"""수집 로그 (11/09): 대시보드 /admin 이 읽는 카테고리별 실행 기록.

파일: data/runlog/<KST 날짜>/<카테고리>.json
  {"cat": "new", "sched": "03:12", "attempts": [{"n": 1, "start": ISO, "end": ISO, "ok": true, "msgs": [["o", "..."], ...]}]}
msgs 종류: o = 정상, w = 경고, x = 실패, r = 실행 안 재시도, m = 참고
같은 날 다시 돌면 attempts 에 이어 붙음 (최초 → 재시도 → 최종).
순위 수집(Node)은 같은 모양을 run.js 안에서 직접 씀."""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
ROOT = os.path.dirname(os.path.abspath(__file__))


def now():
    return datetime.now(KST)


def add(cat, sched, start, ok, msgs, end=None):
    """start: 시작 시각(datetime, KST). msgs: [[종류, 문구], ...] (최대 12개)"""
    end = end or now()
    d = os.path.join(ROOT, 'data', 'runlog', start.strftime('%Y-%m-%d'))
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f'{cat}.json')
    try:
        rec = json.load(open(p, encoding='utf-8'))
    except Exception:
        rec = {'cat': cat, 'sched': sched, 'attempts': []}
    rec['sched'] = sched
    rec['attempts'].append({'n': len(rec['attempts']) + 1, 'start': start.isoformat(timespec='seconds'),
                            'end': end.isoformat(timespec='seconds'), 'ok': bool(ok), 'msgs': msgs[:12]})
    json.dump(rec, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'[runlog] {cat} 시도 {len(rec["attempts"])}: {"정상" if ok else "실패"} · {len(msgs)}줄 → {os.path.relpath(p, ROOT)}', flush=True)
