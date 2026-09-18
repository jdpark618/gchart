"""
wame.is 신작 캘린더 크롤러 → Google Sheets "월간신작DB" 적재

수정 사항 요약
  1. 타임존을 Asia/Seoul로 고정 (GitHub Actions는 UTC로 돌기 때문)
  2. clear() 후 append 하던 비원자적 쓰기를 update() 한 번으로 교체 + 백업 시트 생성
  3. get_all_values()로 읽어 gspread의 숫자 강제변환 제거 (has_changes 오탐 해소)
  4. 중복 판정 키를 게임ID 기준으로 변경, 레거시 중복은 자동 병합
  5. 캘린더에서 사라진 행 삭제 + 대량 삭제 방지 가드
  6. bare except 제거, 로깅 추가
  7. 상세정보 무한 재시도 방지 (상세확인일시 기반 백오프)
"""

import logging
import os
import json
import random
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import gspread
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ──────────────────────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────────────────────

KST = ZoneInfo("Asia/Seoul")

SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1CW7Xr3eWBUKBPC0DXRDsqqrx2itUlzZfXPVF2hUoMAw/edit"
)
DB_SHEET_NAME = "월간신작DB"
CONFIG_SHEET_NAME = "설정"
BACKUP_SHEET_NAME = "월간신작DB_백업"

HEADERS = [
    "출시일", "게임명", "플랫폼", "퍼블리셔", "출시유형",
    "게임ID", "아이콘", "한줄설명", "스크린샷",
    "최종확인일시", "상세확인일시",
]
LAST_COL = chr(ord("A") + len(HEADERS) - 1)  # "K"

# ── 설계 결정 1 ────────────────────────────────────────────────
# 같은 게임(게임ID)인데 출시유형만 다른 카드를 별개 행으로 둘지 여부.
#   True(기본) = 별개 행으로 유지.
#                wame는 한 게임을 "테스트 9/8", "정식 출시 9/21" 처럼
#                복수 일정으로 등재하므로 병합하면 한쪽이 유실된다.
#                유형이 제자리에서 바뀌는 경우(사전예약→정식출시)는
#                옛 행이 삭제 로직에 걸려 정리되므로 중복이 남지 않는다.
#   False      = 한 행으로 병합, 출시유형은 최신 관측값으로 덮어씀
INCLUDE_RELEASE_TYPE_IN_KEY = True

# ── 설계 결정 1-b ──────────────────────────────────────────────
# 플랫폼을 중복 판정 키에 포함할지 여부.
#   False(기본) = 키에서 제외하고 갱신 대상 속성으로 취급.
#                 wame는 "PC / 모바일" 처럼 결합 문자열로 표기하므로
#                 플랫폼은 행을 가르는 차원이 아니다. 키에 넣으면
#                 표기가 바뀔 때마다 삭제+신규로 churn이 발생한다.
#   True       = 플랫폼별 별개 행 (플랫폼이 안정적으로 분리될 때만)
INCLUDE_PLATFORM_IN_KEY = False

# ── 설계 결정 2 ────────────────────────────────────────────────
# 게임ID를 못 뽑은 행의 중복 판정을 정규화한 게임명+플랫폼으로 폴백할지 여부.
# False로 두면 ID 없는 행은 절대 병합되지 않습니다(안전하지만 중복이 남음).
USE_NAME_FALLBACK_KEY = True

# ── 설계 결정 3 ────────────────────────────────────────────────
# 레거시 중복 정리 범위. True면 매 실행마다 DB 전체를 게임ID 기준으로 병합합니다.
# (첫 실행에서 과거 중복이 한 번에 정리됩니다)
MERGE_LEGACY_DUPLICATES = True

# ── 안전 가드 ──────────────────────────────────────────────────
MIN_SCRAPE_RETENTION = 0.5   # 스캔 범위 내 기존 행 대비 수집량이 이 비율 미만이면 쓰기 중단
MAX_DELETE_RATIO = 0.30      # 삭제 대상이 스캔 범위 행의 이 비율을 넘으면 삭제만 건너뜀
MAX_DELETE_ABS = 20          # 위 비율 계산의 하한 (소규모일 때 과민반응 방지)
CREATE_BACKUP = True

# ── 크롤링 ────────────────────────────────────────────────────
CARD_SELECTOR = ".px-5.pt-5"
NAV_TIMEOUT_MS = 20000
CARD_WAIT_MS = 5000
NAV_RETRIES = 3
DETAIL_RETRY_DAYS = 14       # 상세정보 수집 실패 후 재시도까지 대기일
MAX_DETAIL_FETCH = 250       # 1회 실행당 상세페이지 요청 상한
DETAIL_SLEEP = 0.3

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# 러너가 UTC이므로 로그 접두 타임스탬프도 KST로 맞춘다
logging.Formatter.converter = lambda *args: datetime.now(ZoneInfo("Asia/Seoul")).timetuple()
log = logging.getLogger("wame")


def now_kst():
    return datetime.now(KST)


def now_str():
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")


# ──────────────────────────────────────────────────────────────
# 1. 날짜 계산
# ──────────────────────────────────────────────────────────────

def get_dates_in_month(year, month):
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = datetime(year, month + 1, 1) - timedelta(days=1)

    dates = []
    cur = start
    while cur <= end:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return dates


# ──────────────────────────────────────────────────────────────
# 2. 캘린더 크롤링
# ──────────────────────────────────────────────────────────────

def extract_game_id(card):
    """카드 내/외부의 /game/{id} 링크에서 게임ID를 추출한다."""
    anchor = card.find_parent("a", href=True)
    if anchor:
        m = re.search(r"/game/(\d+)", anchor.get("href", ""))
        if m:
            return m.group(1)

    for a in card.find_all("a", href=True):
        m = re.search(r"/game/(\d+)", a["href"])
        if m:
            return m.group(1)

    for attr in ("data-game-id", "data-id", "data-gameid"):
        v = card.get(attr)
        if v and str(v).strip().isdigit():
            return str(v).strip()

    return ""


def parse_cards(html, date_str):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all(
        "div", class_=lambda c: c and "px-5" in c and "pt-5" in c
    )

    items = []
    for card in cards:
        try:
            title_elem = card.find("p", class_="line-clamp-2")
            title = title_elem.text.strip() if title_elem else ""

            if not title or title == "N/A":
                log.warning("[%s] 게임명 파싱 실패 카드 1건 스킵", date_str)
                continue

            platform_elem = card.find(
                "span", class_=lambda c: c and "text-xs" in c and "text-black" in c
            )
            platform = platform_elem.text.strip() if platform_elem else ""

            publisher = ""
            release_type = ""
            for row in card.find_all("div", class_="gap-3"):
                label_elem = row.find("div", class_="shrink-0")
                value_elem = row.find("span", class_="truncate")
                if not (label_elem and value_elem):
                    continue
                label = label_elem.text.strip()
                val = value_elem.text.strip()
                if label == "퍼블리셔":
                    publisher = val
                elif label == "출시 유형":
                    release_type = val

            items.append({
                "출시일": date_str,
                "게임명": title,
                "플랫폼": platform,
                "퍼블리셔": publisher,
                "출시유형": release_type,
                "게임ID": extract_game_id(card),
            })
        except Exception as e:
            log.warning("[%s] 카드 파싱 오류: %s", date_str, e)
            continue

    return items


def scrape_one_date(page, date_str):
    """성공 시 리스트(0건 포함), 실패 시 None을 반환한다."""
    url = f"https://www.wame.is/ko/calendar?date={date_str}"

    for attempt in range(1, NAV_RETRIES + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            log.warning("[%s] 페이지 로드 실패 (%d/%d): %s",
                        date_str, attempt, NAV_RETRIES, e)
            time.sleep(1.5 * attempt + random.random())
            continue

        try:
            page.wait_for_selector(CARD_SELECTOR, timeout=CARD_WAIT_MS)
        except PlaywrightTimeout:
            # 페이지는 정상적으로 떴으나 카드가 없음 → 그날 신작 0건으로 확정
            return []

        try:
            return parse_cards(page.content(), date_str)
        except Exception as e:
            log.warning("[%s] 본문 파싱 실패 (%d/%d): %s",
                        date_str, attempt, NAV_RETRIES, e)
            time.sleep(1.5 * attempt)
            continue

    return None


def scrape_calendar(year_months):
    target_dates = []
    for year, month in year_months:
        target_dates.extend(get_dates_in_month(year, month))

    games, scanned_ok, failed = [], set(), set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT, locale="ko-KR")
        page = ctx.new_page()
        page.set_default_timeout(NAV_TIMEOUT_MS)

        for date_str in target_dates:
            result = scrape_one_date(page, date_str)
            if result is None:
                failed.add(date_str)
                log.error("[%s] 수집 실패 — 이 날짜는 삭제 판정에서 제외", date_str)
            else:
                scanned_ok.add(date_str)
                games.extend(result)

        ctx.close()
        browser.close()

    return games, scanned_ok, failed


# ──────────────────────────────────────────────────────────────
# 3. 상세페이지 크롤링
# ──────────────────────────────────────────────────────────────

def scrape_game_detail(game_id, session):
    url = f"https://www.wame.is/ko/game/{game_id}"
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        log.warning("  [상세정보 실패] gameId=%s: %s", game_id, e)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    icon = ""
    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.get("content"):
        icon = og_image["content"].strip()

    summary = ""
    desc_meta = soup.find("meta", attrs={"name": "description"})
    if desc_meta and desc_meta.get("content"):
        summary = desc_meta["content"].strip()
    else:
        og_desc = soup.find("meta", attrs={"property": "og:description"})
        if og_desc and og_desc.get("content"):
            summary = og_desc["content"].strip()

    screenshots, seen = [], set()
    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").lower()
        src = (img.get("src") or img.get("data-src") or "").strip()
        if not src or src in seen:
            continue
        if "screenshot" in alt or "/screenshot/" in src:
            if src.startswith("/"):
                src = "https://www.wame.is" + src
            screenshots.append(src)
            seen.add(src)
        if len(screenshots) >= 4:
            break

    return {
        "아이콘": icon,
        "한줄설명": summary,
        "스크린샷": " | ".join(screenshots),
    }


# ──────────────────────────────────────────────────────────────
# 4. 중복 판정 및 병합 저장소
# ──────────────────────────────────────────────────────────────

# 이번 실행의 관측값으로 덮어쓸 필드.
# 키에 포함된 필드는 애초에 매칭 조건이므로 갱신 대상에서 제외한다.
CAL_FIELDS = ["출시일", "게임명", "퍼블리셔"]
if not INCLUDE_PLATFORM_IN_KEY:
    CAL_FIELDS.append("플랫폼")
if not INCLUDE_RELEASE_TYPE_IN_KEY:
    CAL_FIELDS.append("출시유형")

DETAIL_FIELDS = ["아이콘", "한줄설명", "스크린샷"]


def s(rec, field):
    return str(rec.get(field) or "").strip()


def norm_name(name):
    v = str(name or "").strip().lower()
    v = re.sub(r"[\s\u200b\u00a0]+", " ", v)
    v = re.sub(r"[^\w가-힣 ]+", "", v)
    return v.strip()


def key_suffix(rec):
    parts = []
    if INCLUDE_PLATFORM_IN_KEY:
        parts.append(s(rec, "플랫폼"))
    if INCLUDE_RELEASE_TYPE_IN_KEY:
        parts.append(s(rec, "출시유형"))
    return parts


def id_key(rec):
    gid = s(rec, "게임ID")
    if not gid:
        return None
    return "|".join([f"id:{gid}"] + key_suffix(rec))


def name_key(rec):
    if not USE_NAME_FALLBACK_KEY:
        return None
    nm = norm_name(rec.get("게임명"))
    if not nm or nm in ("na", "n a"):
        return None
    return "|".join([f"nm:{nm}"] + key_suffix(rec))


def completeness(rec):
    return sum(1 for h in HEADERS if s(rec, h))


def is_newer(a, b):
    """a가 b보다 최신 관측이면 True."""
    ka = (s(a, "최종확인일시"), s(a, "출시일"), completeness(a))
    kb = (s(b, "최종확인일시"), s(b, "출시일"), completeness(b))
    return ka > kb


class Store:
    """게임ID(폴백: 정규화 게임명) + 플랫폼 기준으로 행을 유일하게 유지한다."""

    def __init__(self):
        self.records = {}     # seq -> record
        self.by_id = {}       # id_key -> seq
        self.by_name = {}     # name_key -> seq
        self._seq = 0
        self.changed = False
        self.merged_count = 0

    def _index(self, seq, rec):
        ik = id_key(rec)
        if ik:
            self.by_id[ik] = seq
        nk = name_key(rec)
        if nk:
            self.by_name[nk] = seq

    def _resolve(self, rec):
        ik = id_key(rec)
        if ik and ik in self.by_id:
            return self.by_id[ik]

        nk = name_key(rec)
        if nk and nk in self.by_name:
            seq = self.by_name[nk]
            cand_id = s(self.records[seq], "게임ID")
            rec_id = s(rec, "게임ID")
            # 양쪽 다 ID가 있는데 서로 다르면 동명이 다른 게임 → 병합하지 않음
            if not cand_id or not rec_id or cand_id == rec_id:
                return seq
        return None

    def _insert(self, rec):
        self._seq += 1
        seq = self._seq
        self.records[seq] = {h: s(rec, h) for h in HEADERS}
        self._index(seq, self.records[seq])
        return seq

    def load_db_row(self, rec):
        """시트에서 읽은 기존 행을 적재한다. 레거시 중복은 최신 관측을 택해 병합."""
        seq = self._resolve(rec) if MERGE_LEGACY_DUPLICATES else None
        if seq is None:
            return self._insert(rec)

        cur = self.records[seq]
        winner, loser = (rec, cur) if is_newer(rec, cur) else (cur, rec)
        merged = {h: s(loser, h) for h in HEADERS}
        for h in HEADERS:
            v = s(winner, h)
            if v:
                merged[h] = v

        self.records[seq] = merged
        self._index(seq, merged)
        self.merged_count += 1
        self.changed = True
        log.info("레거시 중복 병합: %s / %s (출시일 %s ← %s)",
                 merged.get("게임명"), merged.get("플랫폼"),
                 merged.get("출시일"), s(loser, "출시일"))
        return seq

    def apply_scrape(self, item):
        """이번 실행에서 관측한 값을 반영한다. 캘린더 필드는 항상 최신값이 이긴다."""
        seq = self._resolve(item)
        if seq is None:
            rec = {h: "" for h in HEADERS}
            rec.update({h: s(item, h) for h in
                        ["출시일", "게임명", "플랫폼", "퍼블리셔", "출시유형", "게임ID"]})
            rec["최종확인일시"] = now_str()
            seq = self._insert(rec)
            self.changed = True
            log.info("신규 등록: %s (%s, %s)",
                     rec["게임명"], rec["플랫폼"], rec["출시일"])
            return seq

        cur = self.records[seq]
        for f in CAL_FIELDS:
            v = s(item, f)
            if v and v != "N/A" and s(cur, f) != v:
                log.info("갱신: %s [%s] %s → %s",
                         cur.get("게임명"), f, s(cur, f) or "(빈값)", v)
                cur[f] = v
                self.changed = True

        gid = s(item, "게임ID")
        if gid and s(cur, "게임ID") != gid:
            cur["게임ID"] = gid
            self.changed = True

        cur["최종확인일시"] = now_str()
        self._index(seq, cur)
        return seq

    def rows(self):
        out = [[rec.get(h, "") for h in HEADERS] for rec in self.records.values()]
        out.sort(key=lambda r: (r[0], r[1]))
        return out


# ──────────────────────────────────────────────────────────────
# 5. Google Sheets I/O
# ──────────────────────────────────────────────────────────────

def with_retry(fn, *args, attempts=4, **kwargs):
    for i in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (429, 500, 502, 503, 504) and i < attempts:
                wait = 2 ** i + random.random()
                log.warning("Sheets API %s — %.1fs 후 재시도 (%d/%d)",
                            code, wait, i, attempts)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("with_retry: 재시도 소진")


def open_sheet():
    creds_json = os.environ.get("GCP_CREDENTIALS")
    if creds_json:
        client = gspread.service_account_from_dict(json.loads(creds_json))
    else:
        client = gspread.service_account(filename="credentials.json")
    return client.open_by_url(SHEET_URL)


def read_db(db_sheet):
    """get_all_values()로 읽어 모든 값을 문자열로 유지한다."""
    raw = with_retry(db_sheet.get_all_values)
    if not raw:
        return [], []

    header = [h.strip() for h in raw[0]]
    records = []
    for row in raw[1:]:
        if not any(str(c).strip() for c in row):
            continue
        rec = {}
        for i, h in enumerate(header):
            if h in HEADERS:
                rec[h] = str(row[i]).strip() if i < len(row) else ""
        for h in HEADERS:
            rec.setdefault(h, "")
        records.append(rec)
    return records, raw


def write_backup(sheet, raw):
    if not CREATE_BACKUP or not raw:
        return
    try:
        try:
            bak = sheet.worksheet(BACKUP_SHEET_NAME)
        except gspread.exceptions.WorksheetNotFound:
            bak = sheet.add_worksheet(
                title=BACKUP_SHEET_NAME,
                rows=max(len(raw) + 100, 1000),
                cols=len(HEADERS) + 2,
            )
        with_retry(bak.clear)
        with_retry(bak.update, range_name="A1", values=raw)
        log.info("백업 완료: %s (%d행)", BACKUP_SHEET_NAME, len(raw))
    except Exception as e:
        log.warning("백업 실패 (본 작업은 계속 진행): %s", e)


def write_db(db_sheet, rows, prev_row_count):
    """clear() 없이 덮어쓰고, 줄어든 만큼의 꼬리 행만 비운다."""
    values = [HEADERS] + rows
    needed = len(values) + 50
    if db_sheet.row_count < needed:
        with_retry(db_sheet.resize, rows=needed, cols=max(db_sheet.col_count, len(HEADERS)))

    with_retry(db_sheet.update, range_name=f"A1:{LAST_COL}{len(values)}", values=values)

    prev_total = prev_row_count + 1  # 헤더 포함
    if prev_total > len(values):
        tail = f"A{len(values) + 1}:{LAST_COL}{prev_total}"
        with_retry(db_sheet.batch_clear, [tail])
        log.info("잉여 행 정리: %s", tail)


# ──────────────────────────────────────────────────────────────
# 6. 메인 처리
# ──────────────────────────────────────────────────────────────

def run(scraped, scanned_ok, failed_dates):
    sheet = open_sheet()
    db_sheet = with_retry(sheet.worksheet, DB_SHEET_NAME)
    config_sheet = with_retry(sheet.worksheet, CONFIG_SHEET_NAME)

    existing, raw = read_db(db_sheet)
    db_empty = len(existing) == 0
    log.info("기존 DB %d행 로드", len(existing))

    # ── 가드 1: 수집 0건이면 아무것도 쓰지 않는다 ──
    if not scraped and not db_empty:
        log.error("수집 0건 — 셀렉터 파손 또는 네트워크 문제로 판단하여 쓰기를 중단합니다.")
        return
    if not scanned_ok and not db_empty:
        log.error("성공적으로 스캔된 날짜가 없습니다 — 쓰기를 중단합니다.")
        return

    # ── 가드 2: 스캔 범위 내 수집량이 급감하면 중단 ──
    in_range_existing = [r for r in existing if s(r, "출시일") in scanned_ok]
    if len(in_range_existing) >= 20:
        ratio = len(scraped) / len(in_range_existing)
        if ratio < MIN_SCRAPE_RETENTION:
            log.error(
                "수집량 급감 (기존 %d행 → 수집 %d건, %.0f%%) — 쓰기를 중단합니다.",
                len(in_range_existing), len(scraped), ratio * 100,
            )
            return

    write_backup(sheet, raw)

    # ── 적재 및 병합 ──
    store = Store()
    for rec in existing:
        store.load_db_row(rec)
    if store.merged_count:
        log.info("레거시 중복 %d건 병합됨", store.merged_count)

    seen = set()
    for item in scraped:
        seen.add(store.apply_scrape(item))

    # ── 삭제: 정상 스캔된 날짜인데 이번에 안 보인 행 ──
    stale = [
        seq for seq, rec in store.records.items()
        if seq not in seen and s(rec, "출시일") in scanned_ok
    ]
    limit = max(MAX_DELETE_ABS, int(len(in_range_existing) * MAX_DELETE_RATIO))
    if len(stale) > limit:
        log.error(
            "삭제 대상 %d건이 임계치(%d)를 초과 — 삭제는 건너뛰고 갱신만 반영합니다.",
            len(stale), limit,
        )
    else:
        for seq in stale:
            rec = store.records.pop(seq)
            log.info("삭제: %s (%s, %s) — 캘린더에서 사라짐",
                     rec.get("게임명"), rec.get("플랫폼"), rec.get("출시일"))
            store.changed = True

    # ── 상세정보 보강 ──
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    today = now_kst()
    fetched = 0

    for rec in store.records.values():
        if fetched >= MAX_DETAIL_FETCH:
            log.info("상세정보 요청 상한(%d) 도달 — 나머지는 다음 실행으로 이월",
                     MAX_DETAIL_FETCH)
            break

        gid = s(rec, "게임ID")
        if not gid:
            continue
        if s(rec, "아이콘") and s(rec, "한줄설명"):
            continue

        last_try = s(rec, "상세확인일시")
        if last_try:
            try:
                prev = datetime.strptime(last_try[:10], "%Y-%m-%d").replace(tzinfo=KST)
                if (today - prev).days < DETAIL_RETRY_DAYS:
                    continue
            except ValueError:
                pass

        detail = scrape_game_detail(gid, session)
        rec["상세확인일시"] = now_str()
        fetched += 1
        if detail and (detail["아이콘"] or detail["한줄설명"]):
            rec.update(detail)
            store.changed = True
        time.sleep(DETAIL_SLEEP)

    log.info("상세정보 수집 시도: %d건", fetched)

    no_id = sum(1 for r in store.records.values() if not s(r, "게임ID"))
    no_detail = sum(1 for r in store.records.values()
                    if not s(r, "아이콘") or not s(r, "한줄설명"))
    log.info("건강 지표 — 게임ID 없음 %d행 / 상세정보 미보강 %d행 (전체 %d행)",
             no_id, no_detail, len(store.records))

    # ── 쓰기 ──
    rows = store.rows()
    log.info("최종 %d행 기록 (이전 %d행)", len(rows), len(existing))
    write_db(db_sheet, rows, len(existing))

    # ── 발송 트리거 ──
    is_month_start = now_kst().day == 1
    if db_empty or store.changed or is_month_start:
        with_retry(config_sheet.update_acell, "B1", "발송요청")
        log.info("트리거 발동 (변경=%s, 월초=%s, 최초=%s)",
                 store.changed, is_month_start, db_empty)
    else:
        log.info("트리거 미발동: 변경점이 없습니다.")


if __name__ == "__main__":
    now = now_kst()
    curr_year, curr_month = now.year, now.month
    if curr_month == 12:
        next_year, next_month = curr_year + 1, 1
    else:
        next_year, next_month = curr_year, curr_month + 1

    log.info("실행 시각(KST): %s", now.strftime("%Y-%m-%d %H:%M:%S"))
    log.info("%d년 %d월 및 %d년 %d월 크롤링 시작...",
             curr_year, curr_month, next_year, next_month)

    scraped_data, ok_dates, failed_dates = scrape_calendar(
        [(curr_year, curr_month), (next_year, next_month)]
    )

    log.info("수집 %d건 / 정상 스캔 %d일 / 실패 %d일",
             len(scraped_data), len(ok_dates), len(failed_dates))
    if failed_dates:
        log.warning("실패 날짜: %s", ", ".join(sorted(failed_dates)))

    run(scraped_data, ok_dates, failed_dates)
    log.info("모든 작업 완료.")
