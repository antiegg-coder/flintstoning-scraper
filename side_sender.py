import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# =========================================================
# [설정] 시트 헤더 이름 설정 (이 부분을 실제 시트와 맞춰주세요)
# =========================================================
SHEET_NAME = '플린트스토닝 소재 DB'
COL_TITLE = 'title'      # 제목 컬럼 헤더명
COL_URL = 'url'          # URL 컬럼 헤더명
COL_LOCATION = 'location' # [추가] 지역 컬럼 헤더명
COL_STATUS = 'status'    # 상태 컬럼 헤더명
COL_PUBLISH = 'publish'  # 발행 여부 컬럼 헤더명

# =========================================================
# 1. 설정 및 인증
# =========================================================
try:
    print("--- [Side Sender] 시작 ---")
    
    json_creds = os.environ['GOOGLE_CREDENTIALS']
    creds_dict = json.loads(json_creds)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    spreadsheet = client.open(SHEET_NAME) 
    # [주의] 5번째 탭(index 4)을 가져옵니다. 필요시 get_worksheet(0) 등으로 변경.
    sheet = spreadsheet.get_worksheet(4) 

    # 데이터 가져오기
    data = sheet.get_all_values()
    if not data:
        print("❌ 데이터가 없습니다.")
        exit()

    headers = data.pop(0)
    df = pd.DataFrame(data, columns=headers)

    # =========================================================
    # 2. 필터링 (Status: archived, Publish: TRUE)
    # =========================================================
    
    # 필수 헤더 존재 여부 확인 (Location 추가됨)
    required_cols = [COL_TITLE, COL_URL, COL_LOCATION, COL_STATUS, COL_PUBLISH]
    for col in required_cols:
        if col not in df.columns:
            print(f"❌ 오류: 시트에 '{col}' 헤더가 없습니다. 헤더 이름을 확인해주세요.")
            exit()

    # 조건 확인 (공백 제거 후 비교)
    condition = (df[COL_STATUS].str.strip() == 'archived') & (df[COL_PUBLISH].str.strip() == 'TRUE')
    target_rows = df[condition]

    if target_rows.empty:
        print("ℹ️ 발송할 대상(archived & publish=TRUE)이 없습니다.")
        exit()

    # 첫 번째 행 선택
    row = target_rows.iloc[0]
    
    # 행 번호 계산
    update_row_index = row.name + 2
    
    # 상태 업데이트를 위한 열 번호 계산
    status_col_index = headers.index(COL_STATUS) + 1

    # 데이터 추출
    project_title = row[COL_TITLE]
    project_location = row[COL_LOCATION] # 지역 정보 추출
    target_url = row[COL_URL]
    
    print(f"▶ 선택된 행: {update_row_index}")
    print(f"▶ 제목: {project_title}")
    print(f"▶ 지역: {project_location}")
    print(f"▶ URL: {target_url}")

    # =========================================================
    # 3. 웹 스크래핑
    # =========================================================
    print("--- 스크래핑 시작 ---")
    headers_ua = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    try:
        response = requests.get(target_url, headers=headers_ua, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p')
        full_text = " ".join([p.get_text() for p in paragraphs])
        
        if len(full_text) < 50:
            full_text = soup.get_text()

        truncated_text = full_text[:3000].strip()
        
        if not truncated_text:
            raise Exception("본문 내용을 추출할 수 없습니다.")

    except Exception as e:
        print(f"❌ 스크래핑 실패: {e}")
        exit()

    # =========================================================
    # 4. GPT 요약 (요약 + 추천 대상)
    # =========================================================
    print("--- GPT 요약 요청 ---")
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

    gpt_prompt = f"""
    너는 채용 공고나 프로젝트 정보를 정리해주는 '전문 에디터'야.
    아래 [글 내용]을 읽고, 지정된 **출력 양식**을 엄격하게 지켜서 답변해.
    모든 텍스트에 이모지를 절대 사용하지 마.

    [출력 양식]
    *프로젝트 요약*
    (프로젝트의 핵심 내용을 2~3문장으로 요약)

    *이런 분을 찾고 있어요*
    - (추천 대상 1)
    - (추천 대상 2)

    [글 내용]
    {truncated_text}
    """

    completion = client_openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a strict output formatter. Do not use emojis."},
            {"role": "user", "content": gpt_prompt}
        ]
    )

    gpt_body = completion.choices[0].message.content

    # =========================================================
    # 5. 슬랙 전송 (메시지 포맷 수정됨)
    # =========================================================
    
    # [메시지 구성 요구사항 반영]
    # 1. 첫 줄: <사이드프로젝트 동료 찾고 있어요>
    # 2. 순서: 공고명, 지역, 프로젝트 요약, 이런 분..., URL
    # 3. URL: <URL|바로가기> 형태
    
    final_message = f"<사이드프로젝트 동료 찾고 있어요>\n\n" \
                    f"*{project_title}*\n\n" \
                    f"*지역:* {project_location}\n\n" \
                    f"{gpt_body}\n\n" \
                    f"🔗 <{target_url}|게시글 바로가기>"
    
    print("--- 최종 결과물 생성 완료 ---")
    print(final_message)

    print("--- 슬랙 전송 시작 ---")
    
    webhook_url = os.environ['SLACK_WEBHOOK_URL']
    payload = {"text": final_message}
    
    slack_res = requests.post(webhook_url, json=payload)
    
    if slack_res.status_code == 200:
        print("✅ 슬랙 전송 성공!")
        
        try:
            print(f"▶ 시트 상태 업데이트 중... (행: {update_row_index}, 열: {status_col_index})")
            sheet.update_cell(update_row_index, status_col_index, 'published')
            print("✅ 상태 변경 완료 (archived -> published)")
        except Exception as e:
            print(f"⚠️ 상태 업데이트 실패: {e}")
            
    else:
        print(f"❌ 전송 실패 (상태 코드: {slack_res.status_code})")
        print(slack_res.text)

except Exception as e:
    print(f"\n❌ 치명적 에러 발생: {e}")
