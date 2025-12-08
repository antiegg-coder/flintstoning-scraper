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
    print("--- [Wanted Sender] 시작 ---")
    
    json_creds = os.environ['GOOGLE_CREDENTIALS']
    creds_dict = json.loads(json_creds)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # 시트 제목
    spreadsheet = client.open('플린트스토닝 소재 DB') 
    
    # 네 번째 탭 선택 (Index 3)
    sheet = spreadsheet.get_worksheet(3)

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

    col_f = df.columns[5] # F열
    
    # 조건 확인
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
    # 3. 데이터 추출 (제목, URL, 회사명)
    # =========================================================
    
    title_col_name = 'title' 
    url_col_name = 'url'
    company_col_name = 'company' 

    missing_cols = []
    if title_col_name not in row: missing_cols.append(title_col_name)
    if url_col_name not in row: missing_cols.append(url_col_name)
    
    if company_col_name not in row: 
        print(f"⚠️ 경고: '{company_col_name}' 컬럼이 없습니다. 회사명은 'Company'로 대체합니다.")
        company_name = "Company"
    else:
        company_name = row[company_col_name]

    if missing_cols:
        print(f"오류: 엑셀 헤더 이름({', '.join(missing_cols)})을 확인해주세요.")
        exit()

    project_title = row[title_col_name]
    target_url = row[url_col_name]
    
    print(f"▶ 회사명: {company_name}")
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
    # 5. GPT 요약 (회사명 지정 + 회사 소개 작성)
    # =========================================================
    print("--- GPT 요약 요청 ---")
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

    # [수정] 본문 요약 대신 '회사에 대한 설명'을 요청하도록 프롬프트 변경
    gpt_prompt = f"""
    너는 채용 공고를 분석해서 슬랙(Slack) 메시지로 보내기 좋은 형태로 바꿔주는 봇이야.
    아래 [채용 정보]와 너의 배경지식을 활용해서, **출력 예시**와 똑같은 포맷으로 답변해.

    [출력 예시]
    *추천 채용 공고*
    [{company_name}] {project_title}

    여기에 **[{company_name}]가 어떤 회사인지(주요 서비스, 비즈니스 모델 등)**를 2~3줄로 설명해줘.
    채용 공고 본문의 내용을 참고하되, 네가 알고 있는 회사라면 그 지식을 활용해서 구체적으로 적어줘.
    어투는 해요체(~합니다)를 사용해.


    [작성 규칙]
    1. 첫 줄은 무조건 `*추천 채용 공고*`로 고정해.
    2. 두 번째 줄은 반드시 `[{company_name}] {project_title}` 그대로 작성해.
    3. "이 회사는..." 처럼 주어로 시작하지 말고 자연스럽게 바로 설명을 시작해.
    4. 불필요한 서두(예: "알겠습니다")는 절대 넣지 마.

    [채용 정보]
    회사명: {company_name}
    공고 제목: {project_title}
    본문 텍스트: {truncated_text}
    """

    completion = client_openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful HR assistant. You are good at explaining what a company does."},
            {"role": "user", "content": gpt_prompt}
        ]
    )

    final_message = completion.choices[0].message.content.strip()
    
    print("--- GPT 응답 완료 ---")

    # 링크 추가
    final_message_with_link = f"{final_message}\n\n 🔗 <{target_url}|공고 바로가기>"

    print("--- 최종 전송 메시지 ---")
    print(final_message_with_link)
    
    # =========================================================
    # 6. 슬랙 전송 & 시트 업데이트 (published 처리)
    # =========================================================
    print("--- 슬랙 전송 시작 ---")
    
    webhook_url = os.environ['SLACK_WEBHOOK_URL']
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

except Exception as e:
    print(f"\n❌ 에러 발생: {e}")
