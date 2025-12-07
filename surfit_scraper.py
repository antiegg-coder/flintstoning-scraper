import time
import json
import os
import pandas as pd
import gspread
from gspread_dataframe import set_with_dataframe, get_as_dataframe
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from datetime import datetime

# ==========================================
# 1. 구글 시트 인증
# ==========================================
json_creds = json.loads(os.environ['GOOGLE_CREDENTIALS'])
scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(json_creds, scopes=scope)
gc = gspread.authorize(creds)

def save_to_sheet(sheet_url, new_data):
    try:
        # gid 추출 및 탭 연결
        if 'gid=' in sheet_url:
            target_gid = int(sheet_url.split('gid=')[1].split('#')[0])
            doc = gc.open_by_url(sheet_url)
            worksheet = next((ws for ws in doc.worksheets() if ws.id == target_gid), None)
        else:
            # gid가 없으면 첫번째 시트
            doc = gc.open_by_url(sheet_url)
            worksheet = doc.get_worksheet(0)
            
        if not worksheet:
            print("[서핏] 탭을 찾을 수 없습니다.")
            return

        # 위치 계산 및 중복 확인
        existing_df = get_as_dataframe(worksheet, header=0)
        existing_data_count = len(existing_df.dropna(how='all'))
        next_row = existing_data_count + 2
        
        try:
            existing_urls = worksheet.col_values(3)[1:]
        except:
            existing_urls = []

        # 중복 제거
        final_data = []
        for item in new_data:
            if item['url'] not in existing_urls:
                final_data.append(item)
        
        if final_data:
            df = pd.DataFrame(final_data)
            # 헤더 없이 데이터만 추가
            set_with_dataframe(worksheet, df, row=next_row, include_column_header=False)
            print(f"[서핏] {len(final_data)}개 저장 완료!")
        else:
            print("[서핏] 새로운 데이터가 없습니다.")
            
    except Exception as e:
        print(f"[서핏] 저장 실패: {e}")

# ==========================================
# 2. 브라우저 설정 & 크롤링
# ==========================================
options = Options()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
today_date = datetime.now().strftime('%Y-%m-%d')

print("▶ 서핏 수집 시작")
driver.get("https://www.surfit.io/explore/marketing/content")
time.sleep(5)
for _ in range(5):
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(1)
    
surfit_data = []
articles = driver.find_elements(By.CSS_SELECTOR, "a")
for article in articles:
    try:
        title = article.text.strip()
        link = article.get_attribute("href")
        if link and title and len(title) > 10 and "로그인" not in title:
            surfit_data.append({
                'title': title, 'subtitle': '', 'url': link, 
                'created_at': today_date, 'company': '', 'status': 'archived', 'publish': ''
            })
    except: continue

# ▼▼▼ [중요] 서핏 시트 주소 입력 ▼▼▼
surfit_url = 'https://docs.google.com/spreadsheets/d/1nKPVCZ6zAOfpqCjV6WfjkzCI55FA9r2yvi9XL3iIneo/edit?gid=2112710663#gid=2112710663'
save_to_sheet(surfit_url, surfit_data)

driver.quit()
