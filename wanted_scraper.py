import time
import re
import os
import json
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

# 셀레니움 관련
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 1. 설정
SHEET_URL = "https://docs.google.com/spreadsheets/d/1nKPVCZ6zAOfpqCjV6WfjkzCI55FA9r2yvi9XL3iIneo/edit"

# ▼ 원티드 탭 GID (확인 필수 - 오퍼센트용으로 시트를 새로 판다면 변경 필요)
TARGET_GID = 639559541 

# [변경됨] 스크래핑 대상 URL: 오퍼센트
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
    
    print(f"📂 연결된 시트: {worksheet.title}")
    return worksheet

def get_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # [중요] 봇 탐지 우회 설정
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    chrome_options.add_argument(f"user-agent={user_agent}")
    
    driver = webdriver.Chrome(options=chrome_options)
    
    # navigator.webdriver 속성을 숨김
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    return driver

def get_projects():
    driver = get_driver()
    new_data = []
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        print("🌐 오퍼센트(Offercent) 접속 중...")
        driver.get(SCRAPE_URL)
        
        wait = WebDriverWait(driver, 20)
        
        try:
            # [변경됨] 특정 ul 태그 대신 body 로딩 대기 (사이트 구조가 다를 수 있으므로)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            print(f"✅ 페이지 타이틀: {driver.title}")
            
            # 리스트가 렌더링될 시간을 조금 더 줍니다
            time.sleep(3) 
        except:
            print("⚠️ 페이지 로딩 시간 초과 또는 차단됨")
            print(f"현재 URL: {driver.current_url}")

        # 스크롤 내려서 데이터 확보
        for i in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
        
        # [변경됨] 링크 요소 탐색
        elements = driver.find_elements(By.TAG_NAME, "a")
        print(f"🔍 페이지 내 전체 링크 수: {len(elements)}개")

        for elem in elements:
            try:
                full_url = elem.get_attribute("href")
                
                # [변경됨] URL 필터링 로직 수정 (원티드 /wd/ 제거)
                # 오퍼센트 도메인이 포함되어 있거나, 상세 페이지로 추정되는 링크만 수집
                if not full_url: continue
                
                # 네비게이션, 로그인 등 불필요한 링크 제외 (단순화된 로직)
                if "login" in full_url or "signup" in full_url: continue
                if full_url == SCRAPE_URL: continue # 자기 자신 제외
                
                raw_text = elem.text.strip()
                if not raw_text: continue

                lines = raw_text.split('\n')
                cleaned_lines = []
                for line in lines:
                    text = line.strip()
                    if not text: continue
                    # [변경됨] 원티드 전용 제외 키워드(합격보상금 등) 제거
                    cleaned_lines.append(text)
                
                if not cleaned_lines: continue
                    
                # [로직 유지] 보통 첫 줄이 제목, 두 번째 줄이 회사명인 경우가 많음
                # 사이트 구조에 따라 이 부분은 조정이 필요할 수 있습니다.
                title = cleaned_lines[0]
                company = ""
                if len(cleaned_lines) >= 2:
                    company = cleaned_lines[1]
                else:
                    # 텍스트가 한 줄뿐이라면 회사명으로 간주하거나 제목으로 처리
                    pass 
                
                # 제목이 너무 짧거나(메뉴명 등), 의미 없는 데이터 필터링
                if len(title) > 2:
                    # 중복 방지 체크
                    if not any(d['url'] == full_url for d in new_data):
                        new_data.append({
                            'title': title,
                            'company': company,
                            'url': full_url,
                            'scraped_at': today
                        })
            except:
                 continue
                
    except Exception as e:
        print(f"❌ 크롤링 중 에러 발생: {e}")
    finally:
        driver.quit()
            
    print(f"🎯 수집된 공고(후보): {len(new_data)}개")
    return new_data

def update_sheet(worksheet, data):
    all_values = worksheet.get_all_values()
    
    if not all_values:
        headers = ['title', 'company', 'url', 'scraped_at', 'status']
        worksheet.append_row(headers)
        all_values = [headers]
        print("ℹ️ 빈 시트 감지: 헤더 행을 새로 만들었습니다.")
    else:
        headers = all_values[0]

    try:
        idx_title = headers.index('title')
        idx_company = headers.index('company')
        idx_url = headers.index('url')
        idx_scraped_at = headers.index('scraped_at')
        idx_status = headers.index('status')
    except ValueError as e:
        missing_col = str(e).split("'")[1]
        print(f"⛔ 헤더 오류: 시트 1행에 '{missing_col}' 컬럼이 없습니다.")
        return

    existing_urls = set()
    if len(all_values) > 1:
        for row in all_values[1:]:
            if len(row) > idx_url:
                existing_urls.add(row[idx_url])

    rows_to_append = []
    empty_row_structure = [''] * len(headers)

    for item in data:
        if item['url'] in existing_urls:
            continue
            
        new_row = empty_row_structure.copy()
        new_row[idx_title] = item['title']
        new_row[idx_company] = item['company']
        new_row[idx_url] = item['url']
        new_row[idx_scraped_at] = item['scraped_at']
        new_row[idx_status] = 'archived'
        rows_to_append.append(new_row)

    if rows_to_append:
        worksheet.append_rows(rows_to_append)
        print(f"💾 {len(rows_to_append)}개 신규 공고 저장 완료!")
    else:
        print("ℹ️ 저장할 새로운 공고가 없습니다.")

if __name__ == "__main__":
    try:
        sheet = get_google_sheet()
        projects = get_projects()
        update_sheet(sheet, projects)
    except Exception as e:
        print(f"🚨 실행 실패: {e}")
