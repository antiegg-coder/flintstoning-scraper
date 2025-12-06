import time
import re
import os
import json
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

# 셀레니움 필수 라이브러리
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 1. 설정
SHEET_URL = "https://docs.google.com/spreadsheets/d/1nKPVCZ6zAOfpqCjV6WfjkzCI55FA9r2yvi9XL3iIneo/edit"
TARGET_GID = 1818966683
SCRAPE_URL = "https://sideproject.co.kr/projects"

def get_google_sheet():
    # 구글 시트 인증
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
    chrome_options = Options()
    chrome_options.add_argument("--headless") # 화면 없이 실행
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # [핵심] 403 에러 해결: 봇이 아닌 일반 크롬 브라우저인 척 위장
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    chrome_options.add_argument(f"user-agent={user_agent}")
    
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def get_projects():
    driver = get_driver()
    new_data = []
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        print("🌐 사이트 접속 시도 중...")
        driver.get(SCRAPE_URL)
        
        # [핵심] 빈 화면 방지: 게시물 링크가 뜰 때까지 최대 15초 대기
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'idx=')]"))
            )
            print("✅ 데이터 로딩 확인!")
            time.sleep(2) # 로딩 후 안정화 대기
        except:
            print("⚠️ 로딩 시간 초과 (그래도 수집 시도)")

        # 모든 링크 가져오기
        elements = driver.find_elements(By.TAG_NAME, "a")
        print(f"🔍 발견된 전체 링크 수: {len(elements)}")

        for elem in elements:
            try:
                raw_link = elem.get_attribute("href")
                if not raw_link: continue

                # 링크에 'idx='와 'bmode=view'가 있어야 게시물임
                if "idx=" in raw_link and "bmode=view" in raw_link:
                    title = elem.text.strip()
                    if not title: continue # 제목 없으면 패스

                    # idx 숫자 추출
                    idx_match = re.search(r'idx=(\d+)', raw_link)
                    if idx_match:
                        idx = idx_match.group(1)
                        full_url = f"https://sideproject.co.kr/projects/?bmode=view&idx={idx}"
                        
                        # 중복 방지 (이번 실행에서 수집된 것들 중)
                        if not any(d['url'] == full_url for d in new_data):
                            new_data.append({
                                'title': title,
                                'url': full_url,
                                'created_at': today
                            })
            except:
                continue
                
    except Exception as e:
        print(f"❌ 크롤링 에러: {e}")
    finally:
        driver.quit()
            
    print(f"🎯 수집된 유효 공고 수: {len(new_data)}")
    return new_data

def update_sheet(worksheet, data):
    all_values = worksheet.get_all_values()
    if not all_values: headers = []
    else: headers = all_values[0]

    try:
        idx_title = headers.index('title')
        idx_url = headers.index('url')
        idx_created_at = headers.index('created_at')
        idx_status = headers.index('status')
    except ValueError:
        print("⛔ 시트 헤더 오류: title, url, created_at, status 컬럼이 1행에 있어야 합니다.")
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
        print(f"💾 {len(rows_to_append)}개의 공고 저장 완료!")
    else:
        print("ℹ️ 저장할 새로운 공고가 없습니다 (이미 다 저장됨).")

if __name__ == "__main__":
    try:
        sheet = get_google_sheet()
        projects = get_projects()
        update_sheet(sheet, projects)
    except Exception as e:
        print(f"🚨 실행 실패: {e}")
