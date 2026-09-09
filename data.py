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

# --- 2. 크롤링 함수 (2달치 리스트 처리로 변경됨) ---
def scrape_calendar_by_url(year_months):
    target_dates = []
    # 요청받은 모든 (년, 월)에 대해 날짜 리스트를 생성 후 병합
    for year, month in year_months:
        target_dates.extend(get_dates_in_month(year, month))
        
    monthly_games = []
    
    with sync_playwright() as p:
        # GitHub Actions 환경이므로 headless=True 사용
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

# --- 3. DB 업데이트 및 발송 트리거 함수 ---
def update_db_and_trigger(scraped_data):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # GitHub Actions의 Secret을 환경변수로 불러오기
    creds_json = os.environ.get("GCP_CREDENTIALS")
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        
    client = gspread.authorize(creds)
    
    # 실제 사용하시는 스프레드시트 URL
    sheet_url = "https://docs.google.com/spreadsheets/d/1CW7Xr3eWBUKBPC0DXRDsqqrx2itUlzZfXPVF2hUoMAw/edit?gid=2017461349#gid=2017461349"
    sheet = client.open_by_url(sheet_url)
    
    db_sheet = sheet.worksheet("월간신작DB")
    config_sheet = sheet.worksheet("설정")
    
    existing_records = db_sheet.get_all_records()
    existing_keys = {f"{r.get('게임명', '')}_{r.get('플랫폼', '')}": r for r in existing_records if r.get('게임명')}
    new_keys = {f"{r['게임명']}_{r['플랫폼']}": r for r in scraped_data}
    
    has_changes = False
    if set(existing_keys.keys()) != set(new_keys.keys()):
        has_changes = True
    else:
        for k, v in new_keys.items():
            if existing_keys[k]['출시일'] != v['출시일']:
                has_changes = True
                break
                
    is_month_start = (datetime.now().day == 1)
    
    # 이 부분이 기존 데이터를 삭제하므로 중복 데이터가 쌓이지 않습니다.
    db_sheet.clear()
    db_sheet.append_row(["출시일", "게임명", "플랫폼", "퍼블리셔", "출시유형"])
    if scraped_data:
        db_sheet.append_rows([[d['출시일'], d['게임명'], d['플랫폼'], d['퍼블리셔'], d['출시유형']] for d in scraped_data])
        
    if existing_records == [] or has_changes or is_month_start:
        config_sheet.update_acell('B1', '발송요청')
        print("트리거 발동: B1 셀을 '발송요청'으로 변경했습니다.")
    else:
        print("트리거 미발동: 변경점이 없습니다.")

# --- 4. 메인 실행부 (2달치 로직으로 변경됨) ---
if __name__ == "__main__":
    now = datetime.now()
    
    curr_year, curr_month = now.year, now.month
    
    # 12월인 경우 내년 1월로 넘어가도록 처리
    if curr_month == 12:
        next_year, next_month = curr_year + 1, 1
    else:
        next_year, next_month = curr_year, curr_month + 1
        
    print(f"{curr_year}년 {curr_month}월 및 {next_year}년 {next_month}월 크롤링 시작...")
    
    target_months = [(curr_year, curr_month), (next_year, next_month)]
    scraped_data = scrape_calendar_by_url(target_months)
    
    print(f"총 {len(scraped_data)}건 수집 완료. DB 업데이트 시작...")
    
    update_db_and_trigger(scraped_data)
    print("모든 작업 완료.")
