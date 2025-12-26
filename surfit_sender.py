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
    print("--- [Mix Sender] 프로세스를 시작합니다 ---")
    
    if 'GOOGLE_CREDENTIALS' not in os.environ:
        raise Exception("환경변수 GOOGLE_CREDENTIALS가 설정되지 않았습니다.")

    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # 스프레드시트 열기
    spreadsheet = client.open('플린트스토닝 소재 DB')
    
    # [수정 사항 1] gid(2112710663)를 기반으로 워크시트 찾기
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
            # 3. 웹 스크래핑
            headers_ua = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(target_url, headers=headers_ua, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            paragraphs = soup.find_all(['p', 'h2', 'h3'])
            text_content = " ".join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
            truncated_text = text_content[:3500]

            # 4. ANTIEGG 정체성 판단
            identity_prompt = f"""
            안녕하세요, 당신은 프리랜서 에디터 공동체 'ANTIEGG'의 편집장입니다. 
            아래 내용을 읽고 ANTIEGG의 정체성에 부합하는지 매우 엄격하게 판단해 주세요.

            [판단 기준]
            1. 필수 주제 (다음 중 하나라도 직접적인 관련이 있어야 합니다):
               - 콘텐츠 마케팅: 브랜드 전략, 비평 등
               - 글쓰기: 스토리텔링, 에디팅 스킬, 에디터의 성장 인사이트 등
               - 브랜드: 브랜드 정체성, 브랜딩 사례, 브랜드 간 협업 등
               - 문화: 문화예술 트렌드, 사회적 현상에 대한 담론, 라이프스타일 분석 등
            2. 필수 가치: '연대와 커뮤니티의 가치'가 담겨 있나요? (함께 토론할 만한 담론형 주제)

            [사례 학습 (Few-Shot)]
            - ✅ 적합: '네이버와 돌고래유괴단 협업', '제로클릭 시대의 마케팅', '마케터의 커뮤니티 운영 회고'.
            - ❌ 부적합: '채팅 상담 개선기(UX/CS)', '무인 창업 아이템 추천', '단순 앱 프로젝트 성공기', '단순 채용 공고', '기업 성과 보도자료'.

            [글 내용]
            {truncated_text}

            출력 포맷(JSON): {{"is_appropriate": true/false, "reason": "위 기준과 사례를 바탕으로 판단 이유를 정중하게 설명해 주세요."}}
            """
            check_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[{"role": "system", "content": "당신은 ANTIEGG의 정체성을 수호하는 엄격하고 전문적인 편집장입니다."},
                          {"role": "user", "content": identity_prompt}]
            )
            judgment = json.loads(check_res.choices[0].message.content)
            is_appropriate = judgment.get("is_appropriate", False)
            
            sheet.update_cell(update_row_index, identity_col_idx, str(is_appropriate).upper())

            if not is_appropriate:
                print(f"⚠️ 부적합 판정: {judgment.get('reason')}")
                continue

            # 5. 슬랙 메시지 생성
            print(f"✨ 적합 판정: 요약 메시지 생성을 시작합니다.")
            
            # [수정 사항 2] '에디터'를 중심으로 한 추천사 생성 로직 반영
            summary_prompt = f"""
            당신은 ANTIEGG의 인사이트 큐레이터입니다. 지적이고 세련된 어투로 아래 글을 소개해 주세요.

            1. key_points: 본문의 핵심 맥락을 짚어주는 4개의 문장을 작성해 주세요.
            2. recommendations: 이 글이 꼭 필요한 에디터를 3가지 유형으로 제안해 주세요. 
               - **핵심 지침**: 추천 대상은 반드시 '에디터'의 업무, 고민, 성장과 연결되어야 합니다.
               - 추천 문구 예시: "새로운 브랜드 스토리텔링 방식을 고민하는 분", "글의 깊이를 더할 문화적 관점이 필요한 분"
               - 추천 대상 끝맺음: "~한 분" (예: ~하는 분, ~를 찾는 분)
               - 주의: 기업 리소스 효율화 관련 내용은 제외해 주세요.

            어투: 매우 정중하고 지적인 경어체 (~합니다, ~해드립니다).
            [글 내용]
            {truncated_text}

            출력 포맷(JSON): {{"key_points": [], "recommendations": []}}
            """
            
            summary_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[{"role": "system", "content": "당신은 지적이고 다정한 ANTIEGG의 큐레이터입니다. 모든 추천은 동료 에디터를 향합니다."},
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
                sheet.update_cell(update_row_index, status_col_idx, 'published')
                break 
            else:
                print(f"❌ 전송 실패 (에러: {slack_resp.status_code})")
                sheet.update_cell(update_row_index, status_col_idx, 'failed')
                break

        except Exception as e:
            print(f"❌ 오류 발생: {e}")
            sheet.update_cell(update_row_index, status_col_idx, 'failed')
            continue

except Exception as e:
    print(f"❌ 치명적 오류: {e}")
finally:
    print("--- [Mix Sender] 프로세스가 종료되었습니다 ---")
