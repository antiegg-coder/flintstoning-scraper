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
    print("--- [Side Sender] 프로세스를 시작합니다 ---")
    
    if 'GOOGLE_CREDENTIALS' not in os.environ:
        raise Exception("환경변수 GOOGLE_CREDENTIALS가 설정되지 않았습니다.")

    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    spreadsheet = client.open('플린트스토닝 소재 DB')
    
    # GID 1818966683 기반 시트 선택
    TARGET_GID = 1818966683
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
    COL_LOCATION = 'location' # 지역은 컬럼에서 불러옵니다.

    target_rows = df[df[COL_STATUS].str.strip().str.lower() == 'archived']

    if target_rows.empty:
        print("ℹ️ 'archived' 상태의 프로젝트가 없습니다.")
        exit()

    identity_col_idx = headers.index(COL_IDENTITY) + 1
    status_col_idx = headers.index(COL_STATUS) + 1
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
    webhook_url = os.environ['SLACK_WEBHOOK_URL']

    # =========================================================
    # 2. 메인 루프
    # =========================================================
    for index, row in target_rows.iterrows():
        update_row_index = int(index) + 2
        project_title = row[COL_TITLE]
        target_url = row[COL_URL]
        project_location = row.get(COL_LOCATION, "온라인 (협의 가능)") # 시트에서 지역 불러오기
        
        print(f"\n🔍 {update_row_index}행 검토 중: {project_title}")

        try:
            # 3. 웹 스크래핑
            headers_ua = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(target_url, headers=headers_ua, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            text_content = " ".join([p.get_text().strip() for p in soup.find_all(['p', 'h2', 'h3']) if len(p.get_text().strip()) > 20])
            truncated_text = text_content[:3500]

            # 4. 적합성 판단 (에디터/콘텐츠 연관성 엄격 적용)
            identity_prompt = f"""
            안녕하세요, 당신은 에디터 공동체 'ANTIEGG'의 프로젝트 큐레이터입니다. 
            아래 프로젝트가 에디터들이 참여하기 적합한 '콘텐츠 관련 사이드 프로젝트'인지 판단해 주세요.

            [판단 기준]
            - 필수 조건: 구체적인 결과물이 있는 '사이드 프로젝트'인가?
            - 선택 조건: 글 쓰는 에디터, 스토리텔링, 또는 콘텐츠 제작과 직접적인 연관이 있는가? 

            [글 내용] {truncated_text}
            출력 포맷(JSON): {{"is_appropriate": true/false, "reason": "이유 설명"}}
            """
            check_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[{"role": "user", "content": identity_prompt}]
            )
            judgment = json.loads(check_res.choices[0].message.content)
            
            time.sleep(1.5)
            sheet.update_cell(update_row_index, identity_col_idx, str(judgment['is_appropriate']).upper())

            if not judgment['is_appropriate']:
                print(f"⚠️ 부적합: {judgment.get('reason')}")
                continue

            # 5. 슬랙 메시지 내용 생성 (모집 포지션 추론 포함)
            # [5. 슬랙 메시지 생성 프롬프트 최종 수정]
            summary_prompt = f"""
            당신은 ANTIEGG의 프로젝트 큐레이터입니다. 동료들에게 이 프로젝트를 세련되게 소개해 주세요.
            
            1. inferred_role: 본문을 분석하여 에디터가 맡을 수 있는 가장 적합한 '모집 포지션'을 한 단어로 추출해 주세요.
            2. summary: 프로젝트의 정체성과 핵심 기능을 설명하는 2개의 문장을 작성해 주세요. 
               - **주의**: 'ANTIEGG는~'로 시작하지 마세요. 프로젝트 자체를 주어로 하거나 문장형으로 작성해 주세요.
            3. recommendations: 이 프로젝트가 영감을 주거나 필요한 이유 3가지를 제안해 주세요.
               - **주의**: 문구 안에 '에디터'라는 단어를 직접 넣지 마세요. 
               - 대신 콘텐츠 기획, 브랜딩, 글쓰기 등 직무적 고민이 느껴지도록 작성해 주세요.
               - 끝맺음: "~한 분"으로 통일해 주세요.
            4. inferred_location: 본문을 분석하여 '활동 지역' 추출. (온라인/오프라인 여부 포함)
            
            어투: 매우 정중하고 지적인 경어체 (~합니다).
            [글 내용] {truncated_text}
            출력 포맷(JSON): {{"inferred_role": "", "inferred_location": "", "summary": [], "recommendations": []}}
            """
            summary_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[{"role": "user", "content": summary_prompt}]
            )
            gpt_res = json.loads(summary_res.choices[0].message.content)
            
            # [지역 정보 결정] 시트에 있으면 시트값, 없으면 GPT 추론값 사용
            final_location = sheet_location if sheet_location else gpt_res.get('inferred_location', '온라인 (협의)')
            
            # 6. 슬랙 전송 (이미지 UI 완벽 재현)
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "*사이드프로젝트 동료 찾고 있어요*"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"* {project_title}* ┃ *팀원 모집*"}},
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*모집 포지션*\n{gpt_res.get('inferred_role', '콘텐츠 기획자')}"},
                        {"type": "mrkdwn", "text": f"*지역*\n{project_location}"} # 시트에서 가져온 데이터 적용
                    ]
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "📌 *프로젝트 요약*\n" + "\n".join([f"• {s}" for s in gpt_res.get('summary', [])])}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "📌 *이런 분께 추천해요*\n" + "\n".join([f"• {r}" for r in gpt_res.get('recommendations', [])])}
                },
                {"type": "divider"},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "프로젝트 보러가기", "emoji": True},
                            "style": "primary",
                            "url": target_url
                        }
                    ]
                }
            ]
            
            slack_resp = requests.post(webhook_url, json={"blocks": blocks})

            if slack_resp.status_code == 200:
                print("✅ 슬랙 전송 성공!")
                time.sleep(1.5)
                sheet.update_cell(update_row_index, status_col_idx, 'published')
                break 
            else:
                print(f"❌ 실패: {slack_resp.status_code}")
                break

        except Exception as e:
            print(f"❌ 오류: {e}")
            continue

except Exception as e:
    print(f"❌ 치명적 오류: {e}")
finally:
    print("--- 모든 프로세스가 종료되었습니다 ---")
