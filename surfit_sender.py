import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
import time  # API 할당량 초과 방지를 위해 필수입니다.

# =========================================================
# 1. 설정 및 인증
# =========================================================
try:
    print("--- [Surfit Sender] 프로세스를 시작합니다 ---")
    
    if 'GOOGLE_CREDENTIALS' not in os.environ:
        raise Exception("환경변수 GOOGLE_CREDENTIALS가 설정되지 않았습니다.")

    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    spreadsheet = client.open('플린트스토닝 소재 DB')
    
    # [GID 2112710663 기반 워크시트 선택]
    TARGET_GID = 2112710663
    sheet = None
    for s in spreadsheet.worksheets():
        if s.id == TARGET_GID:
            sheet = s
            break
    
    if not sheet:
        raise Exception(f"GID가 {TARGET_GID}인 워크시트를 찾을 수 없습니다.")
    
    data = sheet.get_all_values()
    headers = [h.strip() for h in data[0]]
    df = pd.DataFrame(data[1:], columns=headers)

    COL_STATUS = 'status'
    COL_IDENTITY = 'identity_match'
    COL_TITLE = 'title'
    COL_URL = 'url'

    target_rows = df[df[COL_STATUS].str.strip().str.lower() == 'archived']

    if target_rows.empty:
        print("ℹ️ 'archived' 상태의 아티클이 현재 시트에 없습니다.")
        exit()

    identity_col_idx = headers.index(COL_IDENTITY) + 1
    status_col_idx = headers.index(COL_STATUS) + 1
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
    webhook_url = os.environ['SLACK_WEBHOOK_URL']

    # =========================================================
    # 2. 메인 루프: 적합한 아티클을 찾을 때까지 반복합니다.
    # =========================================================
    for index, row in target_rows.iterrows():
        update_row_index = int(index) + 2
        project_title = row[COL_TITLE]
        target_url = row[COL_URL]
        
        print(f"\n🔍 {update_row_index}행의 아티클을 검토하고 있습니다: {project_title}")

        try:
            # 3. 웹 스크래핑 보완 (403 에러 방지용 헤더)
            headers_ua = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
                'Referer': 'https://www.google.com/'
            }
            resp = requests.get(target_url, headers=headers_ua, timeout=15)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            paragraphs = soup.find_all(['p', 'h2', 'h3'])
            text_content = " ".join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
            truncated_text = text_content[:3500]

            # 4. ANTIEGG 정체성 판단 (엄격한 기준 및 사례 반영)
            identity_prompt = f"""
            안녕하세요, 당신은 프리랜서 에디터 공동체 'ANTIEGG'의 편집장입니다. 
            아래 내용을 읽고 정체성(콘텐츠 마케팅, 글쓰기, 브랜드, 문화 담론)에 부합하는지 엄격히 판단해 주세요.
            [사례] ✅ 적합: 브랜드 협업 분석, 광고 비평, 에디터 회고 / ❌ 부적합: 단순 기능 개선, 창업 아이템 추천.
            [글 내용] {truncated_text}
            출력 포맷(JSON): {{"is_appropriate": true/false, "reason": "이유 설명"}}
            """
            check_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[{"role": "system", "content": "당신은 ANTIEGG의 엄격한 편집장입니다."},
                          {"role": "user", "content": identity_prompt}]
            )
            judgment = json.loads(check_res.choices[0].message.content)
            is_appropriate = judgment.get("is_appropriate", False)
            
            # [API 429 에러 방지] 1초 대기 후 업데이트
            time.sleep(1.5)
            sheet.update_cell(update_row_index, identity_col_idx, str(is_appropriate).upper())

            if not is_appropriate:
                print(f"⚠️ 부적합 판정: {judgment.get('reason')}")
                continue

            # 5. 슬랙 메시지 생성 (에디터 중심 추천)
            summary_prompt = f"""
            당신은 ANTIEGG의 큐레이터입니다. 동료 에디터를 위한 추천사를 작성해 주세요.
            1. key_points: 핵심 맥락 4문장.
            2. recommendations: 이 글이 꼭 필요한 에디터를 3가지 유형으로 제안 (~한 분).
            어투: 정중하고 지적인 경어체.
            [글 내용] {truncated_text}
            출력 포맷(JSON): {{"key_points": [], "recommendations": []}}
            """
            summary_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[{"role": "system", "content": "당신은 지적이고 다정한 ANTIEGG의 큐레이터입니다."},
                          {"role": "user", "content": summary_prompt}]
            )
            gpt_res = json.loads(summary_res.choices[0].message.content)
            
            # 6. 슬랙 전송
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": "지금 주목해야 할 아티클", "emoji": True}},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*{project_title}*"}},
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "📌 *이 글에서 이야기하는 것들*\n" + "\n".join([f"• {p}" for p in gpt_res.get('key_points', [])])}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "📌 *이런 분께 추천해요*\n" + "\n".join([f"• {p}" for p in gpt_res.get('recommendations', [])])}},
                {"type": "divider"},
                {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "아티클 보러가기", "emoji": True}, "style": "primary", "url": target_url}]}
            ]
            
            slack_resp = requests.post(webhook_url, json={"blocks": blocks})

            if slack_resp.status_code == 200:
                print("✅ 슬랙 전송에 성공하였습니다!")
                time.sleep(1.5)
                sheet.update_cell(update_row_index, status_col_idx, 'published')
                break 
            else:
                print(f"❌ 전송 실패 (에러 코드: {slack_resp.status_code})")
                sheet.update_cell(update_row_index, status_col_idx, 'failed')
                break

        except Exception as e:
            print(f"❌ {update_row_index}행 처리 오류: {e}")
            if "429" in str(e): # 구글 API 할당량 초과 시 60초 대기
                print("⏳ 할당량 초과로 60초간 대기합니다...")
                time.sleep(60)
            continue

except Exception as e:
    print(f"❌ 치명적 오류: {e}")
finally:
    print("--- [Surfit Sender] 프로세스가 종료되었습니다 ---")
