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
    print("--- [Side Sender] 시작 ---")
    
    json_creds = os.environ['GOOGLE_CREDENTIALS']
    creds_dict = json.loads(json_creds)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # ★ [체크] 시트 제목이 맞는지 꼭 확인하세요!
    spreadsheet = client.open('플린트스토닝 소재 DB') 
    sheet = spreadsheet.sheet1

    # 데이터 가져오기
    data = sheet.get_all_values()
    if not data:
        print("데이터가 없습니다.")
        exit()

    headers = data.pop(0)
    df = pd.DataFrame(data, columns=headers)

    # =========================================================
    # 2. 필터링 (F열: archived, publish: TRUE)
    # =========================================================
    if len(df.columns) <= 5:
        print("열 개수가 부족합니다.")
        exit()

    col_f = df.columns[5] # F열 (6번째)
    
    # 조건 확인 (archived & TRUE)
    condition = (df[col_f].str.strip() == 'archived') & (df['publish'].str.strip() == 'TRUE')
    target_rows = df[condition]

    if target_rows.empty:
        print("발송할 대상(archived & publish=TRUE)이 없습니다.")
        exit()

    # 첫 번째 행 선택
    row = target_rows.iloc[0]
    
    # ★★★ [이 부분이 핵심입니다] 행 번호 저장 ★★★
    # Pandas 인덱스는 0부터 시작, 헤더 1줄 제외했으므로 실제 시트 행 번호는 +2 해야 함
    update_row_index = row.name + 2
    
    print(f"▶ 선택된 행 번호: {update_row_index}")

    # =========================================================
    # 3. 데이터 추출 (제목 & url)
    # =========================================================
    
    # ★ [체크] 엑셀 헤더 이름 확인
    title_col_name = 'title' 
    url_col_name = 'url'

    if title_col_name not in row or url_col_name not in row:
        print(f"오류: 엑셀에 '{title_col_name}' 또는 '{url_col_name}' 헤더가 없습니다.")
        exit()
    
    project_title = row[title_col_name]
    target_url = row[url_col_name]
    
    print(f"▶ 제목: {project_title}")
    print(f"▶ url: {target_url}")

    # =========================================================
    # 4. 웹 스크래핑
    # =========================================================
    print("--- 스크래핑 시작 ---")
    headers_ua = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    response = requests.get(target_url, headers=headers_ua, timeout=10)
    if response.status_code != 200:
        print(f"접속 실패 (상태 코드: {response.status_code})")
        exit()

    soup = BeautifulSoup(response.text, 'html.parser')
    paragraphs = soup.find_all('p')
    full_text = " ".join([p.get_text() for p in paragraphs])
    truncated_text = full_text[:3000]

    # =========================================================
    # 5. GPT 요약
    # =========================================================
    print("--- GPT 요약 요청 ---")
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

    gpt_prompt = f"""
    너는 채용 공고나 프로젝트 정보를 정리해주는 '전문 에디터'야.
    아래 [글 내용]을 읽고, 지정된 **출력 양식**을 엄격하게 지켜서 답변해.
    모든 텍스트에 이모지를 절대 사용하지 마.

    [출력 양식]

    *이런 분께 추천해요*
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
    final_message = f"*추천 프로젝트*\n<{target_url}|{project_title}>\n\n{gpt_body}"
    
    print("--- 최종 결과물 ---")
    print(final_message)

    # =========================================================
    # 6. 슬랙 전송 & 시트 업데이트 (published 처리)
    # =========================================================
    print("--- 슬랙 전송 시작 ---")
    
    webhook_url = os.environ['SLACK_WEBHOOK_URL']
    payload = {"text": final_message}
    
    slack_res = requests.post(webhook_url, json=payload)
    
    if slack_res.status_code == 200:
        print("✅ 슬랙 전송 성공!")
        
        # 전송 성공 시 상태 변경 (archived -> published)
        try:
            print(f"▶ 시트 상태 업데이트 중... (행: {update_row_index}, 열: 6)")
            # 6번째 열(F열)을 'published'로 수정
            sheet.update_cell(update_row_index, 6, 'published')
            print("✅ 상태 변경 완료 (archived -> published)")
        except Exception as e:
            print(f"⚠️ 상태 업데이트 실패: {e}")
            
    else:
        print(f"❌ 전송 실패 (상태 코드: {slack_res.status_code})")
        print(slack_res.text)

except Exception as e:
    print(f"\n❌ 에러 발생: {e}")
