import os
import json
import time
from datetime import datetime, timedelta

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

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

# --- 2. 크롤링 함수 (목록 기본 정보 5개 컬럼 수집) ---
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
                page.wait_for_load_state("networkidle", timeout=5000)
            except:
                continue
                
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            
            game_cards = soup.select("a[href*='/game/']")
            seen_links = set()
            
            for card in game_cards:
                try:
                    link_href = card.get('href')
                    if not link_href or link_href in seen_links:
                        continue
                    seen_links.add(link_href)
                    
                    title_elem = card.find("p", class_="line-clamp-2")
                    if not title_elem:
                        title_elem = card.find("p")
                    title = title_elem.text.strip() if title_elem else "N/A"
                    
                    platform_elem = card.find("span", class_=lambda c: c and "text-xs" in c)
                    platform = platform_elem.text.strip() if platform_elem else "N/A"
                    
                    publisher = "N/A"
                    release_type = "N/A"
                    
                    info_rows = card.find_all("div", class_=lambda c: c and "gap" in c)
                    for row in info_rows:
                        text_content = row.text.strip()
                        if "퍼블리셔" in text_content:
                            parts = text_content.split("퍼블리셔")
                            if len(parts) > 1:
                                publisher = parts[-1].strip()
                        if "출시 유형" in text_content:
                            parts = text_content.split("출시 유형")
                            if len(parts) > 1:
                                release_type = parts[-1].strip()

                    monthly_games.append({
                        "출시일": date_str,
                        "게임명": title,
                        "플랫폼": platform,
                        "퍼블리셔": publisher,
                        "출시유형": release_type
                    })
                except Exception as card_err:
                    continue
                    
        browser.close()
    return monthly_games

# --- 3. DB 누적 업데이트 및 발송 트리거 함수 ---
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
    
    # 기존 시트 데이터 읽어오기
    existing_records = db_sheet.get_all_records()
    
    # 헤더가 아예 없거나 비어있는 경우 초기화 후 헤더 생성
    if not existing_records:
        db_sheet.clear()
        db_sheet.append_row(["출시일", "게임명", "플랫폼", "퍼블리셔", "출시유형"])
        existing_records = []

    # 기존 데이터를 딕셔너리 형태로 관리 (키: "게임명_플랫폼")
    db_dict = {}
    for r in existing_records:
        g_name = r.get('게임명', '')
        g_plat = r.get('플랫폼', '')
        if g_name:
            db_dict[f"{g_name}_{g_plat}"] = r

    has_changes = False
    
    # 새로 수집된 데이터 병합 (기존에 없으면 추가, 있으면 출시일 등 변경여부 확인)
    for item in scraped_data:
        key = f"{item['게임명']}_{item['플랫폼']}"
        if key not in db_dict:
            # 신규 데이터 추가
            db_dict[key] = item
            has_changes = True
        else:
            # 기존 데이터가 있으나 출시일 등이 변경된 경우 업데이트
            if db_dict[key].get('출시일') != item['출시일'] or db_dict[key].get('퍼블리셔') != item['퍼블리셔']:
                db_dict[key] = item
                has_changes = True

    # 최종 병합된 데이터를 시트에 다시 기록 (전체 클리어 후 5개 컬럼 기준으로 재적재하여 데이터 누적 유지)
    final_rows = [[d['출시일'], d['게임명'], d['플랫폼'], d['퍼블리셔'], d['출시유형']] for d in db_dict.values()]
    
    # 날짜 기준 또는 게임명 기준으로 정렬하여 시트 가독성 높이기 (선택 사항)
    final_rows.sort(key=lambda x: x[0])

    db_sheet.clear()
    db_sheet.append_row(["출시일", "게임명", "플랫폼", "퍼블리셔", "출시유형"])
    if final_rows:
        db_sheet.append_rows(final_rows)
        
    is_month_start = (datetime.now().day == 1)
    
    if has_changes or is_month_start:
        config_sheet.update_acell('B1', '발송요청')
        print("트리거 발동: B1 셀을 '발송요청'으로 변경했습니다.")
    else:
        print("트리거 미발동: 변경점이 없습니다.")

# --- 4. 메인 실행부 ---
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
