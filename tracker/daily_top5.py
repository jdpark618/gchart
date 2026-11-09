"""매일 마케팅 추적 대상 (11/09): 그날 순위 스냅샷(Google Play · 한국)의 인기 1~5위 + 매출 1~5위.
TOP5 에서 빠진 게임은 더 추적하지 않음 (사이트 목록에는 마지막 추적일 기준으로 남음).
처음 보는 게임은 ad_track.py 가 광고 계정을 자동으로 찾아 data/ads/accounts.json 에 저장.
각 게임에 '--ranks popular:2,sales:3' 처럼 그날 순위를 넘겨 사이트가 인기/매출 순위 추적으로 나눠 보여줌."""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
snap = json.load(open(os.path.join(ROOT, 'data', 'snapshot.json'), encoding='utf-8'))
charts = snap['charts']['gp']['kr']
games = {}
for chart in ('popular', 'sales'):
    for r in charts[chart]['rows'][:5]:
        g = games.setdefault(r[4], {'name': r[3], 'pkg': r[4], 'publisher': r[6], 'ranks': {}, 'date': r[0]})
        g['ranks'][chart] = r[1]
print(f"{snap.get('today', '')} 추적 대상 {len(games)}개:", ', '.join(f"{g['name']}({'/'.join(f'{k} {v}' for k, v in g['ranks'].items())})" for g in games.values()), flush=True)
for g in games.values():
    subprocess.run([sys.executable, os.path.join(ROOT, 'tracker', 'ad_track.py'), '--name', g['name'], '--pkg', g['pkg'], '--publisher', g['publisher'] or '',
                    '--ranks', ','.join(f'{k}:{v}' for k, v in g['ranks'].items()), '--rank-date', g['date']], check=False)
