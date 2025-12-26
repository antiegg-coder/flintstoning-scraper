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
    print("--- [Mix Sender] 프로세스 시작 ---")
    
    # 환경변수 로드
    if 'GOOGLE_CREDENTIALS' not in os.environ:
        raise Exception("환경변수 GOOGLE_CREDENTIALS가 설정되지 않았습니다.")

    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # 시트 열기
    spreadsheet = client.open('플린트스토닝 소재 DB')
    sheet = spreadsheet.get_worksheet(2)  # 세 번째 탭
    print(f"📂 연결된 시트: {sheet.title}")

    # 데이터 로드
    data = sheet.get_all_values()
    if len(data) <= 1:
        print("ℹ️ 처리할 데이터가 시트에 없습니다.")
        exit()

    headers = [h.strip() for h in data[0]]
    df = pd.DataFrame(data[1:], columns=headers)

    # =========================================================
    # 2. 필터링 (상태가 archived이고 publish가 TRUE인 데이터)
    # =========================================================
    COL_STATUS = 'status'
    COL_PUBLISH = 'publish'
    COL_TITLE = 'title'
    COL_URL = 'url'

    # 필터링 (대소문자 및 공백 허용)
    target_rows = df[
        (df[COL_STATUS].str.strip().str.lower() == 'archived') & 
        (df[COL_PUBLISH].str.strip().str.upper() == 'TRUE')
    ]

    if target_rows.empty:
        print("ℹ️ 발송 대기 중인 아티클이 없습니다.")
        exit()

    # 가장 오래된(상단) 1개 행 처리
    row = target_rows.iloc[0]
    # 시트 인덱스 계산: df 인덱스는 0부터, 헤더 제외 데이터는 2행부터 시작
    update_row_index = int(row.name) + 2
    
    project_title = row[COL_TITLE]
    target_url = row[COL_URL]
    
    print(f"▶ 대상 선정: {project_title} ({target_url})")

    # =========================================================
    # 3. 웹 스크래핑 (개선된 본문 추출)
    # =========================================================
    print("--- 🕸️ 본문 스크래핑 시작 ---")
    headers_ua = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    try:
        resp = requests.get(target_url, headers=headers_ua, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # 본문 영역 탐색 (일반적인 태그들)
        main_content = soup.find(['article', 'main']) or soup.find('div', class_='content')
        target_area = main_content if main_content else soup
        
        # 너무 짧거나 광고성 문구 제외하고 텍스트 추출
        paragraphs = target_area.find_all(['p', 'h2', 'h3'])
        text_content = " ".join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
        
        if len(text_content) < 100:
            print("⚠️ 본문이 너무 짧습니다. 전체 텍스트 추출로 전환합니다.")
            text_content = soup.get_text(separator=' ', strip=True)

        truncated_text = text_content[:3500] # GPT 토큰 절약을 위한 제한
        
    except Exception as e:
        print(f"❌ 스크래핑 실패: {e}")
        exit()

    # =========================================================
    # 4. GPT 요약 (GPT-4o-mini 사용)
    # =========================================================
    print("--- 🤖 GPT 요약 요청 (gpt-4o-mini) ---")
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

    gpt_prompt = f"""
    당신은 IT/테크 인사이트 큐레이터입니다. 다음 글을 분석하여 팀원들에게 공유할 핵심 내용을 요약하세요.
    반드시 한국어로 답변하고, 제공된 JSON 형식에 맞춰 응답하세요.

    [글 내용]
    {truncated_text}

    [JSON 형식]
    {{
      "key_points": ["핵심 요약 1", "핵심 요약 2", "핵심 요약 3", "핵심 요약 4"],
      "recommendations": ["추천 대상/이유 1", "추천 대상/이유 2"]
    }}
    """

    completion = client_openai.chat.completions.create(
        model="gpt-4o-mini",
        response_format={ "type": "json_object" },
        messages=[
            {"role": "system", "content": "You are a professional tech analyst who outputs strictly in JSON."},
            {"role": "user", "content": gpt_prompt}
        ]
    )

    gpt_res = json.loads(completion.choices[0].message.content)
    key_points = gpt_res.get("key_points", [])
    recommendations = gpt_res.get("recommendations", [])

    # =========================================================
    # 5. 슬랙 전송 (Block Kit)
    # =========================================================
    print("--- 💬 슬랙 전송 시작 ---")
    webhook_url = os.environ['SLACK_WEBHOOK_URL']

    key_points_text = "\n".join([f"• {p}" for p in key_points])
    recommend_text = "\n".join([f"• {r}" for r in recommendations])

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚀 오늘의 인사이트 아티클", "emoji": True}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*<{target_url}|{project_title}>*"}
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"📌 *핵심 요약*\n{key_points_text}"}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"💡 *추천 포인트*\n{recommend_text}"}
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "원문 읽어보기"},
                    "style": "primary",
                    "url": target_url
                }
            ]
        }
    ]

    slack_res = requests.post(webhook_url, json={"blocks": blocks})

    # =========================================================
    # 6. 시트 상태 업데이트 (중복 방지)
    # =========================================================
    if slack_res.status_code == 200:
        print("✅ 슬랙 발송 성공!")
        # publish 컬럼 위치(열 번호) 찾기
        publish_col_idx = headers.index(COL_PUBLISH) + 1
        # 해당 셀을 'DONE'으로 업데이트하여 다음 실행 시 제외되도록 함
        sheet.update_cell(update_row_index, publish_col_idx, 'DONE')
        print(f"✅ 시트 업데이트 완료: {update_row_index}행 'DONE' 처리")
    else:
        print(f"❌ 슬랙 전송 실패: {slack_res.status_code}")

except Exception as e:
    print(f"❌ 전체 프로세스 중 치명적 오류 발생: {e}")

finally:
    print("--- [Mix Sender] 종료 ---")
