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

    texts = []

    # 제목 계열 (회사/포지션 단서를 많이 줌)
    for tag in soup.find_all(['h1', 'h2', 'h3']):
        texts.append(tag.get_text(separator=" ", strip=True))

    # 본문 문단
    for p in soup.find_all('p'):
        texts.append(p.get_text(separator=" ", strip=True))

    # 리스트 항목 (업무 내용, 회사 특징 등)
    for li in soup.find_all('li'):
        texts.append(li.get_text(separator=" ", strip=True))

    # meta description도 있으면 추가
    meta_desc = soup.find('meta', attrs={'name': 'description'})
    if meta_desc and meta_desc.get('content'):
        texts.append(meta_desc['content'].strip())

    full_text = " ".join(texts)
    truncated_text = full_text[:4000]  # 조금 늘려도 됩니다 (모델 입력 한도 안에서)


        # =========================================================
    # 5. GPT 요약 (회사명 지정 + 회사 소개 작성)
    # =========================================================
    print("--- GPT 요약 요청 ---")
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

    gpt_prompt = f"""
    [목표]
    - 에디터들이 봤을 때 "이 포지션이 어떤 회사의 어떤 역할인지" 한눈에 이해하게 한다.
    - 회사 소개는 2~3줄 정도로, 채용 공고 본문과 너의 배경지식을 활용해 구체적으로 작성한다.
    - 필요 이상으로 장황하게 쓰지 말고, 핵심만 전달한다.

    [출력 형식 예시]

    *추천 채용 공고*
    [{company_name}] {project_title}

    프로덕트와 콘텐츠를 동시에 다루는 디지털 스튜디오로,
    브랜딩과 캠페인, 콘텐츠 제작을 통합적으로 수행합니다.
    B2B 브랜드와 함께 장기적인 콘텐츠 전략을 설계하는 일을 중심으로 합니다.

    위 형식을 그대로 따르되, 회사 설명 부분은 아래 [채용 정보]를 참고해서 네가 새로 써줘.

    [작성 규칙]
    1. 첫 줄은 무조건 `*추천 채용 공고*`로 시작한다.
    2. 두 번째 줄은 반드시 `[{company_name}] {project_title}` 형식으로 쓴다.
    3. 그 아래에 회사 설명을 2~3줄로 쓴다.
    4. "이 회사는..." 으로 시작하지 말고 바로 설명을 시작한다.
    5. 불필요한 서두(예: "알겠습니다")는 절대 넣지 않는다.
    6. 슬랙 이모지는 넣지 않는다. (링크는 파이썬 코드에서 붙인다.)

    [채용 정보]
    회사명: {company_name}
    공고 제목: {project_title}
    본문 텍스트(일부): {truncated_text}
    """

    completion = client_openai.chat.completions.create(
        model="gpt-4.1",  # 최신 엔진 사용
        messages=[
            {
                "role": "system",
                "content": "너는 채용 공고를 Slack 메시지 형식으로 정리하는 전문 어시스턴트다. 문체는 간결하고 정보 중심이어야 하며, 불필요한 인사말이나 메타 코멘트는 포함하지 않는다."
            },
            {"role": "user", "content": gpt_prompt}
        ],
        temperature=0.3,
    )

    base_message = completion.choices[0].message.content.strip()
    final_message_with_link = f"{base_message}\n\n🔗 <{target_url}|공고 바로가기>"

    print("--- GPT 응답 완료 ---")
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
