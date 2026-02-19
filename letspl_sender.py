import os
import sys
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
import random
import time

# =========================================================
# 1. 설정 및 인증
# =========================================================
had_fatal_error = False
try:
    print("--- [Letspl Sender] 전체 자동화 프로세스를 시작합니다 ---")

    if 'GOOGLE_CREDENTIALS' not in os.environ:
        raise Exception("환경변수 GOOGLE_CREDENTIALS가 설정되지 않았습니다.")

    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    spreadsheet = client.open('플린트스토닝 소재 DB')
    
    # GID 1669656972 기반 시트 선택
    TARGET_GID = 1669656972
    sheet = next((s for s in spreadsheet.worksheets() if s.id == TARGET_GID), None)
    
    if not sheet:
        raise Exception(f"GID가 {TARGET_GID}인 워크시트를 찾을 수 없습니다.")
    
    data = sheet.get_all_values()
    headers = [h.strip() for h in data[0]]
    df = pd.DataFrame(data[1:], columns=headers)

    # 컬럼 설정
    COL_STATUS = 'status'
    COL_TITLE = 'title'     
    COL_URL = 'url'         
    COL_LOCATION = 'location' 

    # 'archived' 상태인 모든 프로젝트 추출
    target_rows = df[df[COL_STATUS].str.strip().str.lower() == 'archived']

    if target_rows.empty:
        print("ℹ️ 처리할 'archived' 상태의 프로젝트가 없습니다.")
        exit()

    print(f"총 {len(target_rows)}건의 프로젝트 처리를 시작합니다.")

    status_col_idx = headers.index(COL_STATUS) + 1
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
    webhook_url = os.environ['SLACK_MOJIPGONGGO']
    
    session = requests.Session()

    # =========================================================
    # 2. 메인 루프: 모든 'archived' 행을 끝까지 순회합니다.
    # =========================================================
    for index, row in target_rows.iterrows():
        update_row_index = int(index) + 2
        project_title = row[COL_TITLE]
        target_url = row[COL_URL]
        sheet_location = row.get(COL_LOCATION, "").strip() 
        
        print(f"\n🔍 {update_row_index}행 요약 및 전송 중: {project_title}")

        try:
            # 3. [브라우저 위장 및 랜덤 대기]
            headers_ua = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
                'Referer': 'https://www.google.com/',
                'Connection': 'keep-alive'
            }

            time.sleep(random.uniform(3.0, 5.0))

            resp = session.get(target_url, headers=headers_ua, timeout=15)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            text_content = " ".join([p.get_text().strip() for p in soup.find_all(['p', 'h2', 'h3', 'li', 'span']) if len(p.get_text().strip()) > 10])
            truncated_text = text_content[:3500]

            # 4. [슬랙 콘텐츠 생성] 
            summary_prompt = f"""
            당신은 ANTIEGG의 프로젝트 큐레이터입니다. 지적이고 세련된 어투로 아래 글을 소개해 주세요.
            어투는 매우 정중하고 지적인 경어체 (~합니다, ~해드립니다)를 사용해 주세요. 
            JSON 포맷으로 만들어 주세요. 
            [지침]:      
            1. key_points: 프로젝트의 정체성과 핵심 기능을 설명하는 문장을 3개 내외로 작성해 주세요.
               - 첫 번째 문장 : 반드시 ‘이 프로젝트는~’을 주어로 시작해 주세요.
               - 첫 번째 문장, 이후 : 주어를 생략하고, 앞 문맥을 자연스럽게 이어 주세요.
               - 주의사항 : 각 불릿에는 반드시 하나의 문장만 포함해 주세요.
               - 주의사항 : 'ANTIEGG는~'로 시작하지 마세요.
            2. recommendations: 이 글이 꼭 필요한 에디터를 3가지 내외의 유형으로 제안해 주세요. 
               - 주의사항 : '열심히 할 분' 같은 일반적인 말은 금지. 
               - 문구 예시: "브랜드의 보이스앤톤을 직접 설계해보고 싶은 분", "독립 잡지 출판의 전 과정을 경험하고 싶은 분", "텍스트 기반 커뮤니티의 운영 로직을 배우고 싶은 분" 등 직무적 성장과 연결할 것.
               - 끝맺음: "~한 분" (예: ~하는 분, ~를 찾는 분)
               - 주의사항 : "에디터"라는 말을 직접 사용하지 말 것. 
               - 주의사항 : 각 불릿에는 반드시 하나의 문장만 포함해 주세요.
            3. inferred_location: 본문을 분석하여 '활동 지역' 추출 (예: 서울 강남, 온라인 등).
            
            어투: 매우 정중하고 지적인 경어체 (~합니다).
            [내용] {truncated_text}
            """
            summary_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[
                    {"role": "system", "content": "Respond only in JSON format with keys: inferred_role, inferred_location, summary(list), recommendations(list)."},
                    {"role": "user", "content": summary_prompt}
                ]
            )
            gpt_res = json.loads(summary_res.choices[0].message.content)
            
            final_location = sheet_location if sheet_location else gpt_res.get('inferred_location', '온라인 (협의 가능)')
            
            # 5. 슬랙 전송
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": "🤝사이드프로젝트 동료 찾고 있어요", "emoji": True}},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"* {project_title}*"}},
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*모집 포지션*\n콘텐츠 기획자"},
                        {"type": "mrkdwn", "text": f"*지역*\n{final_location}"}
                    ]
                },
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "📌 *프로젝트 요약*\n" + "\n".join([f"• {s}" for s in gpt_res.get('summary', [])])}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "📌 *이런 분께 추천해요*\n" + "\n".join([f"• {r}" for r in gpt_res.get('recommendations', [])])}},
                {"type": "divider"},
                {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "프로젝트 보러가기", "emoji": True}, "style": "primary", "url": target_url}]}
            ]
            
            slack_resp = requests.post(webhook_url, json={"blocks": blocks})
            
            if slack_resp.status_code == 200:
                print(f"✅ 전송 성공: {project_title}")
                time.sleep(1)
                sheet.update_cell(update_row_index, status_col_idx, 'published')
            else:
                print(f"❌ 슬랙 전송 실패: {slack_resp.status_code}")
                sheet.update_cell(update_row_index, status_col_idx, 'failed')

            time.sleep(1.5)

        except Exception as e:
            print(f"❌ {update_row_index}행 처리 오류: {e}")
            if "429" in str(e): 
                time.sleep(60)
            continue

except Exception as e:
    print(f"❌ 치명적 오류: {e}")
    had_fatal_error = True
finally:
    print("--- [Letspl Sender] 모든 프로세스가 종료되었습니다 ---")

if had_fatal_error:
    sys.exit(1)
