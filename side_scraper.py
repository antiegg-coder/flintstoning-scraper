import time
import re
import os
import json
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

# Selenium 관련 라이브러리
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# 1. 설정
SHEET_URL = "https://docs.google.com/spreadsheets/d/1nKPVCZ6zAOfpqCjV6WfjkzCI55FA9r2yvi9XL3iIneo/edit"
TARGET_GID = 1818966683
SCRAPE_URL = "https://sideproject.co.kr/projects"

def get_google_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    spreadsheet = client.open_by_url(SHEET_URL)
    worksheet = None
    for sheet in spreadsheet.worksheets():
        if sheet.id == TARGET_GID:
            worksheet = sheet
            break
    if worksheet is None:
        raise Exception(f"GID가 {TARGET_GID}인 시트를 찾을 수 없습니다.")
    return worksheet

def get_driver():
    # GitHub Actions(리눅스 서버)에서 크롬을 띄우기 위한 필수 옵션들
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # 화면 없이 실행
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def get_projects():
    driver = get_driver()
    new_data = []
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        driver.get(SCRAPE_URL)
        # 페이지 로딩 대기 (안전하게 5초)
        driver.implicitly_wait(5)
        time.sleep(3) 

        # 게시물 링크 요소 찾기 (a 태그 중 class가 post_link인 것)
        articles = driver.find_elements(By.CSS_SELECTOR, "a.post_link")

        for article in articles:
            # 텍스트 추출 (제목)
            title = article.text.strip()
            
            # href 속성 추출 (링크)
            raw_link = article.get_attribute("href")
            
            # idx 추출하여 깔끔한 URL 만들기
            idx_match = re.search(r'idx=(\d+)', raw_link)
            if idx_match:
                idx = idx_match.group(1)
                full_url = f"https://sideproject.co.kr/projects/?bmode=view&idx={idx}"
                
                new_data.append({
                    'title': title,
                    'url': full_url,
                    'created_at': today
                })
                
    except Exception as e:
        print(f"크롤링 중 에러 발생: {e}")
    finally:
        driver.quit() # 브라우저 닫기
            
    return new_data

def update_sheet(worksheet, data):
    all_values = worksheet.get_all_values()
    if not all_values:
        print("시트가 비어있습니다. 헤더를 확인해주세요.")
        return

    headers = all_values[0]
    
    try:
        idx_title = headers.index('title')
        idx_url = headers.index('url')
        idx_created_at = headers.index('created_at')
        idx_status = headers.index('status')
    except ValueError:
        print("오류: 시트 1행에 'title', 'url', 'created_at', 'status' 헤더가 있어야 합니다.")
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
        new_row[idx_status] = 'archived'  # status 자동 입력
        
        rows_to_append.append(new_row)

    if rows_to_append:
        worksheet.append_rows(rows_to_append)
        print(f"총 {len(rows_to_append)}개의 데이터를 저장했습니다.")
    else:
        print("새로운 공고가 없습니다.")

if __name__ == "__main__":
    try:
        sheet = get_google_sheet()
        projects = get_projects()
        update_sheet(sheet, projects)
    except Exception as e:
        print(f"전체 실행 중 에러: {e}")
