import time
import re
import os
import json
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

# 셀레니움 관련 (bs4 삭제함)
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 설정
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
    
    # 봇 차단 회피
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    chrome_options.add_argument(f"user-agent={user_agent}")
    
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def get_projects():
    driver = get_driver()
    new_data = []
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        print("🌐 사이트 접속 중...")
        driver.get(SCRAPE_URL)
        
        # 데이터 로딩 대기
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//a[contains(@href, 'idx=')]"))
            )
            print("✅ 로딩 완료")
            time.sleep(2)
        except:
            print("⚠️ 대기 시간 초과")

        # 모든 링크 수집
        elements = driver.find_elements(By.TAG_NAME, "a")
        print(f"🔍 발견된 링크: {len(elements)}개")

        for elem in elements:
            try:
                raw_link = elem.get_attribute("href")
                if not raw_link: continue

                if "idx=" in raw_link and "bmode=view" in raw_link:
                    title = elem.text.strip()
                    if not title: continue 

                    idx_match = re.search(r'idx=(\d+)', raw_link)
                    if idx_match:
                        idx = idx_match.group(1)
                        full_url = f"https://sideproject.co.kr/projects/?bmode=view&idx={idx}"
                        
                        if not any(d['url'] == full_url for d in new_data):
                            new_data.append({
                                'title': title,
                                'url': full_url,
                                'created_at': today
                            })
            except:
                continue
                
    except Exception as e:
        print(f"❌ 에러: {e}")
    finally:
        driver.quit()
            
    print(f"🎯 수집된 공고: {len(new_data)}개")
    return new_data

def update_sheet(worksheet, data):
    # 1. 시트의 모든 값 가져오기
    all_values = worksheet.get_all_values()
    
    # 시트가 비어있으면 헤더가 없는 것
    if not all_values:
        print("⚠️ 시트가 비어있습니다. 헤더가 없습니다.")
        headers = []
        last_row = 1 # 데이터가 하나도 없으면 1행부터라고 가정
    else:
        headers = all_values[0]
        # 실제 데이터가 있는 마지막 줄 찾기 (빈 줄 제외)
        last_row = len(all_values) 
        # 만약 1000줄이 있는데 데이터는 1줄뿐이라면?
        # 구글 시트는 보통 빈 행도 값으로 칠 수 있으므로, 역순으로 검사해서 실제 데이터 위치를 찾습니다.
        for i in range(len(all_values) - 1, 0, -1):
            if any(all_values[i]): # 행에 뭔가 내용이 있으면
                last_row = i + 1   # 그 다음 줄부터 써라
                break
            else:
                last_row = 1 # 헤더만 있고 아래가 다 비었으면 2번째 줄(인덱스 1)부터

    # 헤더 위치 찾기
    try:
        idx_title = headers.index('title')
        idx_url = headers.index('url')
        idx_created_at = headers.index('created_at')
        idx_status = headers.index('status')
    except ValueError:
        print("⛔ 헤더 오류: 1행에 title, url, created_at, status 가 정확히 있어야 합니다.")
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
        # 빈 줄 무시하고 바로 이어 쓰기 위해 append_rows 대신 insert_rows 사용하거나 범위를 지정해야 함
        # 가장 쉬운 방법: append_rows를 쓰되, table_range를 인식하게 함.
        # 하지만 gspread의 append_rows는 기본적으로 '시트의 끝'에 추가함.
        # 시트가 1000줄이면 1001줄에 추가하는 게 기본 동작.
        
        print(f"📝 데이터 쓰기 시작... (총 {len(rows_to_append)}건)")
        worksheet.append_rows(rows_to_append) 
        print(f"💾 저장 완료! (시트 스크롤을 맨 아래 1000행 근처까지 내려보세요)")
    else:
        print("ℹ️ 새로운 공고 없음.")

if __name__ == "__main__":
    try:
        sheet = get_google_sheet()
        projects = get_projects()
        update_sheet(sheet, projects)
    except Exception as e:
        print(f"🚨 실행 실패: {e}")
