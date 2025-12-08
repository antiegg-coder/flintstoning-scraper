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
    print("--- [Mix Sender] 시작 ---")
    
    json_creds = os.environ['GOOGLE_CREDENTIALS']
    creds_dict = json.loads(json_creds)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # 시트 열기
    spreadsheet = client.open('플린트스토닝 소재 DB')
    
    # [수정] 세 번째 탭 선택 (Index 0, 1, "2")
    sheet = spreadsheet.get_worksheet(2)

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
    update_row_index = row.name + 2
    
    print(f"▶ 선택된 행 번호: {update_row_index}")

    # =========================================================
    # 3. 데이터 추출 (A열: 제목, C열: URL)
    # =========================================================
    
    # 위치 기반 추출 (A열=0, C열=2)
    # 만약 헤더 이름으로 찾고 싶다면 row['제목'], row['URL']로 변경 가능
    project_title = row.iloc[0]
    target_url = row.iloc[2]
    
    print(f"▶ 제목: {project_title}")
    print(f"▶ URL: {target_url}")

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
    # 5. GPT 요약 (인사이트 스타일)
    # =========================================================
    print("--- GPT 요약 요청 ---")
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

    gpt_prompt = f"""
    너는 IT/테크 트렌드를 분석해주는 '인사이트 큐레이터'야.
    아래 [글 내용]을 읽고, 팀원들에게 공유할 수 있게 깔끔하게 요약해줘.
    이모지 금지, 자연스러운 줄글 사용.

    [출력 양식]
    *요약*
    (글의 핵심 내용을 3문장 내외의 자연스러운 줄글로 작성)

    *인사이트*
    (이 글에서 얻을 수 있는 시사점이나 배울 점을 1~2문장으로 작성)

    [글 내용]
    {truncated_text}
    """

    completion = client_openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Do not use emojis."},
            {"role": "user", "content": gpt_prompt}
        ]
    )

    gpt_body = completion.choices[0].message.content
    
    # 메시지 조립
    final_message = f"*오늘의 인사이트*\n<{target_url}|{project_title}>\n\n{gpt_body}"
    
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
        
        try:
            print(f"▶ 시트 상태 업데이트 중... (행: {update_row_index}, 열: 6)")
            sheet.update_cell(update_row_index, 6, 'published')
            print("✅ 상태 변경 완료 (archived -> published)")
        except Exception as e:
            print(f"⚠️ 상태 업데이트 실패: {e}")
            
    else:
        print(f"❌ 전송 실패 (상태 코드: {slack_res.status_code})")
        print(slack_res.text)

except Exception as e:
    print(f"\n❌ 에러 발생: {e}")
