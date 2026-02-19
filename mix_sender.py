import os
import sys
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
import time
import random

# =========================================================
# 1. 설정 및 인증
# =========================================================
had_fatal_error = False
try:
    print("--- [Mix Sender] 프로세스를 시작합니다 ---")

    if 'GOOGLE_CREDENTIALS' not in os.environ:
        raise Exception("환경변수 GOOGLE_CREDENTIALS가 설정되지 않았습니다.")

    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    spreadsheet = client.open('플린트스토닝 소재 DB')
    
    # gid(981623942) 기반 워크시트 찾기
    TARGET_GID = 981623942
    sheet = next((s for s in spreadsheet.worksheets() if s.id == TARGET_GID), None)
    
    if not sheet:
        raise Exception(f"GID가 {TARGET_GID}인 워크시트를 찾을 수 없습니다.")
    
    data = sheet.get_all_values()
    headers = [h.strip() for h in data[0]]
    df = pd.DataFrame(data[1:], columns=headers)

    COL_STATUS = 'status'
    COL_IDENTITY = 'identity_match'
    COL_TITLE = 'title'
    COL_URL = 'url'

    # 'archived' 상태인 모든 행 추출
    target_rows = df[df[COL_STATUS].str.strip().str.lower() == 'archived']

    if target_rows.empty:
        print("ℹ️ 'archived' 상태의 아티클이 현재 시트에 없습니다.")
        exit()

    print(f"총 {len(target_rows)}건의 아티클 처리를 시작합니다.")

    identity_col_idx = headers.index(COL_IDENTITY) + 1
    status_col_idx = headers.index(COL_STATUS) + 1
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
    webhook_url = os.environ['SLACK_INSIGHT']

    # =========================================================
    # 2. 메인 루프
    # =========================================================
    for index, row in target_rows.iterrows():
        update_row_index = int(index) + 2
        project_title = row[COL_TITLE]
        target_url = row[COL_URL]
        
        print(f"\n🔍 {update_row_index}행 검토 중: {project_title}")

        try:
            # 3. 웹 스크래핑
            headers_ua = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            time.sleep(random.uniform(2.0, 4.0))
            
            resp = requests.get(target_url, headers=headers_ua, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            paragraphs = soup.find_all(['p', 'h2', 'h3'])
            text_content = " ".join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
            truncated_text = text_content[:3500]

            # 4. ANTIEGG 정체성 판단
            identity_prompt = f"""
            당신은 'ANTIEGG'의 편집장입니다. 내용을 읽고 ANTIEGG의 정체성에 부합하는지 판단해 주세요.

            [적합 조건 (TRUE)]
            필수 주제 (다음 중 하나라도 직접적인 관련이 있어야 합니다):
               - 콘텐츠 마케팅: 브랜드 전략, 비평 등
               - 글쓰기: 스토리텔링, 에디팅 스킬, 에디터의 성장 인사이트 등
               - 브랜드: 브랜드 정체성, 브랜딩 사례, 브랜드 간 협업 등
               - 문화: 문화예술 트렌드, 사회적 현상에 대한 담론, 라이프스타일 분석, 디자인 등
               - 일: 커리어, 인사이트, 일 잘하는 방법 등
               - AI: ai, 자동화, 프롬프트, 에이전트 등

            [사례 학습 (Few-Shot)]
            - ✅ 적합: '네이버와 돌고래유괴단 협업', '제로클릭 시대의 마케팅', '마케터의 커뮤니티 운영 회고', '클로드 코드로 블로그 글 자동화 에이전트 만들기', '현실과 결합! 이게 정말 AI 딸깍이라고?', '디자인에 진심인 하인즈'.
            - ❌ 부적합: '채팅 상담 개선기(UX/CS)', '무인 창업 아이템 추천', '단순 앱 프로젝트 성공기', '단순 채용 공고', '기업 성과 보도자료', '인플루언서'.

            [글 내용]
            {truncated_text}
            """
            
            check_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[
                    {"role": "system", "content": "You are a professional editor. Respond only in json format with keys: 'is_appropriate' (boolean), 'reason' (string)."},
                    {"role": "user", "content": identity_prompt}
                ]
            )
            judgment = json.loads(check_res.choices[0].message.content)
            is_appropriate = judgment.get("is_appropriate", False)
            drop_reason = judgment.get("reason", "사유 미상")
            
            # identity_match 업데이트
            sheet.update_cell(update_row_index, identity_col_idx, str(is_appropriate).upper())

            # [수정] 부적합 시 상세 로그 출력 후 skip
            if not is_appropriate:
                print("-" * 60)
                print(f"🚫 [DROP] 부적합 아티클: {project_title}")
                print(f"   ㄴ 사유: {drop_reason}")
                print("-" * 60)
                sheet.update_cell(update_row_index, status_col_idx, 'dropped')
                continue

            # 5. 슬랙 메시지 생성
            summary_prompt = f"""
            당신은 ANTIEGG의 인사이트 큐레이터입니다. 지적이고 세련된 어투로 아래 글을 소개해 주세요.
            어투는 매우 정중하고 지적인 경어체 (~합니다, ~해드립니다)를 사용해 주세요. 
            JSON 포맷으로 만들어 주세요. 
            [지침]:
            1. key_points: 본문의 핵심 맥락을 짚어주는 문장을 3개 내외로 작성해 주세요.
               - 주의사항 : 모든 문장은 ‘글’ 자체를 설명하는 메타 시점에서 작성해 주세요. 
               - 첫 번째 문장 : 반드시 ‘이 글은~’을 주어로 시작해 주세요.
               - 첫 번째 문장, 이후 : 주어를 생략하고, 앞 문맥을 자연스럽게 이어 주세요.
               - 주의사항 : 각 불릿에는 반드시 하나의 문장만 포함해 주세요.
            2. recommendations: 이 글이 꼭 필요한 에디터를 3가지 내외의 유형으로 제안해 주세요. 
               - 문구 예시: "새로운 브랜드 스토리텔링 방식을 고민하는 분", "글의 깊이를 더할 문화적 관점이 필요한 분"
               - 끝맺음: "~한 분" (예: ~하는 분, ~를 찾는 분)
               - 주의사항 : "에디터"라는 말을 직접 사용하지 말 것. 
               - 주의사항 : 각 불릿에는 반드시 하나의 문장만 포함해 주세요.
            
            [글 내용]
            {truncated_text}
            """
            
            summary_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[
                    {"role": "system", "content": "Respond only in json format with keys: 'key_points', 'recommendations' (lists)."},
                    {"role": "user", "content": summary_prompt}
                ]
            )
            gpt_res = json.loads(summary_res.choices[0].message.content)
            
            # 6. 슬랙 전송
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": "💡지금 주목해야 할 아티클", "emoji": True}},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*{project_title}*"}},
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "📌 *이 글에서 이야기하는 것들*\n" + "\n".join([f"• {p}" for p in gpt_res.get('key_points', [])])}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "📌 *이런 분께 추천해요*\n" + "\n".join([f"• {p}" for p in gpt_res.get('recommendations', [])])}},
                {"type": "divider"},
                {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "아티클 보러가기", "emoji": True}, "style": "primary", "url": target_url}]}
            ]
            
            slack_payload = {
                "blocks": blocks,
                "icon_emoji": ":fried_egg:",
                "username": "에그서치봇"
            }

            slack_resp = requests.post(webhook_url, json=slack_payload)

            if slack_resp.status_code == 200:
                print(f"✅ 전송 성공: {project_title}")
                sheet.update_cell(update_row_index, status_col_idx, 'published')
            else:
                print(f"❌ 전송 실패 ({slack_resp.status_code}): {project_title}")
                sheet.update_cell(update_row_index, status_col_idx, 'failed')

            time.sleep(1) 

        except Exception as e:
            print(f"❌ {update_row_index}행 처리 오류: {e}")
            sheet.update_cell(update_row_index, status_col_idx, 'failed')
            continue

except Exception as e:
    print(f"❌ 치명적 오류: {e}")
    had_fatal_error = True
finally:
    print("--- [Mix Sender] 모든 프로세스가 종료되었습니다 ---")

if had_fatal_error:
    sys.exit(1)
