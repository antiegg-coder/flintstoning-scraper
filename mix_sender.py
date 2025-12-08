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
    
    # [변경] 인덱스(2) 대신 시트 이름으로 명시적 선택 권장 (오류 방지)
    # 탭 이름이 정확한지 확인해주세요. 예: '채용공고', '아티클' 등
    # 만약 이름을 모른다면 기존처럼 get_worksheet(2) 사용하되 주의 필요.
    try:
        sheet = spreadsheet.get_worksheet(2) 
        print(f"📂 연결된 시트: {sheet.title}")
    except:
        print("❌ 시트를 찾을 수 없습니다. (인덱스 2번)")
        exit()

    # 데이터 가져오기
    data = sheet.get_all_values()
    if not data:
        print("데이터가 없습니다.")
        exit()

    headers = data.pop(0)
    df = pd.DataFrame(data, columns=headers)
    
    # [추가] 헤더 공백 제거 (실수로 ' url ' 처럼 들어가는 경우 방지)
    df.columns = df.columns.str.strip()

    # =========================================================
    # 2. 필터링 (헤더 이름 기반으로 변경)
    # =========================================================
    # 필수 컬럼 이름 정의 (시트의 실제 헤더와 일치해야 함)
    COL_STATUS = 'status'    # F열 역할
    COL_PUBLISH = 'publish'  # publish 열
    COL_TITLE = 'title'      # A열 역할
    COL_URL = 'url'          # C열 역할

    # 필수 헤더가 있는지 검사
    required_cols = [COL_STATUS, COL_PUBLISH, COL_TITLE, COL_URL]
    for col in required_cols:
        if col not in df.columns:
            print(f"❌ 오류: 시트에 '{col}' 헤더가 없습니다.")
            exit()

    # 조건 확인 (archived & TRUE)
    condition = (df[COL_STATUS].str.strip() == 'archived') & (df[COL_PUBLISH].str.strip() == 'TRUE')
    target_rows = df[condition]

    if target_rows.empty:
        print("ℹ️ 발송할 대상(archived & publish=TRUE)이 없습니다.")
        exit()

    # 첫 번째 행 선택
    row = target_rows.iloc[0]
    
    # 구글 시트 행 번호 계산 (헤더 1행 + 판다스 인덱스 + 1 = 인덱스 + 2)
    update_row_index = row.name + 2
    
    print(f"▶ 선택된 행 번호: {update_row_index}")

    # =========================================================
    # 3. 데이터 추출 (이름 기반 추출)
    # =========================================================
    project_title = row[COL_TITLE]
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
        response.raise_for_status() # 400/500 에러 시 예외 발생
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # [Tip] 본문 추출 정확도 높이기 (p태그만 가져오면 메뉴/푸터가 섞일 수 있음)
        # article 태그가 있으면 우선 사용, 없으면 p태그 사용
        article = soup.find('article')
        if article:
            paragraphs = article.find_all('p')
        else:
            paragraphs = soup.find_all('p')
            
        full_text = " ".join([p.get_text() for p in paragraphs])
        
        if len(full_text) < 50:
             print("⚠️ 본문 내용이 너무 짧습니다. (스크래핑 실패 가능성)")
             # 그래도 진행하거나 여기서 멈출 수 있음
             
        truncated_text = full_text[:3000]
        
    except Exception as e:
        print(f"❌ 스크래핑 실패: {e}")
        exit()

    # =========================================================
    # 5. GPT 요약 (조건 반영 수정됨)
    # =========================================================
    print("--- GPT 요약 요청 ---")
    client_openai = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

    # [수정 1] 프롬프트 강화: ~합니다체 강제, 이모지 절대 금지
    gpt_prompt = f"""
    너는 IT/테크 트렌드를 분석해주는 '인사이트 큐레이터'야.
    아래 [글 내용]을 읽고, 팀원들에게 공유할 수 있게 요약해줘.

    [작성 규칙]
    1. **어조**: 모든 문장은 반드시 '**~합니다.**' 또는 '**~입니다.**'와 같은 정중한 합쇼체(경어)로 끝내야 해.
    2. **금지**: '~음', '~함', '~것' 같은 명사형 종결이나 반말은 절대 사용하지 마.
    3. **이모지**: 본문 내용 중에 이모지를 절대 사용하지 마.

    [출력 양식]
    *요약*
    (글의 핵심 내용을 3문장 내외의 줄글로 작성. 반드시 '~합니다.'로 끝낼 것.)

    *인사이트*
    (이 글에서 얻을 수 있는 시사점을 1~2문장으로 작성. 반드시 '~합니다.'로 끝낼 것.)

    [글 내용]
    {truncated_text}
    """

    completion = client_openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Use polite Korean sentences ending in period."},
            {"role": "user", "content": gpt_prompt}
        ]
    )

    gpt_body = completion.choices[0].message.content

    # [수정 2] 제목에서 이모지(📰) 제거
    # 요청: 1번째 줄은 "오늘의 인사이트"로 고정 (이모지 없음)
    final_message = f"*오늘의 인사이트*\n제목: {article_title}\n\n{gpt_body}"
    
    # [수정 3] 하단 링크에만 🔗 이모지 유지
    final_message_with_link = f"{final_message}\n\n🔗 <{target_url}|원문 보러가기>"
    
    print("--- 최종 결과물 ---")
    print(final_message_with_link)

    # =========================================================
    # 6. 슬랙 전송 & 시트 업데이트
    # =========================================================
    print("--- 슬랙 전송 시작 ---")
    webhook_url = os.environ['SLACK_WEBHOOK_URL']
    payload = {"text": final_message_with_link}
    
    slack_res = requests.post(webhook_url, json=payload)
    
    if slack_res.status_code == 200:
        print("✅ 슬랙 전송 성공!")
        
        try:
            # [변경] 열 번호 동적 찾기
            # headers 리스트에서 'status' 컬럼의 인덱스 찾기 (+1 해야 실제 시트 열 번호)
            status_col_index = headers.index(COL_STATUS) + 1
            
            print(f"▶ 시트 상태 업데이트 중... (행: {update_row_index}, 열: {status_col_index})")
            sheet.update_cell(update_row_index, status_col_index, 'published')
            print("✅ 상태 변경 완료 (archived -> published)")
        except Exception as e:
            print(f"⚠️ 상태 업데이트 실패: {e}")
            
    else:
        print(f"❌ 전송 실패 (상태 코드: {slack_res.status_code})")
        print(slack_res.text)

except Exception as e:
    print(f"🚨 전체 실행 중 에러 발생: {e}")
