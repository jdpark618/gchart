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

# --- 2. 크롤링 함수 (원복 소스 기준) ---
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
                                
                    monthly_games.append({
                        "출시일": date_str,
                        "게임명": title,
                        "플랫폼": platform,
                        "퍼블리셔": publisher,
                        "출시유형": release_type
                    })
                except:
                    continue
                    
        browser.close()
    return monthly_games

# --- 3. DB 누적 업데이트 및 발송 트리거 함수 (Upsert 적용) ---
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
    
    # 시트가 아예 비어있으면 헤더 추가
    if not existing_records:
        db_sheet.clear()
        db_sheet.append_row(["출시일", "게임명", "플랫폼", "퍼블리셔", "출시유형"])
        existing_records = []

    # 기존 데이터를 딕셔너리 형태로 변환 (키: "게임명_플랫폼")
    db_dict = {}
    for r in existing_records:
        g_name = r.get('게임명', '')
        g_plat = r.get('플랫폼', '')
        if g_name:
            db_dict[f"{g_name}_{g_plat}"] = r

    has_changes = False
    
    # 새로 크롤링한 데이터와 비교하여 없으면 추가, 내용이 바뀌었으면 갱신
    for item in scraped_data:
        key = f"{item['게임명']}_{item['플랫폼']}"
        if key not in db_dict:
            db_dict[key] = item
            has_changes = True
        else:
            if db_dict[key].get('출시일') != item['출시일'] or db_dict[key].get('퍼블리셔') != item['퍼블리셔'] or db_dict[key].get('출시유형') != item['출시유형']:
                db_dict[key] = item
                has_changes = True

    # 최종 병합된 데이터를 5개 컬럼 기준으로 정렬하여 전체 재기재 (기존 누적 데이터 보존)
    final_rows = [[d['출시일'], d['게임명'], d['플랫폼'], d['퍼블리셔'], d['출시유형']] for d in db_dict.values()]
    final_rows.sort(key=lambda x: x[0])

    db_sheet.clear()
    db_sheet.append_row(["출시일", "게임명", "플랫폼", "퍼블리셔", "출시유형"])
    if final_rows:
        db_sheet.append_rows(final_rows)
        
    is_month_start = (datetime.now().day == 1)
    
    if existing_records == [] or has_changes or is_month_start:
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
