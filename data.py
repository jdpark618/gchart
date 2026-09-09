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

# --- 2. 크롤링 함수 (목록 + 상세 페이지 메타데이터 정밀 확장) ---
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

                    icon_url = ""
                    img_elem = card.find("img")
                    if img_elem and img_elem.get('src'):
                        icon_url = img_elem['src']

                    description = ""
                    screenshots = []
                    
                    detail_url = "https://www.wame.is" + link_href if link_href.startswith('/') else link_href
                    
                    detail_page = browser.new_page()
                    try:
                        detail_page.goto(detail_url, timeout=6000)
                        detail_page.wait_for_load_state("networkidle", timeout=4000)
                        detail_html = detail_page.content()
                        detail_soup = BeautifulSoup(detail_html, "html.parser")
                        
                        # 1. 아이콘 URL (object-fill 클래스 및 고해상도 srcset 우선 추출)
                        icon_elem = detail_soup.find("img", class_="object-fill")
                        if icon_elem:
                            srcset = icon_elem.get('srcset')
                            if srcset:
                                sources = [s.strip().split(' ')[0] for s in srcset.split(',')]
                                icon_url = sources[-1] if sources else icon_elem.get('src', '')
                            else:
                                icon_url = icon_elem.get('src', '')

                        # 2. 상세 설명 텍스트 파싱
                        desc_elem = detail_soup.find("p", class_=lambda c: c and "text-" in c and not "line-clamp" in c)
                        if desc_elem:
                            description = desc_elem.text.strip()

                        # 3. 스크린샷 URL ("스크린샷" 타이틀 하단 슬라이더 컨테이너 추출)
                        screenshot_heading = detail_soup.find(lambda tag: tag.name == "h2" and "스크린샷" in tag.text)
                        if screenshot_heading:
                            parent_section = screenshot_heading.find_parent("div")
                            if parent_section:
                                shot_cards = parent_section.find_all("div", class_=lambda c: c and "shrink-0" in c)
                                for card_elem in shot_cards:
                                    shot_img = card_elem.find("img")
                                    if shot_img:
                                        srcset = shot_img.get('srcset')
                                        if srcset:
                                            sources = [s.strip().split(' ')[0] for s in srcset.split(',')]
                                            img_url = sources[-1] if sources else shot_img.get('src', '')
                                        else:
                                            img_url = shot_img.get('src', '')
                                        
                                        if img_url and img_url not in screenshots:
                                            screenshots.append(img_url)
                                            
                    except Exception as e:
                        print(f"상세 페이지 수집 중 오류 ({detail_url}): {e}")
                    finally:
                        detail_page.close()

                    monthly_games.append({
                        "출시일": date_str,
                        "게임명": title,
                        "플랫폼": platform,
                        "퍼블리셔": publisher,
                        "출시유형": release_type,
                        "아이콘url": icon_url,
                        "설명": description,
                        "스크린샷url": ",".join(screenshots)
                    })
                except Exception as card_err:
                    continue
                    
        browser.close()
    return monthly_games

# --- 3. DB 업데이트 및 발송 트리거 함수 ---
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
    existing_keys = {f"{r.get('게임명', '')}_{r.get('플랫폼', '')}": r for r in existing_records if r.get('게임명')}
    new_keys = {f"{r['게임명']}_{r['플랫폼']}": r for r in scraped_data}
    
    has_changes = False
    if set(existing_keys.keys()) != set(new_keys.keys()):
        has_changes = True
    else:
        for k, v in new_keys.items():
            if existing_keys.get(k, {}).get('출시일') != v['출시일']:
                has_changes = True
                break
                
    is_month_start = (datetime.now().day == 1)
    
    db_sheet.clear()
    db_sheet.append_row(["출시일", "게임명", "플랫폼", "퍼블리셔", "출시유형", "아이콘 url", "설명", "스크린샷 url"])
    if scraped_data:
        db_sheet.append_rows([[
            d['출시일'], d['게임명'], d['플랫폼'], d['퍼블리셔'], d['출시유형'],
            d['아이콘url'], d['설명'], d['스크린샷url']
        ] for d in scraped_data])
        
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
    
    print(f"총 {len(scraped_data)}건 수집 완료. DB 업데이트 시작...")
    
    update_db_and_trigger(scraped_data)
    print("모든 작업 완료.")
