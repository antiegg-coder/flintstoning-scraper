import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd

# ==========================================
# [NEW] 새로 추가된 라이브러리 (인터넷 접속 + GPT)
# ==========================================
import requests
from bs4 import BeautifulSoup
from openai import OpenAI


# =========================================================
# PART 1. [기존 기능] 구글 시트 연결 및 데이터 가져오기
# =========================================================
try:
    # 1. 인증 설정 (깃허브 시크릿 사용)
    json_creds = os.environ['GOOGLE_CREDENTIALS']
    creds_dict = json.loads(json_creds)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # 2. 시트 열기
    # ★ [체크] 본인의 시트 제목으로 수정되어 있는지 확인하세요
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
    # PART 2. [기존 기능 + 업그레이드] 조건 필터링
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
    # PART 3. [완전 신규 기능] url 접속 및 내용 긁어오기 (Scraping)
    # =========================================================
    print("\n--- [NEW] url 내용 추출 시작 ---")

    # ★ [체크] 엑셀의 url 컬럼 이름이 'url'이 맞는지 확인하세요 (다르면 수정!)
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
    # PART 4. [완전 신규 기능] GPT에게 요약 시키기
    # =========================================================
    print("\n--- [NEW] GPT 요약 요청 시작 ---")

    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

    # [수정됨] 옵션 1: 전문 큐레이터 스타일 적용
    gpt_prompt = f"""
    너는 IT/비즈니스 커뮤니티에 매일 유익한 정보를 전달하는 '전문 큐레이터'야.
    아래 [글 내용]을 읽고, 슬랙(Slack) 커뮤니티 멤버들이 흥미를 가질 수 있도록 요약해줘.

    [작성 가이드]
    1. 톤앤매너: 전문적이면서도 친절하게 (~해요 체 사용)
    2. 형식:
       - 💡 **[제목]**: 글의 핵심을 관통하는 매력적인 한 줄 제목
       - 📝 **[3줄 요약]**:
         • 핵심 내용 1
         • 핵심 내용 2
         • 핵심 내용 3
       - 🚀 **[인사이트]**: 이 글이 주는 시사점이나 우리가 주목해야 할 포인트 1문장

    반드시 한국어로 작성해줘.

    [글 내용]
    {truncated_text}
    """

    # GPT-3.5 호출
    completion = client_openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful assistant summary bot."},
            {"role": "user", "content": gpt_prompt}
        ]
    )

    # 결과 받기
    summary = completion.choices[0].message.content
    
    # [수정됨] 요약문 아래에 원문 URL을 깔끔하게 붙여서 출력
    final_message = f"{summary}\n\n🔗 **원문 보러 가기**: {target_url}"
    
    print("\n" + "="*30)
    print(" [슬랙 공유용 메시지] ")
    print("="*30)
    print(final_message)
    print("="*30)

except Exception as e:
    print(f"\n❌ 에러 발생: {e}")
