import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# =========================================================
# 1. 설정 및 인증
# =========================================================
try:
    print("--- [Surfit Sender] 시작 ---")
    
    # 환경변수 로드 확인
    if 'GOOGLE_CREDENTIALS' not in os.environ:
        raise Exception("환경변수 GOOGLE_CREDENTIALS가 없습니다.")

    json_creds = os.environ['GOOGLE_CREDENTIALS']
    creds_dict = json.loads(json_creds)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # 시트 열기 (파일명은 기존과 동일)
    spreadsheet = client.open('플린트스토닝 소재 DB')
    
    # [수정됨] 인덱스 번호 대신 '서핏'이라는 탭 이름을 직접 찾습니다.
    try:
        sheet = spreadsheet.worksheet('서핏')
        print(f"📂 연결된 시트: {sheet.title}")
    except gspread.exceptions.WorksheetNotFound:
        print("❌ '서핏'이라는 이름의 탭을 찾을 수 없습니다. 탭 이름을 확인해주세요.")
        exit()
    except Exception as e:
        print(f"❌ 시트 로드 중 에러: {e}")
        exit()

    # 데이터 가져오기
    data = sheet.get_all_values()
    if not data:
        print("데이터가 없습니다.")
        exit()

    headers = data.pop(0)
    df = pd.DataFrame(data, columns=headers)
    
    # 헤더 공백 제거
    df.columns = df.columns.str.strip()

    # =========================================================
    # 2. 필터링
    # =========================================================
    COL_STATUS = 'status'
    COL_PUBLISH = 'publish'
    COL_TITLE = 'title'
    COL_URL = 'url'

    required_cols = [COL_STATUS, COL_PUBLISH, COL_TITLE, COL_URL]
    for col in required_cols:
        if col not in df.columns:
            print(f"❌ 오류: 시트에 '{col}' 헤더가 없습니다.")
            exit()

    # 조건: status는 'archived', publish는 'TRUE'
    condition = (df[COL_STATUS].str.strip() == 'archived') & (df[COL_PUBLISH].str.strip() == 'TRUE')
    target_rows = df[condition]

    if target_rows.empty:
        print("ℹ️ 발송할 대상(archived & publish=TRUE)이 없습니다.")
        exit()

    # 첫 번째 행 선택
    row = target_rows.iloc[0]
    
    # 행 번호 계산 (헤더 제외한 데이터 프레임 인덱스 + 2)
    update_row_index = row.name + 2
    
    print(f"▶ 선택된 행 번호: {update_row_index}")

    # =========================================================
    # 3. 데이터 추출
    # =========================================================
    project_title = row[COL_TITLE]
    target_url = row[COL_URL]
    
    print(f"▶ 제목: {project_title}")
    print(f"▶ URL: {target_url}")

    # =========================================================
    # 4. 웹 스크래핑
    # =========================================================
    print("--- 스크래핑 시작 ---")
    headers_ua = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(target_url, headers=headers_ua, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        article = soup.find('article')
        if article:
            paragraphs = article.find_all('p')
        else:
            paragraphs = soup.find_all('p')
        
        # 빈 문단 제외 및 공백 제거 후 연결
        text_list = [p.get_text().strip() for p in paragraphs if p.get_text().strip()]
        full_text = " ".join(text_list)
        
        if len(full_text) < 50:
             print("⚠️ 본문 내용이 너무 짧습니다. (스크래핑 실패 가능성)")
             
        truncated_text = full_text[:3000]
        
    except Exception as e:
        print(f"❌ 스크래핑 실패:
