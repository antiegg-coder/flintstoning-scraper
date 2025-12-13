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
    print("--- [Letspl Sender] 시작 ---")
    
    json_creds = os.environ['GOOGLE_CREDENTIALS']
    creds_dict = json.loads(json_creds)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # 시트 열기
    spreadsheet = client.open('플린트스토닝 소재 DB')
    
    # [수정] 다섯 번째 탭 선택 (Index 0, 1, 2, 3, "4")
    sheet = spreadsheet.get_worksheet(4)

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
    # 3. 데이터 추출
    # =========================================================
    
    # A열(제목), C열(URL)은 기존 위치 유지 (필요 시 이것도 헤더명으로 변경 가능)
    project_title = row.iloc[0]
    target_url = row.iloc[2]
    
    # [수정] 'location' 헤더 이름으로 지역 정보 가져오기
    # 엑셀 파일의 헤더에 'location' 이라고 적혀 있어야 합니다.
    try:
        project_location = row['location']
    except KeyError:
        print("⚠️ 'location' 헤더를 찾을 수 없습니다. 컬럼명을 확인해주세요.")
        project_location = "지역 정보 없음"

    print(f"▶ 제목: {project_title}")
    print(f"▶ 지역: {project_location}")
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
    # 5. GPT 요약
    # =========================================================
    print("--- GPT 요약 요청 ---")
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

    # 프롬프트: '프로젝트 요약'과 '이런 분을 찾고 있어요' 두 파트로 분리 요청
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
    # 6. 슬랙 전송
    # =========================================================
    
    # [최종 메시지 조립]
    # 1. 헤더: <사이드프로젝트 동료 찾고 있어요>
    # 2. 정보: 공고명, 지역
    # 3. 내용: GPT 요약 내용
    # 4. 링크: '아티클 바로가기' 텍스트에 URL 적용
    
    final_message = f"<사이드프로젝트 동료 찾고 있어요>\n\n" \
                    f"{project_title}\n" \
                    f"*지역:* {project_location}\n\n" \
                    f"{gpt_body}\n\n" \
                    f"🔗 <{target_url}|게시글 바로가기>"
    
    print("--- 최종 결과물 ---")
    print(final_message)

    print("--- 슬랙 전송 시작 ---")
    webhook_url = os.environ['SLACK_WEBHOOK_URL']
    payload = {"text": final_message}
    
    slack_res = requests.post(webhook_url, json=payload)
    
    # ... (이하 동일)
    
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
