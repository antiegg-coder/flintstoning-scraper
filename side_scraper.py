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

# 1. 설정
SHEET_URL = "https://docs.google.com/spreadsheets/d/1nKPVCZ6zAOfpqCjV6WfjkzCI55FA9r2yvi9XL3iIneo/edit"
TARGET_GID = 1818966683
SCRAPE_URL = "https://sideproject.co.kr/projects"

def get_google_sheet():
    # 깃허브 Secret 키로 구글 시트 접속
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    spreadsheet = client.open_by_url(SHEET_URL)
    worksheet = None
    
    # GID로 시트 찾기
    for sheet in spreadsheet.worksheets():
        if sheet.id == TARGET_GID:
            worksheet = sheet
            break
            
    if worksheet is None:
        raise Exception(f"GID가 {TARGET_GID}인 시트를 찾을 수 없습니다.")
    return worksheet

def get_driver():
    # 깃허브 서버에서 화면 없이(Headless) 크롬 실행
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def get_projects():
    driver = get_driver()
    new_data = []
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        driver.get(SCRAPE_URL)
        time.sleep(3) # 페이지 로딩 대기

        # 게시물 링크(a 태그) 찾기
        articles = driver.find_elements(By.CSS_SELECTOR, "a.post_link")

        for article in articles:
            # 제목
            title = article.text.strip()
            # 링크
            raw_link = article.get_attribute("href")
            
            # idx 추출
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
        print(f"크롤링 중 에러: {e}")
    finally:
        driver.quit()
            
    return new_data

def update_sheet(worksheet, data):
    all_values = worksheet.get_all_values()
    if not all_values:
        print("시트가 비어있습니다.")
        return

    headers = all_values[0]
    
    # 1행 헤더 위치 자동 찾기
    try:
        idx_title = headers.index('title')
        idx_url = headers.index('url')
        idx_created_at = headers.index('created_at')
        idx_status = headers.index('status')
    except ValueError:
        print("오류: 1행에 title, url, created_at, status 헤더가 정확히 있어야 합니다.")
        return

    # 중복 방지용 기존 URL 확인
    existing_urls = set()
    for row in all_values[1:]:
        if len(row) > idx_url:
            existing_urls.add(row[idx_url])

    rows_to_append = []
    for item in data:
        if item['url'] in existing_urls:
            continue
            
        # 빈 행 만들기
        new_row = [''] * len(headers)
        
        # 데이터 채우기
        new_row[idx_title] = item['title']
        new_row[idx_url] = item['url']
        new_row[idx_created_at] = item['created_at']
        new_row[idx_status] = 'archived'  # 요청하신 대로 archived 자동 입력
        
        rows_to_append.append(new_row)

    if rows_to_append:
        worksheet.append_rows(rows_to_append)
        print(f"{len(rows_to_append)}개 추가 완료.")
    else:
        print("새로운 공고가 없습니다.")

if __name__ == "__main__":
    try:
        sheet = get_google_sheet()
        projects = get_projects()
        update_sheet(sheet, projects)
    except Exception as e:
        print(f"실행 에러: {e}")
