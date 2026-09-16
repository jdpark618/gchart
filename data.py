import os
import json
import time
import re
from datetime import datetime, timedelta

import gspread
import requests
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

HEADERS = ["출시일", "게임명", "플랫폼", "퍼블리셔", "출시유형", "게임ID", "아이콘", "한줄설명", "스크린샷"]


# --- 1. 날짜 계산 함수 ---
def get_dates_in_month(year, month):
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        end_date = datetime(year, month + 1, 1) - timedelta(days=1)

    date_list = []
    current = start_date
    while current <= end_date:
        date_list.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return date_list


# --- 2. 캘린더 크롤링 함수 (Playwright, 기존 로직 + gameId 추출 추가) ---
def scrape_calendar_by_url(year_months):
    target_dates = []
    for year, month in year_months:
        target_dates.extend(get_dates_in_month(year, month))

    monthly_games = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for date_str in target_dates:
            url = f"https://www.wame.is/ko/calendar?date={date_str}"
            page.goto(url)

            try:
                page.wait_for_selector(".px-5.pt-5", timeout=3000)
            except:
                continue

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")

            game_cards = soup.find_all("div", class_=lambda c: c and "px-5" in c and "pt-5" in c)

            for card in game_cards:
                try:
                    title_elem = card.find("p", class_="line-clamp-2")
                    title = title_elem.text.strip() if title_elem else "N/A"

                    platform_elem = card.find("span", class_=lambda c: c and "text-xs" in c and "text-black" in c)
                    platform = platform_elem.text.strip() if platform_elem else "N/A"

                    publisher = "N/A"
                    release_type = "N/A"

                    info_rows = card.find_all("div", class_="gap-3")
                    for row in info_rows:
                        label_elem = row.find("div", class_="shrink-0")
                        value_elem = row.find("span", class_="truncate")

                        if label_elem and value_elem:
                            label = label_elem.text.strip()
                            val = value_elem.text.strip()

                            if label == "퍼블리셔":
                                publisher = val
                            elif label == "출시 유형":
                                release_type = val

                    # --- 카드를 감싸는 <a href="/ko/game/{id}..."> 에서 gameId 추출 ---
                    game_id = ""
                    anchor = card if card.name == "a" else card.find_parent("a")
                    if not anchor:
                        anchor = card.find("a", href=True)
                    if anchor and anchor.get("href"):
                        m = re.search(r"/game/(\d+)", anchor["href"])
                        if m:
                            game_id = m.group(1)

                    monthly_games.append({
                        "출시일": date_str,
                        "게임명": title,
                        "플랫폼": platform,
                        "퍼블리셔": publisher,
                        "출시유형": release_type,
                        "게임ID": game_id,
                    })
                except:
                    continue

        browser.close()
    return monthly_games


# --- 3. 상세페이지 크롤링 함수 (requests, 신규/미보강 게임만 호출) ---
def scrape_game_detail(game_id, session):
    url = f"https://www.wame.is/ko/game/{game_id}"
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [상세정보 실패] gameId={game_id}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    icon = ""
    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.get("content"):
        icon = og_image["content"]

    summary = ""
    desc_meta = soup.find("meta", attrs={"name": "description"})
    if desc_meta and desc_meta.get("content"):
        summary = desc_meta["content"].strip()
    else:
        og_desc = soup.find("meta", attrs={"property": "og:description"})
        if og_desc and og_desc.get("content"):
            summary = og_desc["content"].strip()

    screenshots = []
    seen = set()
    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").lower()
        src = img.get("src") or ""
        if src and src not in seen and ("screenshot" in alt or "/screenshot/" in src):
            screenshots.append(src)
            seen.add(src)
        if len(screenshots) >= 4:
            break

    return {
        "아이콘": icon,
        "한줄설명": summary,
        "스크린샷": " | ".join(screenshots),
    }


# --- 4. DB 누적 업데이트 및 발송 트리거 함수 (Upsert + 상세정보 보강) ---
def update_db_and_trigger(scraped_data):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

    creds_json = os.environ.get("GCP_CREDENTIALS")
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)

    client = gspread.authorize(creds)

    sheet_url = "https://docs.google.com/spreadsheets/d/1CW7Xr3eWBUKBPC0DXRDsqqrx2itUlzZfXPVF2hUoMAw/edit?gid=2017461349#gid=2017461349"
    sheet = client.open_by_url(sheet_url)

    db_sheet = sheet.worksheet("월간신작DB")
    config_sheet = sheet.worksheet("설정")

    existing_records = db_sheet.get_all_records()

    if not existing_records:
        db_sheet.clear()
        db_sheet.append_row(HEADERS)
        existing_records = []

    # 기존 데이터를 딕셔너리 형태로 변환 (키: "게임명_플랫폼")
    db_dict = {}
    for r in existing_records:
        g_name = r.get('게임명', '')
        g_plat = r.get('플랫폼', '')
        if g_name:
            db_dict[f"{g_name}_{g_plat}"] = {
                "출시일": r.get("출시일", ""),
                "게임명": g_name,
                "플랫폼": g_plat,
                "퍼블리셔": r.get("퍼블리셔", ""),
                "출시유형": r.get("출시유형", ""),
                "게임ID": r.get("게임ID", ""),
                "아이콘": r.get("아이콘", ""),
                "한줄설명": r.get("한줄설명", ""),
                "스크린샷": r.get("스크린샷", ""),
            }

    has_changes = False

    # 새로 크롤링한 캘린더 데이터와 비교하여 없으면 추가, 내용이 바뀌었으면 갱신
    for item in scraped_data:
        key = f"{item['게임명']}_{item['플랫폼']}"
        if key not in db_dict:
            db_dict[key] = {**item, "아이콘": "", "한줄설명": "", "스크린샷": ""}
            has_changes = True
        else:
            existing = db_dict[key]
            if (existing.get('출시일') != item['출시일']
                    or existing.get('퍼블리셔') != item['퍼블리셔']
                    or existing.get('출시유형') != item['출시유형']
                    or (item.get('게임ID') and existing.get('게임ID') != item['게임ID'])):
                # 상세정보(아이콘/한줄설명/스크린샷)는 유지한 채 캘린더 필드만 갱신
                existing.update({
                    "출시일": item["출시일"],
                    "퍼블리셔": item["퍼블리셔"],
                    "출시유형": item["출시유형"],
                })
                if item.get("게임ID"):
                    existing["게임ID"] = item["게임ID"]
                has_changes = True
            elif not existing.get("게임ID") and item.get("게임ID"):
                existing["게임ID"] = item["게임ID"]

    # --- 상세정보가 비어있는 게임만 골라 보강 수집 (신규 + 과거 미보강분) ---
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    })

    detail_fetch_count = 0
    for item in db_dict.values():
        game_id = item.get("게임ID")
        needs_detail = not item.get("아이콘") or not item.get("한줄설명")
        if game_id and needs_detail:
            detail = scrape_game_detail(game_id, session)
            if detail:
                item.update(detail)
                has_changes = True
            detail_fetch_count += 1
            time.sleep(0.3)

    print(f"상세정보 신규/보강 수집: {detail_fetch_count}건")

    # 최종 병합된 데이터를 날짜 기준 정렬하여 전체 재기재 (기존 누적 데이터 보존)
    final_rows = [
        [d["출시일"], d["게임명"], d["플랫폼"], d["퍼블리셔"], d["출시유형"],
         d["게임ID"], d["아이콘"], d["한줄설명"], d["스크린샷"]]
        for d in db_dict.values()
    ]
    final_rows.sort(key=lambda x: x[0])

    db_sheet.clear()
    db_sheet.append_row(HEADERS)
    if final_rows:
        db_sheet.append_rows(final_rows)

    is_month_start = (datetime.now().day == 1)

    if existing_records == [] or has_changes or is_month_start:
        config_sheet.update_acell('B1', '발송요청')
        print("트리거 발동: B1 셀을 '발송요청'으로 변경했습니다.")
    else:
        print("트리거 미발동: 변경점이 없습니다.")


# --- 5. 메인 실행부 ---
if __name__ == "__main__":
    now = datetime.now()
    curr_year, curr_month = now.year, now.month

    if curr_month == 12:
        next_year, next_month = curr_year + 1, 1
    else:
        next_year, next_month = curr_year, curr_month + 1

    print(f"{curr_year}년 {curr_month}월 및 {next_year}년 {next_month}월 크롤링 시작...")

    target_months = [(curr_year, curr_month), (next_year, next_month)]
    scraped_data = scrape_calendar_by_url(target_months)

    print(f"총 {len(scraped_data)}건 수집 완료. DB 누적 업데이트 시작...")

    update_db_and_trigger(scraped_data)
    print("모든 작업 완료.")
