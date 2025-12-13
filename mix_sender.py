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
    
    # 환경변수 로드 확인
    if 'GOOGLE_CREDENTIALS' not in os.environ:
        raise Exception("환경변수 GOOGLE_CREDENTIALS가 없습니다.")

    json_creds = os.environ['GOOGLE_CREDENTIALS']
    creds_dict = json.loads(json_creds)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # 시트 열기
    spreadsheet = client.open('플린트스토닝 소재 DB')
    
    # [수정 권장] 시트 이름으로 가져오기 (예: '채용공고', '아티클' 등 실제 탭 이름 입력)
    # 인덱스(2)를 사용하려면 탭 순서가 절대 바뀌지 않도록 주의해야 합니다.
    try:
        # sheet = spreadsheet.worksheet('실제_탭_이름')  # <- 가장 권장하는 방식
        sheet = spreadsheet.get_worksheet(2) 
        print(f"📂 연결된 시트: {sheet.title}")
    except:
        print("❌ 시트를 찾을 수 없습니다.")
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

    # 조건: status는 'archived', publish는 'TRUE' (대소문자 무관하게 처리하려면 upper() 사용 권장)
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
    project_title = row[COL_TITLE]  # 변수명 통일
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
        
        # [개선] 빈 문단 제외 및 공백 제거 후 연결
        text_list = [p.get_text().strip() for p in paragraphs if p.get_text().strip()]
        full_text = " ".join(text_list)
        
        if len(full_text) < 50:
             print("⚠️ 본문 내용이 너무 짧습니다. (스크래핑 실패 가능성)")
             # 필요 시 여기서 exit() 할 수도 있음
             
        truncated_text = full_text[:3000]
        
    except Exception as e:
        print(f"❌ 스크래핑 실패: {e}")
        exit()

    # =========================================================
    # 5. GPT 요약 (프롬프트 및 메시지 구성 수정됨)
    # =========================================================
    print("--- GPT 요약 요청 ---")
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

    # [수정] 요청하신 구조(내용 요약, 추천 이유)에 맞춰 프롬프트 변경
    gpt_prompt = f"""
    너는 IT/테크 트렌드를 분석해주는 '인사이트 큐레이터'야.
    아래 [글 내용]을 읽고, 팀원들에게 공유할 수 있게 요약해줘.

    [작성 규칙]
    1. **어조**: 모든 문장은 반드시 '**~합니다.**' 또는 '**~입니다.**'와 같은 정중한 합쇼체(경어)로 끝내야 해.
    2. **금지**: '~음', '~함', '~것' 같은 명사형 종결이나 반말은 절대 사용하지 마.
    3. **이모지**: 본문 내용 중에 이모지를 절대 사용하지 마.

    [출력 양식]
    *내용 요약*
    (글의 핵심 내용을 3문장 내외의 줄글로 작성. 반드시 경어로 끝낼 것.)

    *추천 이유*
    (이 글을 팀원들에게 추천하는 이유나 핵심 포인트를 1~2문장으로 작성. 반드시 경어로 끝낼 것.)

    [글 내용]
    {truncated_text}
    """

    completion = client_openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Use polite Korean sentences ending in period."},
            {"role": "user", "content": gpt_prompt}
        ]
    )

    gpt_body = completion.choices[0].message.content

    # [수정] 메시지 조립 순서 및 URL 형식 변경
    # 1. 헤더: <지금 주목해야 할 아티클>
    # 2. 제목
    # 3. GPT 요약 내용 (내용 요약 + 추천 이유)
    # 4. URL (아티클 바로가기)
    
    # 슬랙 링크 포맷: <URL|텍스트>
    formatted_link = f"<{target_url}|아티클 바로가기>"
    
    final_message_with_link = (
        f"*<지금 주목해야 할 아티클>*\n\n"
        f"제목: {project_title}\n\n"
        f"{gpt_body}\n\n"
        f"👉 {formatted_link}"
    )
    
    print("--- 최종 결과물 ---")
    print(final_message_with_link)

    # =========================================================
    # 6. 슬랙 전송 & 시트 업데이트 (이후 코드는 기존과 동일하게 사용)
    # =========================================================

    # =========================================================
    # 6. 슬랙 전송 & 시트 업데이트
    # =========================================================
    print("--- 슬랙 전송 시작 ---")
    webhook_url = os.environ['SLACK_WEBHOOK_URL']
    payload = {"text": final_message_with_link}
    
    slack_res = requests.post(webhook_url, json=payload)
    
    if slack_res.status_code == 200:
        print("✅ 슬랙 전송 성공!")
        
        try:
            # status 컬럼 인덱스 찾기 (+1 보정)
            status_col_index = headers.index(COL_STATUS) + 1
            
            print(f"▶ 시트 상태 업데이트 중... (행: {update_row_index}, 열: {status_col_index})")
            sheet.update_cell(update_row_index, status_col_index, 'published')
            print("✅ 상태 변경 완료 (archived -> published)")
        except Exception as e:
            print(f"⚠️ 상태 업데이트 실패: {e}")
            
    else:
        print(f"❌ 전송 실패 (상태 코드: {slack_res.status_code})")
        print(slack_res.text)

except Exception as e:
    print(f"🚨 전체 실행 중 에러 발생: {e}")
