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
    print("--- [Surfit Sender] 시작 ---")
    
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
    
    # '서핏' 탭 연결
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
    # 4. 웹 스크래핑 (403 에러 해결을 위한 헤더 강화)
    # =========================================================
    print("--- 스크래핑 시작 ---")
    
    # [수정됨] 봇 탐지를 피하기 위해 실제 브라우저와 똑같은 헤더 사용
    headers_ua = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        'Referer': 'https://www.google.com/'
    }
    
    try:
        # 서핏 링크는 리다이렉트가 발생하므로 allow_redirects=True (기본값)
        response = requests.get(target_url, headers=headers_ua, timeout=15)
        response.raise_for_status()
        
        # 최종 도달한 URL 확인 (리다이렉트 된 경우)
        print(f"ℹ️ 최종 목적지 URL: {response.url}")
        
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
             print("⚠️ 본문 내용이 너무 짧습니다. (스크래핑 실패 또는 이미지 위주 본문 가능성)")
             
        truncated_text = full_text[:3000]
        
    except Exception as e:
        # [수정됨] 119번줄 에러 해결
        print(f"❌ 스크래핑 실패: {e}")
        exit()

    # =========================================================
    # 5. GPT 요약
    # =========================================================
    print("--- GPT 요약 요청 ---")
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

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
    (이 글을 팀원들에게 읽어보라고 추천하는 이유나 핵심 가치를 1~2문장으로 작성. 반드시 경어로 끝낼 것.)

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

    # 슬랙 메시지 조립
    slack_link_format = f"<{target_url}|아티클 바로가기>"
    
    final_message_with_link = (
        f"*<지금 주목해야 할 아티클>*\n\n"
        f"제목: {project_title}\n\n"
        f"{gpt_body}\n\n"
        f"👉 {slack_link_format}"
    )
    
    print("--- 최종 결과물 ---")
    print(final_message_with_link)

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
