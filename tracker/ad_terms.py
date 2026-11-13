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
