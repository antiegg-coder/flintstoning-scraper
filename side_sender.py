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
    COL_LOCATION = 'location' 

    target_rows = df[df[COL_STATUS].str.strip().str.lower() == 'archived']

    if target_rows.empty:
        print("ℹ️ 'archived' 상태의 프로젝트가 없습니다.")
        exit()

    identity_col_idx = headers.index(COL_IDENTITY) + 1
    status_col_idx = headers.index(COL_STATUS) + 1
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
    webhook_url = os.environ['SLACK_WEBHOOK_URL']
    session = requests.Session()

    # =========================================================
    # 2. 메인 루프: 적합한 프로젝트를 찾을 때까지 반복
    # =========================================================
    for index, row in target_rows.iterrows():
        update_row_index = int(index) + 2
        project_title = row[COL_TITLE]
        target_url = row[COL_URL]
        
        # 시트 내 지역 정보 확보 (공백 제거)
        sheet_location = row.get(COL_LOCATION, "").strip() 
        
        print(f"\n🔍 {update_row_index}행 검토 중: {project_title}")

        try:
            # -------------------------------------------------------
            # 3. [403 Forbidden 해결] 강력한 브라우저 위장 및 랜덤 대기
            # -------------------------------------------------------
            headers_ua = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
                'Referer': 'https://www.google.com/',  # 구글 유입으로 위장
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'cross-site',
            }

            # 봇 감지 방지를 위해 2.5 ~ 4.5초 사이 랜덤 대기
            time.sleep(random.uniform(2.5, 4.5))

            resp = session.get(target_url, headers=headers_ua, timeout=15)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            # 텍스트 추출 범위 확대 (li 태그 등 포함)
            text_content = " ".join([p.get_text().strip() for p in soup.find_all(['p', 'h2', 'h3', 'li', 'span']) if len(p.get_text().strip()) > 10])
            truncated_text = text_content[:3500]

            # -------------------------------------------------------
            # 4. [적합성 판단] 에디팅/글쓰기 포지션 모집 여부 엄격 필터링
            # -------------------------------------------------------
            identity_prompt = f"""
            안녕하세요, 당신은 에디터 공동체 'ANTIEGG'의 프로젝트 큐레이터입니다. 
            아래 프로젝트가 에디터들이 참여하기 적합한 '콘텐츠 관련 사이드 프로젝트'인지 판단해 주세요.

            [판단 기준]
            1. 프로젝트 자체의 성격보다 **'모집 중인 역할(Role)'**이 중요합니다.
            2. 에디터, 콘텐츠 마케터, 작가, 뉴스레터 기획자, 스토리 작가, 교정교열 등 '텍스트'와 '콘텐츠' 중심의 포지션이 없다면 탈락시키세요.
            3. 단순히 개발자, 디자이너만 모집하는 프로젝트는 FALSE를 반환하세요.


            [내용] {truncated_text}
            출력 포맷(JSON): {{"is_appropriate": true/false, "reason": "모집 포지션 기반의 판단 이유"}}
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
                print(f"⚠️ 에디팅 포지션 없음 (탈락): {judgment.get('reason')}")
                continue

            # -------------------------------------------------------
            # 5. [슬랙 생성] 에디터 맞춤형 추천사 (지역/직무 추론 포함)
            # -------------------------------------------------------
            summary_prompt = f"""
            당신은 ANTIEGG의 프로젝트 큐레이터입니다. 동료들에게 이 프로젝트를 세련되게 소개해 주세요.
            
            1. inferred_role: 본문을 분석하여 에디터가 맡을 수 있는 가장 적합한 '모집 포지션'을 한 단어로 추출해 주세요.
            2. summary: 프로젝트의 정체성과 핵심 기능을 설명하는 2개의 문장을 작성해 주세요. 
               - **주의**: 'ANTIEGG는~'로 시작하지 마세요. 프로젝트 자체를 주어로 하거나 문장형으로 작성해 주세요.
            4. recommendations: 에디터들에게 구미가 당길만한 구체적인 이유 3가지. 
               - **지침**: '열심히 할 분' 같은 일반적인 말은 금지. 
               - **예시**: "브랜드의 보이스앤톤을 직접 설계해보고 싶은 분", "독립 잡지 출판의 전 과정을 경험하고 싶은 분", "텍스트 기반 커뮤니티의 운영 로직을 배우고 싶은 분" 등 직무적 성장과 연결할 것.
               - 문구 내 '에디터' 단어 직접 사용 금지, 끝맺음은 "~한 분"으로 통일.
            4. inferred_location: 본문을 분석하여 '활동 지역' 추출 (예: 서울 강남, 온라인 등).
            
            어투: 매우 정중하고 지적인 경어체 (~합니다).
            [내용] {truncated_text}
            출력 포맷(JSON): {{"inferred_role": "", "inferred_location": "", "summary": [], "recommendations": []}}
            """
            summary_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[{"role": "user", "content": summary_prompt}]
            )
            gpt_res = json.loads(summary_res.choices[0].message.content)
            
            # [수정] 지역 정보 결정: 시트값 우선 -> 없으면 GPT 추론값
            final_location = sheet_location if sheet_location else gpt_res.get('inferred_location', '온라인 (협의 가능)')
            
            # -------------------------------------------------------
            # 6. 슬랙 전송 (이미지 UI 재현)
            # -------------------------------------------------------
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "*사이드프로젝트 동료 찾고 있어요*"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"* {project_title}* ┃ *팀원 모집*"}},
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*모집 포지션*\n{gpt_res.get('inferred_role', '콘텐츠 기획자')}"},
                        {"type": "mrkdwn", "text": f"*지역*\n{final_location}"}
                    ]
                },
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "📌 *프로젝트 요약*\n" + "\n".join([f"• {s}" for s in gpt_res.get('summary', [])])}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "📌 *이런 분께 추천해요*\n" + "\n".join([f"• {r}" for r in gpt_res.get('recommendations', [])])}},
                {"type": "divider"},
                {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "프로젝트 보러가기", "emoji": True}, "style": "primary", "url": target_url}]}
            ]
            
            requests.post(webhook_url, json={"blocks": blocks})
            
            # 성공 시 상태 업데이트 및 루프 종료(한 번에 하나씩 전송 시)
            time.sleep(1.5)
            sheet.update_cell(update_row_index, status_col_idx, 'published')
            print(f"✅ 전송 성공: {project_title}")
            break 

        except Exception as e:
            print(f"❌ {update_row_index}행 처리 오류: {e}")
            continue
