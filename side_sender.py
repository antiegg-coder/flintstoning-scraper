import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests
from bs4 import BeautifulSoup
from openai import OpenAI


# =========================================================
# PART 1. 구글 시트 연결 및 데이터 가져오기
# =========================================================
try:
    # 1. 인증 설정 (깃허브 시크릿 사용)
    json_creds = os.environ['GOOGLE_CREDENTIALS']
    creds_dict = json.loads(json_creds)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # 2. 시트 열기
    spreadsheet = client.open('플린트스토닝 소재 DB') 
    sheet = spreadsheet.sheet1

    # 3. 데이터 프레임 변환
    data = sheet.get_all_values()
    if not data:
        print("데이터가 없습니다.")
        exit()
        
    headers = data.pop(0)
    df = pd.DataFrame(data, columns=headers)

    # =========================================================
    # PART 2. 조건 필터링
    # =========================================================
    
    # 열 개수 확인
    if len(df.columns) <= 5:
        print("오류: 데이터의 열 개수가 부족합니다.")
        exit()

    col_f = df.columns[5] # F열 (6번째)

    # [업그레이드 된 부분] 
    # 기존: df[col_f] == 'archived'
    # 변경: .str.strip()을 추가하여 띄어쓰기 공백을 제거하고 비교 (더 안전함)
    condition = (df[col_f].str.strip() == 'archived') & (df['publish'].str.strip() == 'TRUE')
    target_rows = df[condition]

    if target_rows.empty:
        print("조건(archived & TRUE)에 맞는 행이 없습니다.")
        exit()

    # 첫 번째 행 선택
    row = target_rows.iloc[0]
    print(f"✅ 행 추출 성공: {row.to_dict()}")


    # =========================================================
    # PART 3. url 접속 및 내용 긁어오기 (Scraping)
    # =========================================================
    print("\n--- [NEW] url 내용 추출 시작 ---")
    url_col_name = 'url' 

    if url_col_name not in row:
        print(f"오류: 엑셀에 '{url_col_name}'이라는 헤더가 없습니다.")
        exit()

    target_url = row[url_col_name]
    print(f"▶ 접속 시도: {target_url}")

    # 봇 차단 방지용 헤더 (브라우저인 척 속임)
    headers_ua = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    # requests로 웹페이지 접속
    response = requests.get(target_url, headers=headers_ua, timeout=10)
    if response.status_code != 200:
        print(f"❌ 접속 실패 (상태 코드: {response.status_code})")
        exit()

    # BeautifulSoup으로 HTML 분석
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # <p> 태그(본문)만 찾아서 합치기
    paragraphs = soup.find_all('p')
    full_text = " ".join([p.get_text() for p in paragraphs])

    if not full_text:
        print("❌ 본문 텍스트를 찾지 못했습니다.")
        exit()

    # 너무 길면 GPT가 힘들어하니 3000자로 자르기
    truncated_text = full_text[:3000]
    print(f"▶ 본문 추출 완료 ({len(full_text)}자 → 3000자로 단축됨)")


   # =========================================================
    # PART 4. GPT에게 요약 시키기
    # =========================================================
    print("\n--- [NEW] GPT 요약 요청 시작 ---")

    # ★ 스프레드시트에서 제목 가져오기
    title_col_name = 'title' 

    if title_col_name not in row:
        print(f"오류: 엑셀에 '{title_col_name}'이라는 헤더가 없습니다.")
        exit()
    
    project_title = row[title_col_name]
    print(f"▶ 제목 추출 완료: {project_title}")

    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

    # 프롬프트
    gpt_prompt = f"""
    너는 채용 공고나 프로젝트 정보를 정리해주는 '전문 에디터'야.
    아래 [글 내용]을 읽고, 지정된 **출력 양식**을 엄격하게 지켜서 답변해.
    모든 텍스트에 이모지를 절대 사용하지 마.

    [출력 양식]
    *이런 분께 추천해요*
    - (추천 대상 1)
    - (추천 대상 2)

    [글 내용]
    {truncated_text}
    """

    # GPT-3.5 호출
    completion = client_openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a strict output formatter. Do not use emojis."},
            {"role": "user", "content": gpt_prompt}
        ]
    )

    # GPT 결과 (추천대상)
    gpt_body = completion.choices[0].message.content
    
    # 파이썬이 직접 '제목(링크)'와 'GPT요약'을 합체!
    # 슬랙 링크 형식: <URL|텍스트>
    final_message = f"*추천 프로젝트*\n<{target_url}|{project_title}>\n\n{gpt_body}"
    
    # 로그 출력
    print("\n" + "="*30)
    print(" [최종 결과물] ")
    print("="*30)
    print(final_message)
    print("="*30)

    # =========================================================
    # PART 5. 슬랙(Slack)으로 전송하기
    # =========================================================
    print("\n--- [NEW] 슬랙 전송 시작 ---")
    
    try:
        webhook_url = os.environ['SLACK_WEBHOOK_URL']
        
        payload = {
            "text": final_message
        }
        
        response = requests.post(webhook_url, json=payload)
        
        if response.status_code == 200:
            print("✅ 슬랙 전송 성공!")
        else:
            print(f"❌ 전송 실패 (상태 코드: {response.status_code})")
            print(response.text)
            
    except KeyError:
        print("⚠️ 경고: SLACK_WEBHOOK_URL 시크릿이 설정되지 않아서 전송을 건너뜁니다.")
    except Exception as e:
        print(f"❌ 슬랙 전송 중 에러 발생: {e}")

except Exception as e:
    print(f"\n❌ 에러 발생: {e}")
