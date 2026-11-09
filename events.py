"""행사 달력 수집 (11/09): 공개 구글 캘린더(ICS) → data/events.json (대시보드 행사 달력이 직접 그림).
브라우저는 구글 캘린더 파일을 직접 못 읽어서(CORS) 여기서 받아 Pages 에 올린다.
일정이 바뀌었을 때만 파일을 다시 씀 → 워크플로는 바뀐 경우에만 커밋. updated = 마지막으로 내용이 바뀐 시각(KST)."""
import html, json, os, re, sys, urllib.request
from datetime import date, datetime, timedelta, timezone

CAL_ID = 'c_58bf32be64488847f1007ad21615b0fcdf767a96764ea45106af7b5b75974456@group.calendar.google.com'
URL = f'https://calendar.google.com/calendar/ical/{CAL_ID.replace("@", "%40")}/public/basic.ics'
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'data', 'events.json')
KST = timezone(timedelta(hours=9))


def unesc(v):
    return v.replace('\\n', '\n').replace('\\N', '\n').replace('\\,', ',').replace('\\;', ';').replace('\\\\', '\\').strip()


def clean_desc(v):
    """설명: HTML 태그 · 엔티티 · 마크다운 굵게(**) 정리, 빈 줄 하나로"""
    v = html.unescape(re.sub(r'<br\s*/?>|</p>|</li>', '\n', unesc(v)))
    v = re.sub(r'<[^>]+>', '', v).replace('**', '')
    return re.sub(r'\n{3,}', '\n\n', v).strip()


def parse(ics):
    ics = re.sub(r'\r?\n[ \t]', '', ics.replace('\r\n', '\n'))   # 접힌 줄 펴기
    out = []
    for blk in ics.split('BEGIN:VEVENT')[1:]:
        f = {}
        for line in blk.split('END:VEVENT')[0].strip().split('\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                f[k.split(';')[0]] = (k, v)

        def when(key):
            if key not in f:
                return None
            k, v = f[key]
            if 'VALUE=DATE' in k or len(v) == 8:
                return date(int(v[:4]), int(v[4:6]), int(v[6:8])), True, ''
            t = datetime.strptime(v[:15], '%Y%m%dT%H%M%S')
            if v.endswith('Z'):
                t = t.replace(tzinfo=timezone.utc).astimezone(KST)   # TZID 가 붙은 값은 캘린더가 서울 시간이라 그대로 씀
            return t.date(), False, t.strftime('%H:%M')

        s, e = when('DTSTART'), when('DTEND')
        if not s or f.get('STATUS', ('', ''))[1] == 'CANCELLED':
            continue
        sd = s[0]
        ed = (e[0] - timedelta(days=1) if e[1] else e[0]) if e else sd   # 종일 일정의 끝은 다음 날 0시로 옴
        ed = max(ed, sd)
        out.append({'t': unesc(f.get('SUMMARY', ('', ''))[1]), 's': sd.isoformat(), 'e': ed.isoformat(),
                    'tm': '' if s[1] else s[2], 'loc': unesc(f.get('LOCATION', ('', ''))[1])[:120],
                    'url': unesc(f.get('URL', ('', ''))[1])[:300], 'desc': clean_desc(f.get('DESCRIPTION', ('', ''))[1])})
        d = out[-1]
        if not d['url']:   # 설명 속 첫 링크(홈페이지 등)를 바로가기로
            m = re.search(r'https?://[^\s<>"\')]+', d['desc'])
            d['url'] = m.group(0)[:300] if m else ''
        d['desc'] = d['desc'][:600]
    return sorted(out, key=lambda x: (x['s'], x['e'], x['t']))


def main():
    ics = urllib.request.urlopen(urllib.request.Request(URL, headers={'User-Agent': 'chartdeck-events'}), timeout=60).read().decode('utf-8')
    events = parse(ics)
    if not events:
        print('일정 0건 — 캘린더 응답이 이상해 덮어쓰지 않음'); sys.exit(1)
    try:
        prev = json.load(open(OUT, encoding='utf-8'))
    except Exception:
        prev = {}
    if prev.get('events') == events:
        print(f'변경 없음 ({len(events)}건)'); return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({'updated': datetime.now(KST).isoformat(timespec='minutes'), 'events': events}, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'저장: {len(events)}건 (이전 {len(prev.get("events", []))}건)')


if __name__ == '__main__':
    main()
