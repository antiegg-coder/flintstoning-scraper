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

# ▼ 시트 GID (확인 필수)
TARGET_GID = 639559541
# [변경됨] 오퍼센트 URL
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
    
    # 봇 탐지 우회
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

    try:
        print("🌐 오퍼센트 접속 중...")
        driver.get(SCRAPE_URL)
        
        wait = WebDriverWait(driver, 20)
        # 페이지 본문 로딩 대기
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(5) # 리스트 렌더링 대기

        # 스크롤 다운
        for i in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
        
        # [수정] 오퍼센트의 채용 공고 카드는 보통 a 태그로 감싸져 있습니다.
        elements = driver.find_elements(By.TAG_NAME, "a")
        print(f"🔍 탐색된 링크 수: {len(elements)}개")

        for elem in elements:
            try:
                full_url = elem.get_attribute("href")
                if not full_url or full_url == SCRAPE_URL: continue
                
                # 텍스트 가져오기
                raw_text = elem.text.strip()
                if not raw_text: continue

                # 줄바꿈 기준으로 텍스트 분리
                lines = raw_text.split('\n')
                cleaned_lines = [line.strip() for line in lines if line.strip()]
                
                # 데이터가 너무 적으면 스킵 (최소 회사명, 제목은 있어야 함)
                if len(cleaned_lines) < 2: continue

                # [중요] 오퍼센트 구조에 맞춘 파싱 로직
                # 보통 순서: 1.회사명 2.카테고리/빈칸 3.제목 OR 1.회사명 2.제목
                # 예: ['파마리서치', '[경력] 이커머스 콘텐츠 마케팅', 'D-10']
                
                company = cleaned_lines[0] # 첫 번째 줄을 회사명으로 가정
                title = ""

                # 두 번째 줄부터 제목 찾기 (보통 두 번째 줄이 제목)
                if len(cleaned_lines) >= 2:
                    title = cleaned_lines[1]
                
                # 제목 검증: 만약 2번째 줄이 카테고리(예: '마케팅')고 3번째 줄이 진짜 제목일 경우 대비
                # 제목이 너무 짧거나(4글자 이하) 특정 단어면 다음 줄을 제목으로 봅니다.
                if len(title) < 4 and len(cleaned_lines) >= 3:
                     title = cleaned_lines[2]

                # 제목에 대괄호 [ ] 가 포함되어 있다면 제목일 확률이 높음 (예: [경력])
                # 혹은 회사명이 너무 길면(공고 제목이 첫 줄에 왔을 가능성) 스왑 로직 추가 가능하나,
                # 현재는 "파마리서치"가 먼저 나오는 패턴을 우선합니다.

                # 필터링: 마감일, D-Day, 지역명 등이 제목으로 들어가는 것을 방지
                if title.startswith("D-") or "마감" in title or title.endswith("구"):
                     if len(cleaned_lines) >= 3:
                         title = cleaned_lines[2]

                # 결과가 유효한지 확인 후 추가
                if len(title) > 2 and len(company) > 1:
                    # 중복 URL 체크
                    if not any(d['url'] == full_url for d in new_data):
                        # 디버깅용 출력 (로그에서 확인 가능)
                        # print(f"  -> 추출: {company} / {title}")
                        
                        new_data.append({
                            'title': title,
                            'company': company,
                            'url': full_url,
                            'scraped_at': today
                        })
            except Exception:
                continue
                
    except Exception as e:
        print(f"❌ 크롤링 에러: {e}")
    finally:
        driver.quit()
            
    print(f"🎯 수집된 공고: {len(new_data)}개")
    # 샘플 데이터 3개만 출력해서 확인
    if len(new_data) > 0:
        print("📊 [샘플 데이터 확인]")
        for i in range(min(3, len(new_data))):
            print(f"   제목: {new_data[i]['title']} | 회사: {new_data[i]['company']}")

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
        print(f"💾 {len(rows_to_append)}개 저장 완료")
    else:
        print("ℹ️ 신규 공고 없음")

if __name__ == "__main__":
    try:
        sheet = get_google_sheet()
        projects = get_projects()
        update_sheet(sheet, projects)
    except Exception as e:
        print(f"🚨 실패: {e}")
