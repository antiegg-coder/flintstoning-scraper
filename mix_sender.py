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
    print("--- [Mix Sender] 프로세스 시작 ---")
    
    if 'GOOGLE_CREDENTIALS' not in os.environ:
        raise Exception("환경변수 GOOGLE_CREDENTIALS가 설정되지 않았습니다.")

    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    spreadsheet = client.open('플린트스토닝 소재 DB')
    sheet = spreadsheet.get_worksheet(2) # 세 번째 탭
    
    data = sheet.get_all_values()
    headers = [h.strip() for h in data[0]]
    df = pd.DataFrame(data[1:], columns=headers)

    COL_STATUS = 'status'
    COL_PUBLISH = 'publish'
    COL_TITLE = 'title'
    COL_URL = 'url'

    target_rows = df[df[COL_STATUS].str.strip().str.lower() == 'archived']

    if target_rows.empty:
        print("ℹ️ 'archived' 상태의 아티클이 없습니다.")
        exit()

    publish_col_idx = headers.index(COL_PUBLISH) + 1
    status_col_idx = headers.index(COL_STATUS) + 1
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
    webhook_url = os.environ['SLACK_WEBHOOK_URL']

    # =========================================================
    # 2. 메인 루프: 적합한 콘텐츠를 찾을 때까지 반복
    # =========================================================
    for index, row in target_rows.iterrows():
        update_row_index = int(index) + 2
        project_title = row[COL_TITLE]
        target_url = row[COL_URL]
        
        print(f"\n🔍 검토 중 ({update_row_index}행): {project_title}")

        try:
            # 3. 스크래핑
            headers_ua = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(target_url, headers=headers_ua, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            paragraphs = soup.find_all(['p', 'h2', 'h3'])
            text_content = " ".join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
            truncated_text = text_content[:3500]

            # 4. ANTIEGG 정체성 판단
            identity_prompt = f"""
            너는 문화예술 및 테크 미디어 'ANTIEGG'의 편집장이야. 
            아래 내용을 읽고 ANTIEGG의 정체성(기존 관점을 뒤틀고 영감을 주는 인사이트)에 부합하는지 판단해.
            내용: {truncated_text}
            출력 포맷(JSON): {{"is_appropriate": true/false, "reason": "한 문장 설명"}}
            """
            check_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[{"role": "system", "content": "You are a professional editor for ANTIEGG."},
                          {"role": "user", "content": identity_prompt}]
            )
            judgment = json.loads(check_res.choices[0].message.content)
            
            if not judgment.get("is_appropriate", False):
                print(f"⚠️ 부적합: {judgment.get('reason')}")
                sheet.update_cell(update_row_index, publish_col_idx, 'FALSE')
                continue

            # 5. 슬랙 메시지 최적화 생성 (이미지 분석 결과 반영)
            print(f"✨ 적합 판정: 요약 생성을 시작합니다.")
            
            summary_prompt = f"""
            너는 ANTIEGG의 수석 에디터야. 독자들에게 지적 영감을 주는 스타일로 아래 글을 요약해줘.
            
            1. key_points: 단순 요약이 아닌 '배경-원리-방향'의 맥락이 담긴 4개 문장.
            2. recommendations: 이 글이 독자의 사고를 어떻게 확장시키는지 에디터의 시선에서 작성한 3개 문장.
            
            어투: 전문적이고 지적인 경어체 (~합니다, ~해줍니다).
            내용: {truncated_text}
            
            출력 포맷(JSON): {{"key_points": [], "recommendations": []}}
            """
            
            summary_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[{"role": "system", "content": "You are a lead editor at ANTIEGG. Use polite and intellectual Korean."},
                          {"role": "user", "content": summary_prompt}]
            )
            gpt_res = json.loads(summary_res.choices[0].message.content)
            
            # 6. 슬랙 전송 (이미지 레이아웃 재현)
            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "지금 주목해야 할 아티클", "emoji": True}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{project_title}*"}
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "📌 *이 글에서 이야기하는 것들*\n" + "\n".join([f"• {p}" for p in gpt_res.get('key_points', [])])}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "📌 *이런 분께 추천해요*\n" + "\n".join([f"• {p}" for p in gpt_res.get('recommendations', [])])}
                },
                {"type": "divider"},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "아티클 보러가기", "emoji": True},
                            "style": "primary",
                            "url": target_url
                        }
                    ]
                }
            ]
            
            slack_resp = requests.post(webhook_url, json={"blocks": blocks})

            if slack_resp.status_code == 200:
                print("✅ 슬랙 전송 성공!")
                sheet.update_cell(update_row_index, status_col_idx, 'published')
                sheet.update_cell(update_row_index, publish_col_idx, 'DONE')
                break # 한 개 성공 시 종료
            else:
                print(f"❌ 슬랙 전송 실패 (HTTP {slack_resp.status_code})")

        except Exception as e:
            print(f"❌ {update_row_index}행 처리 오류: {e}")
            continue

except Exception as e:
    print(f"❌ 치명적 오류: {e}")
finally:
    print("--- [Mix Sender] 프로세스 종료 ---")
