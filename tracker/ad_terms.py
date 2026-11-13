"""마케팅 추적: Google 광고주를 찾을 때 쓰는 검색어 만들기 (11/13 고도화). 브라우저 없이 쓰는 순수 함수만 둠.

광고 투명성 센터의 이름 검색은 단어 앞부분이 맞아야 나오고 한글 ↔ 영문을 이어 주지 않는다. 그래서
  - 이름은 출처를 가리지 않고 모두 모음: 순위 데이터의 퍼블리셔 · Play 법인명 · App Store 판매자/개발자 · Meta 페이지 이름
  - 이름마다 법인 표기((주) · ㈜ · 주식회사 · Co., Ltd · Inc · Pte. Ltd …)를 뗀 '알맹이'도 검색 → '(주)룡게임' 은 안 나와도 '룡게임' 은 '주식회사 룡게임' 을 찾음
  - 도메인은 웹사이트 · 판매자 주소 · 문의 메일 주소에서, 그리고 패키지명의 회사 부분(com.kakaogames.x → kakaogames)으로 추천받은 것까지
"""
import re
from urllib.parse import urlparse

MATCH_VER = 2   # 찾는 방법이 바뀌면 올림 → 저장된 계정이 예전 방법으로 찾은 것이면 한 번 다시 찾음

CORP = re.compile(r"\(주\)|㈜|주식회사|유한회사|\(유\)|株式会社|有限公司|[,.\s]+(co\.?,?\s*ltd|corporation|corp|inc|ltd|limited|private|llc|pte|gmbh|company)\.?(?=[\s,.]|$)", re.I)
JUNK = re.compile(r'google\.|gstatic|youtube|android\.com|apple\.com|ftc\.go\.kr|akamai|cloudfront|cdn|policies|privacy|facebook|instagram', re.I)
FREE_MAIL = {'gmail.com', 'googlemail.com', 'naver.com', 'daum.net', 'hanmail.net', 'kakao.com', 'nate.com', 'outlook.com', 'hotmail.com', 'live.com', 'yahoo.com', 'icloud.com', 'me.com',
             'qq.com', '163.com', '126.com', 'foxmail.com', 'proton.me', 'protonmail.com', 'yandex.ru', 'mail.ru'}
# 여러 회사가 같이 쓰는 주소: 통째로 버리는 곳(포털 · SNS · 링크 모음) / 하위 주소만 쓰는 곳(무료 호스팅 — ryonggame.blogspot.com 은 쓰고 blogspot.com 은 버림)
SHARED_DROP = {'naver.com', 'daum.net', 'kakao.com', 'kakaocorp.com', 'tistory.com', 'x.com', 'twitter.com', 'discord.gg', 'discord.com', 't.me', 'linktr.ee', 'onelink.me', 'notion.so', 'notion.site',
               'bit.ly', 'page.link', 'adjust.com', 'appsflyer.com', 'go.link', 'sng.link'}
SHARED_HOST = {'blogspot.com', 'web.app', 'firebaseapp.com', 'chatgpt.site', 'github.io', 'wixsite.com', 'weebly.com', 'wordpress.com', 'netlify.app', 'vercel.app', 'pages.dev', 'carrd.co',
               'imweb.me', 'cafe24.com', 'modoo.at', 'creatorlink.net', 'webflow.io', 'framer.website', 'itch.io'}
PKG_TLD = {'com', 'net', 'org', 'io', 'kr', 'co', 'jp', 'cn', 'us', 'app', 'games', 'game', 'gg', 'dev', 'me', 'tw', 'hk', 'sg', 'de', 'fr', 'uk', 'in', 'vn', 'id', 'th', 'ru', 'tr', 'br'}
PKG_GENERIC = {'google', 'android', 'game', 'games', 'global', 'mobile', 'studio', 'studios', 'app', 'apps', 'play', 'company', 'unity', 'dev'}

def core(n):
    """법인 표기를 뗀 이름: '(주)배드빈즈' → '배드빈즈', 'BAD BEANS CO., LTD' → 'BAD BEANS'"""
    v = n or ''
    for _ in range(4):   # 'PTE. LTD.' · 'Private Limited' 처럼 겹친 표기는 여러 번에 걸쳐 뗌
        w = re.sub(r'\s+', ' ', CORP.sub(' ', v)).strip(' ,.')
        if w == v: break
        v = w
    return v

def name_terms(names):
    """광고주 이름 검색어 (앞에 둔 것일수록 먼저 확인): 알맹이 먼저, 그다음 원래 이름. '(주)' 가 붙은 원래 이름은 검색이 되지 않아 뺌"""
    out = []
    for n in names:
        n = re.sub(r'\s+', ' ', n or '').strip()
        if not n: continue
        c = core(n)
        if len(c) >= 2: out.append(c)
        if c != n and not re.search(r'\((주|유)\)', n): out.append(n)
    seen, res = set(), []
    for v in out:
        if v.lower() not in seen: seen.add(v.lower()); res.append(v)
    return res

def host(u):
    h = urlparse(u if '://' in u else 'http://' + u).hostname or ''
    return re.sub(r'^www\.', '', h)

def domains_of(urls, emails=()):
    """주소 · 문의 메일 → 검색할 도메인: 원래 호스트 + 대표 도메인(dokkaebi.kakaogames.com → kakaogames.com). 공정위 · CDN · 무료 메일 제외"""
    out = []
    for u in list(urls) + [e.split('@')[-1] for e in emails if '@' in (e or '')]:
        h = host(u or '').lower()
        if not h or '.' not in h or JUNK.search(h) or h in FREE_MAIL: continue
        parts = h.split('.'); n = 3 if len(parts) > 2 and parts[-2] in ('co', 'com', 'or', 'ne', 'go') else 2
        top = '.'.join(parts[-n:])
        if top in SHARED_DROP: continue
        out += [h] if top in SHARED_HOST else [h, top]
    out = [d for d in out if d not in SHARED_HOST]
    return list(dict.fromkeys(out))

def pkg_hints(pkg):
    """패키지명의 회사 부분 (com.kakaogames.dokkaebi → kakaogames): 이 이름으로 추천되는 도메인을 찾는 데만 씀"""
    out = [p for p in (pkg or '').lower().split('.')[:3] if p not in PKG_TLD and p not in PKG_GENERIC and len(p) >= 4 and not p.isdigit()]
    return out[:1]


# ---------- Meta (11/13 고도화) ----------
# 광고 라이브러리의 키워드 검색은 '광고 문구'만 찾고 페이지 이름 · 링크 주소는 찾지 않는다. 그래서 검색어는 게임 이름을 여러 모양으로 넣고,
# 찾은 광고가 이 게임 것인지는 근거의 세기로 나눠 본다:
#   A  광고 링크에 Play 패키지 / iOS 앱 ID                      (가장 확실 — 예전부터 쓰던 기준)
#   B  광고 링크가 게임 공식 사이트 (사전예약 · 이벤트 페이지로 보내는 광고 — 스토어 링크가 없음)
#   C  추적 링크(onelink · adjust …)라 주소로는 알 수 없지만, 문구에 게임 이름이 있고 페이지 이름이 게임 · 회사 이름과 같음 (광고 2개 이상일 때만)
TRACKERS = re.compile(r'onelink\.me|adjust\.com|adj\.st|go\.link|sng\.link|app\.link|appsflyer|smart\.link|kochava|branch\.io|abr\.ge|airbridge|bit\.ly|page\.link', re.I)

def nzk(v):
    return re.sub(r'[\W_]', '', v or '').lower()

def game_queries(names):
    """Meta 검색어: 게임 이름(순위 데이터 · App Store 표기)마다 원래 이름 · 괄호 뺀 것 · 부제 앞/뒤 · 띄어쓰기 없는 것"""
    out = []
    for n in names:
        n = re.sub(r'\s+', ' ', n or '').strip()
        if not n: continue
        base = [n, re.sub(r'\s*\(.*?\)', '', n), re.sub(r'\s*[:：].*$', '', n), n.split(':')[-1], re.sub(r'\s*[-–]\s.*$', '', n), n.replace(':', ' ')]
        for v in base:
            v = re.sub(r'\s+', ' ', v).strip()
            if len(v) >= 2: out += [v] + ([v.replace(' ', '')] if ' ' in v and ':' not in v and len(v.replace(' ', '')) <= 12 else [])
    seen, res = set(), []
    for v in out:
        if v.lower() not in seen: seen.add(v.lower()); res.append(v)
    return res

def game_keys(names):
    """문구에 게임 이름이 있는지 볼 때 쓰는 열쇠 (기호 · 띄어쓰기 뺀 소문자): 원래 이름과 부제 앞부분. 너무 짧은 것(한글 2자 미만 · 영문 4자 미만)은 뺌"""
    out = set()
    for n in names:
        for v in [n or '', re.sub(r'\s*[:：(].*$', '', n or '')]:
            k = nzk(v)
            if len(k) >= (2 if re.search(r'[가-힣]', k) else 4): out.add(k)
    return out

def hosts_of(urls):
    """게임 공식 사이트의 호스트 (www 뺌). 포털 · SNS · 무료 호스팅 최상위 주소는 제외"""
    out = []
    for u in urls:
        h = host(u or '').lower()
        if not h or '.' not in h or JUNK.search(h) or h in SHARED_HOST or h in SHARED_DROP or TRACKERS.search(h): continue
        parts = h.split('.'); n = 3 if len(parts) > 2 and parts[-2] in ('co', 'com', 'or', 'ne', 'go') else 2
        if '.'.join(parts[-n:]) in SHARED_DROP: continue
        out.append(h)
    return list(dict.fromkeys(out))

def meta_evidence(snap, pkg, ios, hosts, keys, cores):
    """광고 1개가 이 게임 것인지: ('A'|'B'|'C', 설명) 또는 None.
    snap = 광고 라이브러리의 snapshot · hosts = 게임 공식 사이트 호스트 · keys = game_keys() · cores = 회사 이름 알맹이(nzk)"""
    import json as _json
    sj = _json.dumps(snap, ensure_ascii=False)
    if pkg and pkg in sj: return ('A', 'Play 패키지')
    if ios and f'id{ios}' in sj: return ('A', 'iOS 앱 ID')
    cards = snap.get('cards') or []
    links = [snap.get('link_url') or ''] + [c.get('link_url') or '' for c in cards]
    text = nzk(' '.join([snap.get('title') or '', (snap.get('body') or {}).get('text') or '', snap.get('link_description') or ''] + [(c.get('title') or '') + ' ' + (c.get('body') or '') for c in cards]))
    mention = any(k in text for k in keys)
    for u in links:
        h = host(u).lower() if u else ''
        if h and h in hosts:
            parts = h.split('.'); n = 3 if len(parts) > 2 and parts[-2] in ('co', 'com', 'or', 'ne', 'go') else 2
            if len(parts) > n or mention: return ('B', f'게임 사이트 {h}')   # 회사 대표 주소(하위 주소 없음)면 다른 게임 광고일 수 있어 문구에 게임 이름도 있어야 함
    page = nzk(snap.get('page_name'))
    if mention and page and any(TRACKERS.search(u or '') for u in links) and (page in cores or page in keys or any(len(k) >= 3 and k in page for k in keys)):
        return ('C', '페이지 이름 + 문구의 게임 이름')
    return None
