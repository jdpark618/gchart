"""서버 IP 확인 (11/19): 마케팅 추적 세 단계(TOP5 · 순번 · 보충)는 모두 다른 IP 에서 돌아야 함.
사용: python tracker/ip_guard.py <단계: top5|rotation|fill> <시도 번호>
- 오늘 다른 단계의 수집 로그(data/runlog/<날짜>/ads · ads_rot · ads_fill)에 남은 '서버 IP' 와 지금 IP 를 비교
- 같으면: 3번째 시도 전까지는 이 단계부터 워크플로를 새로 실행(새 서버)하고 이번 실행은 여기서 멈춤 (dup=true)
          3번째에도 같으면 오늘은 이 단계를 건너뛰고 실패로 기록 → 다음 예비 예약에서 다시 (gate.py 가 진행시킴)
결과: GITHUB_OUTPUT 에 ip · dup."""
import json, os, re, subprocess, sys, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE, ATTEMPT = sys.argv[1], int(sys.argv[2] or 1)
CATS = {'top5': 'ads', 'rotation': 'ads_rot', 'fill': 'ads_fill'}
day = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d')


def my_ip():
    for u in ('https://api.ipify.org', 'https://ifconfig.me/ip', 'https://icanhazip.com'):
        try:
            v = urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'curl/8'}), timeout=10).read().decode().strip()
            if re.fullmatch(r'[\d.]+|[0-9a-fA-F:]+', v): return v
        except Exception: pass
    return ''


def used_ips():
    out = set()
    for stage, cat in CATS.items():
        if stage == STAGE: continue
        try: rec = json.load(open(os.path.join(ROOT, 'data', 'runlog', day, f'{cat}.json'), encoding='utf-8'))
        except Exception: continue
        for a in rec.get('attempts', []):
            for _, t in a.get('msgs', []):
                m = re.search(r'서버 IP ([0-9a-fA-F.:]+)', t)
                if m: out.add(m.group(1))
    return out


ip, used = my_ip(), used_ips()
dup = bool(ip) and ip in used
print(f'{day} {STAGE} 단계 서버 IP {ip or "확인 실패"} · 오늘 다른 단계 IP {sorted(used) or "없음"} → {"중복" if dup else "사용 가능"}', flush=True)
if dup:
    if ATTEMPT < 3:
        subprocess.run(['gh', 'workflow', 'run', 'ad_track.yml', '-R', os.environ.get('GITHUB_REPOSITORY', 'jdpark618/gchart'), '-f', f'stage={STAGE}', '-f', f'attempt={ATTEMPT + 1}'], check=False)
        print(f'IP 가 겹쳐 {STAGE} 단계부터 새 서버로 다시 실행 (시도 {ATTEMPT + 1})', flush=True)
    else:
        sys.path.insert(0, ROOT); import runlog
        runlog.add(CATS[STAGE], '이어서', runlog.now(), False, [['x', f'서버 IP {ip} 가 오늘 다른 단계와 3번 연속 겹쳐 실행하지 않음 → 예비 예약에서 다시']])
out = os.environ.get('GITHUB_OUTPUT')
if out:
    with open(out, 'a') as f: f.write(f'ip={ip or "알수없음"}\ndup={"true" if dup else "false"}\n')
