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

# ▼ 방금 주신 'Wanted' 탭의 고유 번호
TARGET_GID = 639559541
SCRAPE_URL = "https://www.wanted.co.kr/wdlist/523/1635?country=kr&job_sort=job.popularity_order&years=-1&locations=all"

def get_google_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    spreadsheet = client.open_by_url(SHEET_URL)
    worksheet = None
    
    # GID로 시트 찾기
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
    
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    chrome_options.add_argument(f"user-agent={user_agent}")
    
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def get_projects():
    driver = get_driver()
    new_data = []
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        print("🌐 원티드(Wanted) 접속 중...")
        driver.get(SCRAPE_URL)
        
        # 화면 로딩 대기
        time.sleep(5)
        
        # 스크롤을 살짝 내려서 데이터를 더 불러옵니다
        driver.execute_script("window.scrollTo(0, 1000);")
        time.sleep(3)
        
        # 모든 링크(a 태그) 수집
        elements = driver.find_elements(By.TAG_NAME, "a")
        print(f"🔍 페이지 내 전체 링크 수: {len(elements)}개")

        for elem in elements:
            try:
                full_url = elem.get_attribute("href")
                
                # 원티드 채용 공고 링크 패턴: /wd/숫자
                if not full_url or "/wd/" not in full_url:
                    continue
                
                # 텍스트 가져오기
                raw_text = elem.text.strip()
                if not raw_text: continue

                # [원티드 제목 정제 로직]
                lines = raw_text.split('\n')
                cleaned_lines = [line.strip() for line in lines if line.strip()]
                
                if not cleaned_lines:
                    continue
                    
                # 첫 번째 줄을 제목으로 사용
                title = cleaned_lines[0]
                
                idx_match = re.search(r'/wd/(\d+)', full_url)
                if len(title) > 2 and idx_match:
                    
                    if not any(d['url'] == full_url for d in new_data):
                        new_data.append({
                            'title': title,
                            'url': full_url,
                            'created_at': today
                        })
            except:
                continue
                
    except Exception as e:
        print(f"❌ 에러 발생: {e}")
    finally:
        driver.quit()
            
    print(f"🎯 수집된 공고: {len(new_data)}개")
    return new_data

def update_sheet(worksheet, data):
    all_values = worksheet.get_all_values()
    
    if not all_values:
        headers = []
    else:
        headers = all_values[0]

    try:
        idx_title = headers.index('title')
        idx_url = headers.index('url')
        idx_created_at = headers.index('created_at')
        idx_status = headers.index('status')
    except ValueError:
        print("⛔ 헤더 오류: 시트 1행에 title, url, created_at, status 가 있어야 합니다.")
        return

    existing_urls = set()
    for row in all_values[1:]:
        if len(row) > idx_url:
            existing_urls.add(row[idx_url])

    rows_to_append = []
    for item in data:
        if item['url'] in existing_urls:
            continue
            
        new_row = [''] * len(headers)
        new_row[idx_title] = item['title']
        new_row[idx_url] = item['url']
        new_row[idx_created_at] = item['created_at']
        new_row[idx_status] = 'archived' # 원티드도 archived 고정
        rows_to_append.append(new_row)

    if rows_to_append:
        worksheet.append_rows(rows_to_append)
        print(f"💾 {len(rows_to_append)}개 저장 완료!")
    else:
        print("ℹ️ 새로운 공고가 없습니다.")

if __name__ == "__main__":
    try:
        sheet = get_google_sheet()
        projects = get_projects()
        update_sheet(sheet, projects)
    except Exception as e:
        print(f"🚨 실행 실패: {e}")
