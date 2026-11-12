"""바뀐 시각 표시 (11/12): 신작 일정 · 행사 일정이 이전 수집과 달라지면 data/changed/<키>.json 에 그 시각(KST)을 남긴다.
사이트(대시보드)가 이 파일을 읽어 메뉴에 UPDATE 띠를 붙임 (바뀐 지 24시간 안 · 열어 보면 꺼짐).
사용: import changed; changed.mark('new')   # 키: new(신작 달력) · events(행사 달력) — 파일을 키마다 따로 둬 워크플로끼리 충돌하지 않게"""
import json, os
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))


def mark(key):
    d = os.path.join(ROOT, 'data', 'changed')
    os.makedirs(d, exist_ok=True)
    at = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='minutes')
    json.dump({'at': at}, open(os.path.join(d, f'{key}.json'), 'w', encoding='utf-8'))
    return at
