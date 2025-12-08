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
TARGET_GID = 981623942  # 시트 탭 GID
# [수정됨] 렛플(Letspl) 검색 결과 URL
SCRAPE_URL = "https://letspl.me/project?location=KR00&type=00&recruitingType=all&jobD=0207&skill=&interest=&keyword="

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
    
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    chrome_options.add_argument(f"user-agent={user_agent}")
    
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def get_projects():
    driver = get_driver()
    new_data = []
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        print("🌐 Letspl 접속 중...")
        driver.get(SCRAPE_URL)
        
        # [수정됨] 스마트 대기: 프로젝트 리스트가 뜰 때까지 최대 15초 대기
        # 렛플은 링크(a) 태그의 href가 '/project/'로 시작함
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href^='/project/']")))
        
        # 약간의 추가 로딩 대기 (이미지/텍스트 렌더링)
        time.sleep(3)
        
        # [수정됨] CSS Selector로 프로젝트 링크만 정확히 타겟팅
        # href 속성이 '/project/'로 시작하는 모든 a 태그 수집
        elements = driver.find_elements(By.CSS_SELECTOR, "a[href^='/project/']")
        print(f"🔍 발견된 프로젝트 링크 수: {len(elements)}개")

        for elem in elements:
            try:
                full_url = elem.get_attribute("href")
                
                # ----------------------------------------------------
                # [필터링 로직]
                # 1. 실제 프로젝트 상세 링크인지 확인 (숫자 ID가 포함되어야 함)
                # 예: https://letspl.me/project/1234/제목 -> OK
                # 예: https://letspl.me/project -> NO (상단 메뉴바 등)
                if not re.search(r'/project/\d+', full_url):
                    continue
                
                # 2. 제목 추출
                # 렛플은 a 태그 안에 텍스트가 여러 개(상태, 인원 등) 섞여 있음.
                # 보통 가장 긴 텍스트나, 줄바꿈으로 나눴을 때 핵심 문구가 제목임.
                raw_text = elem.text.strip()
                if not raw_text:
                    continue

                lines = raw_text.split('\n')
                # 불필요한 태그 텍스트 제거 ('모집중', '프로젝트', '새로운' 등)
                cleaned_lines = [
                    line.strip() for line in lines 
                    if len(line.strip()) > 2  # 너무 짧은 단어 제외
                    and "모집" not in line
                    and "스크랩" not in line
                ]
                
                if cleaned_lines:
                     # 보통 첫 번째나 두 번째 의미 있는 줄이 제목일 확률이 높음
                     # 여기서는 가장 긴 줄을 제목으로 채택 (기존 로직 유지하되 안전장치)
                    title = max(cleaned_lines, key=len)
                else:
                    title = raw_text
                # ----------------------------------------------------

                # 중복 체크 및 데이터 추가
                if len(title) > 2:
                    if not any(d['url'] == full_url for d in new_data):
                        new_data.append({
                            'title': title,
                            'url': full_url,
                            'created_at': today
                        })
            except Exception as e:
                # 개별 요소 에러는 무시하고 계속 진행
                continue
                
    except Exception as e:
        print(f"❌ 에러 발생: {e}")
    finally:
        driver.quit()
            
    print(f"🎯 정제된 게시물: {len(new_data)}개")
    return new_data

def update_sheet(worksheet, data):
    # (이 함수는 기존과 동일하게 유지)
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
        new_row[idx_status] = 'archived'
        
        rows_to_append.append(new_row)

    if rows_to_append:
        worksheet.append_rows(rows_to_append)
        print(f"💾 {len(rows_to_append)}개 저장 완료!")
    else:
        print("ℹ️ 새로운 게시물이 없습니다.")

if __name__ == "__main__":
    try:
        sheet = get_google_sheet()
        projects = get_projects()
        update_sheet(sheet, projects)
    except Exception as e:
        print(f"🚨 실행 실패: {e}")
