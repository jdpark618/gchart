# --- 2. 크롤링 함수 (스크린샷 복수 개 및 상세 메타데이터 수집 확장) ---
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

                    # 썸네일 아이콘 추출
                    icon_url = ""
                    img_elem = card.find("img")
                    if img_elem and img_elem.get('src'):
                        icon_url = img_elem['src']

                    # 상세 페이지 링크 추출 후 이동하여 상세 정보 및 스크린샷 긁어오기
                    description = ""
                    screenshots = []
                    
                    link_elem = card.find("a", href=True)
                    if link_elem:
                        detail_url = "https://www.wame.is" + link_elem['href'] if link_elem['href'].startswith('/') else link_elem['href']
                        
                        # 새 탭이나 같은 페이지에서 상세 페이지로 이동
                        detail_page = browser.new_page()
                        try:
                            detail_page.goto(detail_url, timeout=5000)
                            detail_page.wait_for_load_selector("img", timeout=3000)
                            detail_html = detail_page.content()
                            detail_soup = BeautifulSoup(detail_html, "html.parser")
                            
                            # 상세 설명 추출 (사이트 구조에 맞춘 셀렉터 확인 필요)
                            desc_elem = detail_soup.find("p", class_=lambda c: c and "text-" in c) # 예시용 셀렉터
                            if desc_elem:
                                description = desc_elem.text.strip()

                            # 스크린샷 영역의 모든 이미지 수집
                            # (보통 스크린샷 영역은 특정 클래스나 컨테이너 내부에 있으므로 원하시는 영역 지정 가능)
                            shot_imgs = detail_soup.find_all("img") 
                            for img in shot_imgs:
                                src = img.get('src')
                                if src and src.startswith('http') and src != icon_url:
                                    screenshots.append(src)
                        except:
                            pass
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
                        "스크린샷목록": ",".join(screenshots) # 콤마로 결합하여 저장
                    })
                except:
                    continue
                    
        browser.close()
    return monthly_games
