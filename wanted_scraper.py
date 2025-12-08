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
# from selenium.webdriver.support.ui import WebDriverWait # 현재 미사용이라 주석처리
# from selenium.webdriver.support import expected_conditions as EC # 현재 미사용이라 주석처리

# 1. 설정
SHEET_URL = "https://docs.google.com/spreadsheets/d/1nKPVCZ6zAOfpqCjV6WfjkzCI55FA9r2yvi9XL3iIneo/edit"

# ▼ 원티드 탭 GID (확인 필수)
TARGET_GID = 639559541
SCRAPE_URL = "https://www.wanted.co.kr/wdlist/523/1635?country=kr&job_sort=job.popularity_order&years=-1&locations=all"

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
    # options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")
    
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

                # [수정된 제목/회사명 추출 로직]
                lines = raw_text.split('\n')
                
                # 필터링: 보상금, 응답률 등 불필요한 정보가 섞인 줄 제거
                cleaned_lines = []
                for line in lines:
                    text = line.strip()
                    if not text: continue
                    
                    # 1. '합격보상금'이나 '만원' 같은 돈 관련 단어 제거
                    if "합격보상금" in text or "보상금" in text:
                        continue
                    # 2. 숫자로만 되어있거나 '원'으로 끝나는 금액 제거 (예: 1,000,000원)
                    if text.endswith("원") and any(c.isdigit() for c in text):
                        continue
                    # 3. '응답률 높음' 같은 뱃지 제거
                    if "응답률" in text or "입사축하금" in text or "지역" in text: # 지역 정보도 필터링에 추가
                        continue
                        
                    cleaned_lines.append(text)
                
                if not cleaned_lines:
                    continue
                    
                # ---- [변경점 1] 회사명 추출 로직 추가 ----
                # 필터링 후 남은 줄 중 첫 번째가 제목, 두 번째가 회사명이라고 가정
                title = cleaned_lines[0]
                company = "" # 기본값 비워둠

                # 정제된 줄이 2줄 이상 남아있다면, 두 번째 줄을 회사명으로 간주
                if len(cleaned_lines) >= 2:
                    company = cleaned_lines[1]
                # -----------------------------------------
                
                idx_match = re.search(r'/wd/(\d+)', full_url)
                # 제목이 너무 짧으면 이상한 데이터일 수 있음 (길이 조건 약간 완화)
                if len(title) > 1 and idx_match:
                    
                    if not any(d['url'] == full_url for d in new_data):
                        new_data.append({
                            'title': title,
                            'company': company, # 데이터에 회사명 추가
                            'url': full_url,
                            'scraped_at': today
                        })
            except Exception as e_inner:
                 # 개별 요소 처리 중 에러는 무시하고 다음 요소로 진행
                 # print(f"개별 요소 처리 에러: {e_inner}")
                 continue
                
    except Exception as e:
        print(f"❌ 크롤링 중 에러 발생: {e}")
    finally:
        driver.quit()
            
    print(f"🎯 수집된 공고: {len(new_data)}개")
    # 확인을 위해 상위 3개만 출력해봄
    # for d in new_data[:3]:
    #     print(f"- [{d['company']}] {d['title']}")
        
    return new_data

def update_sheet(worksheet, data):
    all_values = worksheet.get_all_values()
    
    if not all_values:
        # 시트가 비어있으면 헤더부터 만듭니다.
        headers = ['title', 'company', 'url', 'scraped_at', 'status']
        worksheet.append_row(headers)
        all_values = [headers] # 아래 로직을 위해 추가
        print("ℹ️ 빈 시트 감지: 헤더 행을 새로 만들었습니다.")
    else:
        headers = all_values[0]

    try:
        # ---- [변경점 2] company 인덱스 찾기 추가 ----
        idx_title = headers.index('title')
        idx_company = headers.index('company') # 필수! 시트에 이 컬럼이 있어야 함
        idx_url = headers.index('url')
        idx_scraped_at = headers.index('scraped_at')
        idx_status = headers.index('status')
        # -------------------------------------------
    except ValueError as e:
        missing_col = str(e).split("'")[1]
        print(f"⛔ 헤더 오류: 시트 1행에 '{missing_col}' 컬럼이 없습니다.")
        print("title, company, url, scraped_at, status 헤더가 모두 존재하는지 확인해주세요.")
        return

    existing_urls = set()
    # 데이터가 있는 2행부터 URL 수집
    if len(all_values) > 1:
        for row in all_values[1:]:
            # 행의 길이가 idx_url보다 짧을 경우 대비
            if len(row) > idx_url:
                existing_urls.add(row[idx_url])

    rows_to_append = []
    # 구글 시트 API 요구사항에 맞춰 빈 셀로 채워진 기본 행 생성
    empty_row_structure = [''] * len(headers)

    for item in data:
        if item['url'] in existing_urls:
            continue
            
        new_row = empty_row_structure.copy()
        new_row[idx_title] = item['title']
        new_row[idx_company] = item['company'] # 회사명 매핑
        new_row[idx_url] = item['url']
        new_row[idx_scraped_at] = item['scraped_at']
        new_row[idx_status] = 'archived'
        rows_to_append.append(new_row)

    if rows_to_append:
        # 한 번에 여러 행 추가 (API 호출 최소화)
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
