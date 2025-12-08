import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
import time

# =========================================================
# 1. 설정 및 인증
# =========================================================
try:
    json_creds = os.environ['GOOGLE_CREDENTIALS']
    creds_dict = json.loads(json_creds)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # [수정] '플린트스토닝 소재 DB' 시트 열기
    spreadsheet = client.open('플린트스토닝 소재 DB')
    
    # [수정] 두 번째 탭 선택 (인덱스는 0부터 시작하므로 1이 두 번째 탭)
    sheet = spreadsheet.get_worksheet(1) 

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
    
    # [중요] 업데이트를 위해 행 번호 저장 (Pandas 인덱스 + 2 = 시트 실제 행 번호)
    # Pandas는 0부터 시작, 헤더 1줄 제외했으므로 +2를 해야 실제 시트 행 번호와 맞음
    update_row_index = row.name + 2 
    
    print(f"▶ 선택된 행 번호: {update_row_index}")

    # =========================================================
    # 3. 데이터 추출 (A열: 제목, C열: URL)
    # =========================================================
    
    # [수정] A열(인덱스 0)에서 제목 가져오기
    article_title = row.iloc[0] 
    
    # [수정] C열(인덱스 2)에서 URL 가져오기
    target_url = row.iloc[2]

    if not target_url.startswith('http'):
        print(f"URL 형식이 올바르지 않습니다: {target_url}")
        exit()

    print(f"▶ 제목: {article_title}")
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
    truncated_text = full_text[:3000] # 3000자 제한


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

    # 1. GPT 응답 내용 가져오기
    gpt_body = completion.choices[0].message.content

    # 2. [수정] 헤더를 '추천 프로젝트' -> '오늘의 인사이트'로 변경
    final_message = f"*📰 오늘의 인사이트*\n<{target_url}|{project_title}>\n\n{gpt_body}"
    
    # 3. [수정] 버튼 텍스트를 '모집공고 바로가기' -> '원문 보러가기'로 변경
    final_message_with_link = f"{final_message}\n\n🔗 <{target_url}|원문 보러가기>"
    
    print("--- 최종 결과물 ---")
    print(final_message_with_link)


    # =========================================================
    # 6. 슬랙 전송 & 시트 업데이트 (published 처리)
    # =========================================================
    print("--- 슬랙 전송 시작 ---")
    
    webhook_url = os.environ['SLACK_WEBHOOK_URL']
    
    # 4. 전송할 때는 링크가 포함된 변수(final_message_with_link)를 사용
    payload = {"text": final_message_with_link}
    
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
