import os
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
try:
    print("--- [Recruit Sender] 채용 공고 프로세스를 시작합니다 ---")
    
    if 'GOOGLE_CREDENTIALS' not in os.environ:
        raise Exception("환경변수 GOOGLE_CREDENTIALS가 설정되지 않았습니다.")

    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    spreadsheet = client.open('플린트스토닝 소재 DB')
    
    TARGET_GID = 639559541
    sheet = next((s for s in spreadsheet.worksheets() if s.id == TARGET_GID), None)
    
    if not sheet:
        raise Exception(f"GID가 {TARGET_GID}인 워크시트를 찾을 수 없습니다.")
    
    data = sheet.get_all_values()
    headers = [h.strip() for h in data[0]]
    df = pd.DataFrame(data[1:], columns=headers)

    # 컬럼 설정
    COL_STATUS = 'status'
    COL_IDENTITY = 'identity_match'
    COL_TITLE = 'title'     
    COL_URL = 'url'         
    COL_LOCATION = 'location' 
    COL_EXPERIENCE = 'experience'
    COL_COMPANY = 'company' # 회사명 컬럼 추가

    target_rows = df[df[COL_STATUS].str.strip().str.lower() == 'archived']

    if target_rows.empty:
        print("ℹ️ 'archived' 상태의 공고가 없습니다.")
        exit()

    identity_col_idx = headers.index(COL_IDENTITY) + 1
    status_col_idx = headers.index(COL_STATUS) + 1
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
    webhook_url = os.environ['SLACK_WEBHOOK_URL']
    
    session = requests.Session()

    # =========================================================
    # 2. 메인 루프
    # =========================================================
    for index, row in target_rows.iterrows():
        update_row_index = int(index) + 2
        raw_title = row[COL_TITLE]
        target_url = row[COL_URL]
        
        # 시트 데이터 직접 참조 (회사명, 지역, 경력)
        sheet_company = row.get(COL_COMPANY, "회사명 미상").strip() or "회사명 미상"
        sheet_location = row.get(COL_LOCATION, "정보 없음").strip() or "정보 없음"
        sheet_experience = row.get(COL_EXPERIENCE, "경력 무관").strip() or "경력 무관"
        
        print(f"\n🔍 {update_row_index}행 검토 중: {raw_title}")

        try:
            # 3. 차단 우회 헤더
            headers_ua = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Referer': 'https://www.google.com/',
            }

            time.sleep(random.uniform(2.5, 4.5))
            resp = session.get(target_url, headers=headers_ua, timeout=15)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            text_content = " ".join([p.get_text().strip() for p in soup.find_all(['p', 'h2', 'h3', 'li', 'span', 'div']) if len(p.get_text().strip()) > 10])
            truncated_text = text_content[:3800]

            # 4. 적합성 판단
            identity_prompt = f"""
            당신은 에디터 공동체의 커리어 큐레이터입니다. 아래 글이 '콘텐츠 에디터/기획자' 채용 공고인지 판단하세요.
            [내용] {truncated_text}
            출력 포맷(JSON): {{"is_appropriate": true/false}}
            """
            check_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[{"role": "user", "content": identity_prompt}]
            )
            judgment = json.loads(check_res.choices[0].message.content)
            
            time.sleep(1)
            sheet.update_cell(update_row_index, identity_col_idx, str(judgment['is_appropriate']).upper())

            if not judgment.get('is_appropriate', False):
                print(f"⚠️ 부적합 판정 스킵.")
                continue

            # 5. 슬랙 내용 생성 (원문 유지 및 경력 수치 제외)
            summary_prompt = f"""
            동료 에디터들을 위해 채용 공고 요약을 작성해 주세요. 
            [지침]:
            1. job_name: 본문에서 핵심 직무명을 찾아 짧게 작성하세요 (예: 콘텐츠 에디터).
            2. roles, requirements, preferences, recommendations: 
               - 반드시 원문에 있는 표현을 최대한 그대로 사용하세요.
               - **필수 지침**: 'requirements(요구 역량)' 항목에서 "경력 00년 이상", "N년 이상의 경험" 등 경력/기간과 관련된 모든 수치 표현은 반드시 제외하고 실무 역량만 포함하세요.
               - 각 항목은 3개 내외의 리스트로 구성하세요.
               - 'recommendations' 항목은 에디터에게 추천하는 이유 3가지 (끝맺음: "~한 분", '에디터' 단어 사용 금지)로 구성하세요. 
            
            [내용] {truncated_text}
            출력 포맷(JSON): {{"job_name": "", "roles": [], "requirements": [], "preferences": [], "recommendations": []}}
            """
            summary_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[{"role": "user", "content": summary_prompt}]
            )
            gpt_res = json.loads(summary_res.choices[0].message.content)
            
            # 제목 형식: [회사명] 직무명
            display_title = f"[{sheet_company}] {gpt_res.get('job_name', '콘텐츠 기획자')}"
            
            # 6. 슬랙 전송
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "*오늘 올라온 채용 공고*"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*{display_title}*"}},
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*지역*\n{sheet_location}"},
                        {"type": "mrkdwn", "text": f"*경력*\n{sheet_experience}"}
                    ]
                },
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "📌 *주요 역할*\n" + "\n".join([f"• {r}" for r in gpt_res.get('roles', [])])}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "📌 *요구 역량*\n" + "\n".join([f"• {req}" for req in gpt_res.get('requirements', [])])}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "📌 *우대 사항*\n" + "\n".join([f"• {p}" for p in gpt_res.get('preferences', [])])}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "📌 *이런 분께 추천해요*\n" + "\n".join([f"• {rec}" for rec in gpt_res.get('recommendations', [])])}},
                {"type": "divider"},
                {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "상세 공고 보러가기", "emoji": True}, "style": "primary", "url": target_url}]}
            ]
            
            requests.post(webhook_url, json={"blocks": blocks})
            
            time.sleep(1)
            sheet.update_cell(update_row_index, status_col_idx, 'published')
            print(f"✅ 전송 성공: {display_title}")
            break 

        except Exception as e:
            print(f"❌ 처리 오류: {e}")
            continue

except Exception as e:
    print(f"❌ 치명적 오류: {e}")
finally:
    print("--- 모든 프로세스가 종료되었습니다 ---")
