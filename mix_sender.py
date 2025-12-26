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
    print("--- [Mix Sender] 시작 ---")
    
    # 환경변수 로드 확인
    if 'GOOGLE_CREDENTIALS' not in os.environ:
        raise Exception("환경변수 GOOGLE_CREDENTIALS가 없습니다.")

    json_creds = os.environ['GOOGLE_CREDENTIALS']
    creds_dict = json.loads(json_creds)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # 시트 열기
    spreadsheet = client.open('플린트스토닝 소재 DB')
    
    # [수정 권장] 시트 이름으로 가져오기 (예: '채용공고', '아티클' 등 실제 탭 이름 입력)
    # 인덱스(2)를 사용하려면 탭 순서가 절대 바뀌지 않도록 주의해야 합니다.
    try:
        # sheet = spreadsheet.worksheet('실제_탭_이름')  # <- 가장 권장하는 방식
        sheet = spreadsheet.get_worksheet(2) 
        print(f"📂 연결된 시트: {sheet.title}")
    except:
        print("❌ 시트를 찾을 수 없습니다.")
        exit()

    # 데이터 가져오기
    data = sheet.get_all_values()
    if not data:
        print("데이터가 없습니다.")
        exit()

    headers = data.pop(0)
    df = pd.DataFrame(data, columns=headers)
    
    # 헤더 공백 제거
    df.columns = df.columns.str.strip()

    # =========================================================
    # 2. 필터링
    # =========================================================
    COL_STATUS = 'status'
    COL_PUBLISH = 'publish'
    COL_TITLE = 'title'
    COL_URL = 'url'

    required_cols = [COL_STATUS, COL_PUBLISH, COL_TITLE, COL_URL]
    for col in required_cols:
        if col not in df.columns:
            print(f"❌ 오류: 시트에 '{col}' 헤더가 없습니다.")
            exit()

    # 조건: status는 'archived', publish는 'TRUE' (대소문자 무관하게 처리하려면 upper() 사용 권장)
    condition = (df[COL_STATUS].str.strip() == 'archived') & (df[COL_PUBLISH].str.strip() == 'TRUE')
    target_rows = df[condition]

    if target_rows.empty:
        print("ℹ️ 발송할 대상(archived & publish=TRUE)이 없습니다.")
        exit()

    # 첫 번째 행 선택
    row = target_rows.iloc[0]
    
    # 행 번호 계산 (헤더 제외한 데이터 프레임 인덱스 + 2)
    update_row_index = row.name + 2
    
    print(f"▶ 선택된 행 번호: {update_row_index}")

    # =========================================================
    # 3. 데이터 추출
    # =========================================================
    project_title = row[COL_TITLE]  # 변수명 통일
    target_url = row[COL_URL]
    
    print(f"▶ 제목: {project_title}")
    print(f"▶ URL: {target_url}")

    # =========================================================
    # 4. 웹 스크래핑
    # =========================================================
    print("--- 스크래핑 시작 ---")
    headers_ua = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(target_url, headers=headers_ua, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        article = soup.find('article')
        if article:
            paragraphs = article.find_all('p')
        else:
            paragraphs = soup.find_all('p')
        
        # [개선] 빈 문단 제외 및 공백 제거 후 연결
        text_list = [p.get_text().strip() for p in paragraphs if p.get_text().strip()]
        full_text = " ".join(text_list)
        
        # [4번 스크래핑 섹션 끝부분]
        if len(full_text) < 50:
             print("⚠️ 본문 내용이 너무 짧습니다. (스크래핑 실패 가능성)")
             
        truncated_text = full_text[:3000]
        
    except Exception as e:
        print(f"❌ 스크래핑 실패: {e}")
        exit()

    # =========================================================
    # 5. GPT 요약 (JSON 출력 모드 적용)
    # =========================================================
    print("--- GPT 요약 요청 ---")
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

    # f-string 내부에서 { }를 문자열로 쓰려면 {{ }} 처럼 두 번 써야 합니다.
    gpt_prompt = f"""
    너는 IT/테크 트렌드를 분석해주는 '인사이트 큐레이터'야.
    아래 [글 내용]을 읽고, 팀원들에게 공유할 수 있게 핵심 내용을 요약해줘.

    [출력 양식 (반드시 아래 JSON 형식으로만 응답할 것)]
    {{
      "key_points": ["핵심 내용 1", "핵심 내용 2", "핵심 내용 3", "핵심 내용 4"],
      "recommendations": ["추천 이유 1", "추천 이유 2", "추천 이유 3"]
    }}

    [글 내용]
    {truncated_text}
    """

    # 에러 방지를 위해 response_format 구조 주의
    completion = client_openai.chat.completions.create(
        model="gpt-3.5-turbo-0125",
        response_format={ "type": "json_object" }, 
        messages=[
            {"role": "system", "content": "You are a helpful assistant that outputs JSON."},
            {"role": "user", "content": gpt_prompt}
        ]
    )

    # 결과 파싱
    gpt_res = json.loads(completion.choices[0].message.content)
    key_points = gpt_res.get("key_points", [])
    recommendations = gpt_res.get("recommendations", [])

    # =========================================================
    # 6. 슬랙 전송 (Block Kit UI 구성)
    # =========================================================
    print("--- 슬랙 전송 시작 (Block Kit) ---")
    webhook_url = os.environ['SLACK_WEBHOOK_URL']

    # 리스트 데이터를 불렛포인트 텍스트로 변환
    key_points_text = "\n".join([f"• {point}" for point in key_points])
    recommend_text = "\n".join([f"• {rec}" for rec in recommendations])

    # 이미지와 동일한 레이아웃 구성
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "지금 주목해야 할 아티클",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{project_title}*"
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"📌 *이 글에서 이야기하는 것들*\n{key_points_text}"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"📌 *이런 분께 추천해요*\n{recommend_text}"
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "아티클 보러가기",
                        "emoji": True
                    },
                    "style": "primary",
                    "url": target_url
                }
            ]
        }
    ]

    # 슬랙 전송
    slack_res = requests.post(webhook_url, json={"blocks": blocks})
