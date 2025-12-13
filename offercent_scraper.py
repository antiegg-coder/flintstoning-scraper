import time
import re
import os
import json
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

# 셀레니움
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 1. 설정
SHEET_URL = "https://docs.google.com/spreadsheets/d/1nKPVCZ6zAOfpqCjV6WfjkzCI55FA9r2yvi9XL3iIneo/edit"
TARGET_GID = 639559541
SCRAPE_URL = "https://offercent.co.kr/company-list?jobCategories=0040002%2C0170004"

def get_google_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    spreadsheet = client.open_by_url(SHEET_URL)
    worksheet = None
    for sheet in spreadsheet.worksheets():
        if str(sheet.id) == str(TARGET_GID):
            worksheet = sheet
            break
    if worksheet is None:
        raise Exception(f"GID가 {TARGET_GID}인 시트를 찾을 수 없습니다.")
    return worksheet

def get_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # 봇 탐지 우회 설정
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    chrome_options.add_argument(f"user-agent={user_agent}")
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    return driver

def get_projects():
    driver = get_driver()
    new_data = []
    today = datetime.now().strftime("%Y-%m-%d")
    collected_urls = set()

    try:
        print("🌐 오퍼센트 접속 중...")
        driver.get(SCRAPE_URL)
        
        # [수정] 단순 body 대기가 아니라, 실제 'a' 태그가 뜰 때까지 기다림 (최대 30초)
        wait = WebDriverWait(driver, 30)
        try:
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "a")))
            print("✅ 페이지 로딩 감지됨 (링크 요소 확인)")
        except:
            print("⚠️ 경고: 30초 동안 링크(a 태그)가 하나도 발견되지 않았습니다.")
            print("   -> 페이지가 차단되었거나, 로딩이 매우 느립니다.")
            print(f"   -> 현재 URL: {driver.current_url}")
            print(f"   -> 페이지 소스 일부: {driver.page_source[:500]}") # 차단 메시지 확인용

        # 로딩 후 안전하게 조금 더 대기
        time.sleep(5) 

        # ---------------------------------------------------------
        # 수집 함수
        # ---------------------------------------------------------
        def scrape_current_view(debug_mode=False):
            elements = driver.find_elements(By.TAG_NAME, "a")
            count = 0
            
            # 디버깅: 찾은 요소가 0개면 로그 출력
            if len(elements) == 0 and debug_mode:
                print("   ⚠️ 현재 화면에서 'a' 태그를 하나도 못 찾았습니다.")

            BAD_KEYWORDS = ["채용 중인 공고", "채용마감", "마감임박", "상시채용", "NEW", "D-"]

            for elem in elements:
                try:
                    full_url = elem.get_attribute("href")
                    if not full_url or full_url == SCRAPE_URL or full_url in collected_urls: 
                        continue
                    
                    raw_text = elem.text.strip()
                    if not raw_text: continue

                    lines = raw_text.split('\n')
                    cleaned_lines = []
                    
                    for line in lines:
                        text = line.strip()
                        if not text: continue
                        
                        is_bad = False
                        for bad in BAD_KEYWORDS:
                            if bad in text:
                                is_bad = True
                                break
                        if not is_bad:
                            cleaned_lines.append(text)

                    if len(cleaned_lines) < 2: continue

                    company = cleaned_lines[0]
                    title = cleaned_lines[1]

                    # 제목 보정 로직
                    if len(title) <= 3 and len(cleaned_lines) > 2:
                        title = cleaned_lines[2]

                    if len(title) > 1 and len(company) > 1:
                        new_data.append({
                            'title': title,
                            'company': company,
                            'url': full_url,
                            'scraped_at': today
                        })
                        collected_urls.add(full_url)
                        count += 1
                except:
                    continue
            return count
        # ---------------------------------------------------------

        print("⬇️ 스크롤과 동시에 수집 시작...")
        
        # [1] 첫 화면 수집 (디버그 모드 켜기)
        first_count = scrape_current_view(debug_mode=True)
        print(f"   🚀 첫 화면 수집 결과: {first_count}개")

        last_height = driver.execute_script("return document.body.scrollHeight")
        scroll_count = 0
        
        while True:
            # 스크롤 다운
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3) # 로딩 대기
            
            # 스크롤 후 수집
            found = scrape_current_view()
            
            new_height = driver.execute_script("return document.body.scrollHeight")
            scroll_count += 1
            
            # 로그를 너무 많이 출력하지 않도록 3번마다, 혹은 수집되었을 때만 출력
            if found > 0 or scroll_count % 3 == 0:
                print(f"   ...스크롤 {scroll_count}회 (이번 턴 {found}개 추가 / 누적 {len(new_data)}개)")

            if new_height == last_height:
                # 마지막 확인 사살
                scrape_current_view()
                print("🏁 페이지 끝 도달")
                break
                
            last_height = new_height
            
            # [안전장치] 무한루프 방지 (최대 50번 스크롤)
            if scroll_count > 50:
                print("⚠️ 너무 많이 스크롤되어 강제 종료합니다.")
                break
                
    except Exception as e:
        print(f"❌ 크롤링 에러: {e}")
    finally:
        driver.quit()
            
    print(f"🎯 최종 수집된 공고: {len(new_data)}개")
    
    if len(new_data) > 0:
        print("📊 [샘플 데이터]")
        for i in range(min(3, len(new_data))):
             print(f"   제목: {new_data[i]['title']} / 회사: {new_data[i]['company']}")
    else:
        print("⛔ 수집된 데이터가 0개입니다. 로그를 확인해주세요.")

    return new_data

def update_sheet(worksheet, data):
    all_values = worksheet.get_all_values()
    
    if not all_values:
        headers = ['title', 'company', 'url', 'scraped_at', 'status']
        worksheet.append_row(headers)
        all_values = [headers]
    
    headers = all_values[0]
    try:
        idx_title = headers.index('title')
        idx_company = headers.index('company')
        idx_url = headers.index('url')
        idx_scraped_at = headers.index('scraped_at')
        idx_status = headers.index('status')
    except:
        print("⛔ 헤더 오류")
        return

    existing_urls = set()
    if len(all_values) > 1:
        for row in all_values[1:]:
            if len(row) > idx_url:
                existing_urls.add(row[idx_url])

    rows_to_append = []
    empty_row = [''] * len(headers)

    for item in data:
        if item['url'] in existing_urls:
            continue
        new_row = empty_row.copy()
        new_row[idx_title] = item['title']
        new_row[idx_company] = item['company']
        new_row[idx_url] = item['url']
        new_row[idx_scraped_at] = item['scraped_at']
        new_row[idx_status] = 'archived'
        rows_to_append.append(new_row)

    if rows_to_append:
        worksheet.append_rows(rows_to_append)
        print(f"💾 {len(rows_to_append)}개 저장 완료!")
    else:
        print("ℹ️ 저장할 새로운 공고가 없습니다.")

if __name__ == "__main__":
    try:
        sheet = get_google_sheet()
        projects = get_projects()
        update_sheet(sheet, projects)
    except Exception as e:
        print(f"🚨 실패: {e}")
