"""GitHub Pages 용량 기록 (11/18): main 브랜치 파일 합계(= 공개되는 사이트 크기)를 날마다 data/pages_usage.json 에 남김.
{ limit: 1GB(GitHub 권장), hist: [{ d, bytes }] } 최근 90일 · 수집 로그 페이지(/admin)가 막대 · % · 수명으로 보여 줌.
마케팅 추적이 저장하기 직전에 실행 (용량 대부분이 그 썸네일이라 그날 늘어난 만큼이 바로 반영됨)."""
import json, os
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = {'.git', '__pycache__'}
total = 0
for base, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP]
    total += sum(os.path.getsize(os.path.join(base, f)) for f in files)
day = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d')
path = os.path.join(ROOT, 'data', 'pages_usage.json')
try: hist = json.load(open(path, encoding='utf-8')).get('hist', [])
except Exception: hist = []
hist = sorted([x for x in hist if x['d'] != day] + [{'d': day, 'bytes': total}], key=lambda x: x['d'])[-90:]
json.dump({'limit': 1000000000, 'hist': hist}, open(path, 'w', encoding='utf-8'), indent=1)
print(f'Pages 용량 {total / 1e6:.1f}MB ({total / 1e7:.1f}%)')
