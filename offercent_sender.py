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
import re

# =========================================================
# 1. 설정 및 인증
# =========================================================
had_fatal_error = False
try:
    print("--- [Recruit Sender] 전체 자동화 프로세스를 시작합니다 ---")

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
        raise Exception(f"GID {TARGET_GID} 시트를 찾을 수 없습니다.")
    
    data = sheet.get_all_values()
    headers = [h.strip() for h in data[0]]
    df = pd.DataFrame(data[1:], columns=headers)

    COL_STATUS = 'status'
    COL_IDENTITY = 'identity_match'
    COL_TITLE = 'title'     
    COL_URL = 'url'          
    COL_LOCATION = 'location' 
    COL_EXPERIENCE = 'experience'
    COL_COMPANY = 'company'

    # 'archived' 상태인 모든 행 추출
    target_rows = df[df[COL_STATUS].str.strip().str.lower() == 'archived']

    if target_rows.empty:
        print("ℹ️ 처리할 'archived' 상태의 공고가 없습니다.")
        exit()

    print(f"총 {len(target_rows)}건의 공고를 순차적으로 처리합니다.")

    identity_col_idx = headers.index(COL_IDENTITY) + 1
    status_col_idx = headers.index(COL_STATUS) + 1
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
    webhook_url = os.environ['SLACK_MOJIPGONGGO']
    
    session = requests.Session()

    # =========================================================
    # 2. 메인 루프 (모든 행 순회)
    # =========================================================
    for index, row in target_rows.iterrows():
        update_row_index = int(index) + 2
        
        # 제목 정제 없이 스프레드시트의 원본 제목 그대로 사용
        original_title = row[COL_TITLE].strip()
        
        target_url = row[COL_URL]
        sheet_company = row.get(COL_COMPANY, "회사명 미상").strip() or "회사명 미상"
        sheet_location = row.get(COL_LOCATION, "정보 없음").strip() or "정보 없음"
        sheet_experience = row.get(COL_EXPERIENCE, "경력 무관").strip() or "경력 무관"
        
        print(f"\n🔍 {update_row_index}행 검토 중: {original_title}")

        try:
            # 3. [차단 우회] 브라우저 위장 헤더
            headers_ua = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Referer': 'https://www.google.com/',
            }

            time.sleep(random.uniform(3.0, 6.0))
            
            resp = session.get(target_url, headers=headers_ua, timeout=15)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, 'html.parser')
            text_content = " ".join([p.get_text().strip() for p in soup.find_all(['p', 'h2', 'h3', 'li', 'span', 'div']) if len(p.get_text().strip()) > 10])
            truncated_text = text_content[:3500]

            # 4. [적합성 판단] 사례 학습 포함
            identity_prompt = f"""
            당신은 에디터 공동체 'ANTIEGG'의 채용 큐레이터입니다. 아래 채용 공고가 ANTIEGG 기준의 ‘에디팅 직무’에 해당하는지 판단하세요.

            [적합 조건 (TRUE)]
            - 아래 키워드 중 하나라도 직무명 또는 주요 업무에 포함되어 있으면 기본적으로 적합(TRUE)으로 판단합니다.
                - 마케터
                - 마케팅
                - 콘텐츠
                - 브랜드
            - 단, 부적합 조건에 해당하면 예외적으로 FALSE 처리합니다.
            
            [부적합 조건 (FALSE)]
            - 채용 목적이 아닌 사이드 프로젝트, 커뮤니티 모집
            
            [사례 학습 (Few-Shot)]
            - ✅ 적합: '[린다이어트] 브랜드 마케터 인턴', '[무무키] 콘텐츠마케터 포지션 (신입/경력)', '[인턴/신입] 국가별 콘텐츠 마케터 인턴 (한국/일본/미국)'. 
            - ❌ 부적합: '[라비킷/에르고바디] 촬영 스타일리스트', '[메이크스타] 커머스 운영/상품등록 담당자 (중국어)'. 

            [내용] {truncated_text}
            """
            
            check_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[
                    {"role": "system", "content": "You are a job analyst. Respond only in json format with key 'is_appropriate' (boolean)."},
                    {"role": "user", "content": identity_prompt}
                ]
            )
            is_appropriate = json.loads(check_res.choices[0].message.content).get('is_appropriate', False)
            
            # identity_match 컬럼 업데이트
            sheet.update_cell(update_row_index, identity_col_idx, str(is_appropriate).upper())

            # 적합성 판단 결과가 FALSE인 경우
            if not is_appropriate:
                print(f"⚠️ 부적합 공고 판단: status를 'dropped'로 변경합니다.")
                sheet.update_cell(update_row_index, status_col_idx, 'dropped')
                continue

            # 5. [요약 생성] 프롬프트 전문 유지
            summary_prompt = f"""
            당신은 ANTIEGG의 채용 큐레이터입니다. 지적이고 세련된 어투로 아래 글을 소개해 주세요.
            어투는 매우 정중하고 지적인 경어체 (~합니다, ~해드립니다)를 사용해 주세요. 
            JSON 포맷으로 만들어 주세요. 
            [지침]:
                1. roles: 해당 포지션에서 실제로 수행하게 될 역할을 3개 내외로 작성해 주세요.
                    - 어휘와 표현 : 원문에 사용된 표현과 어휘를 최대한 그대로 유지해 주세요.
                    - 주의사항 : 각 불릿에는 반드시 하나의 문장만 포함해 주세요.
                2. requirements: 이 역할을 수행하기 위해 최소한으로 요구되는 조건을 3개 내외로 작성해 주세요.
                    - 어휘와 표현 : 원문에 사용된 표현과 어휘를 최대한 그대로 유지해 주세요.
                    - 경력 삭제 : “N년 이상”, “경력 ○년” 등 모든 숫자 형태의 경력 요건은 반드시 삭제해 주세요.
                    - 주의사항 : 각 불릿에는 반드시 하나의 문장만 포함해 주세요.
                3. preferences: 필수는 아니지만, 있을 경우 더 잘 맞는 성향·경험·업무 맥락을 3개 내외로 작성해 주세요.
                    - 어휘와 표현 : 원문에 사용된 표현과 어휘를 최대한 그대로 유지해 주세요.
                    - 주의사항 : 각 불릿에는 반드시 하나의 문장만 포함해 주세요.
                4. recommendations: 이 채용공고가 특히 잘 맞는 사람의 유형을 3개 내외로 작성해 주세요.
                    - 주의사항 : '열심히 할 분' 같은 일반적인 말은 금지.
                    - 작성 기준 : 
                        - 원문을 그대로 옮기지 말고, 
                        - 채용공고 전체를 읽은 뒤 GPT가 판단하여, 
                        - 글을 쓰는 사람의 직무적 성장과 경험 확장과 연결되는 유형을 제안해 주세요. 
                    - 문구 예시: "브랜드의 보이스앤톤을 직접 설계해보고 싶은 분", "독립 잡지 출판의 전 과정을 경험하고 싶은 분", "텍스트 기반 커뮤니티의 운영 로직을 배우고 싶은 분" 등 직무적 성장과 연결할 것.
                    - 끝맺음 : "~한 분" (예: ~하는 분, ~를 찾는 분)
                    - 주의사항 : "에디터"라는 말을 직접 사용하지 말 것. 
                    - 주의사항 : 각 불릿에는 반드시 하나의 문장만 포함해 주세요.

            [내용] {truncated_text}
            """
            
            summary_res = client_openai.chat.completions.create(
                model="gpt-4o-mini",
                response_format={ "type": "json_object" },
                messages=[
                    {"role": "system", "content": "You are a professional editor. Respond only in json format with keys: 'roles', 'requirements', 'preferences', 'recommendations' (all lists)."},
                    {"role": "user", "content": summary_prompt}
                ]
            )
            gpt_res = json.loads(summary_res.choices[0].message.content)
            
            # 6. 슬랙 전송
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": "🆕 오늘 올라온 채용 공고", "emoji": True}},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*{original_title}*"}},
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*회사*\n{sheet_company}"},
                        {"type": "mrkdwn", "text": f"*지역*\n{sheet_location}"},
                        {"type": "mrkdwn", "text": f"*경력*\n{sheet_experience}"}
                    ]
                },
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "🎯 *주요 역할*\n" + "\n".join([f"• {r}" for r in gpt_res.get('roles', [])])}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "🧠 *요구 역량*\n" + "\n".join([f"• {req}" for req in gpt_res.get('requirements', [])])}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "⭐ *우대 사항*\n" + "\n".join([f"• {p}" for p in gpt_res.get('preferences', [])])}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "👍 *이런 분께 추천해요*\n" + "\n".join([f"• {rec}" for rec in gpt_res.get('recommendations', [])])}},
                {"type": "divider"},
                {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "상세 공고 보러가기", "emoji": True}, "style": "primary", "url": target_url}]}
            ]
            
            resp_slack = requests.post(webhook_url, json={"blocks": blocks})
            
            if resp_slack.status_code == 200:
                sheet.update_cell(update_row_index, status_col_idx, 'published')
                print(f"✅ 전송 성공: {original_title}")
            else:
                print(f"❌ 슬랙 전송 실패 (상태 코드: {resp_slack.status_code})")

            time.sleep(2)

        except Exception as e:
            print(f"❌ {update_row_index}행 처리 중 오류: {e}")
            continue

    print("--- 모든 대기 중인 공고 처리가 완료되었습니다 ---")

except Exception as e:
    print(f"❌ 치명적 오류: {e}")
    had_fatal_error = True

if had_fatal_error:
    sys.exit(1)
