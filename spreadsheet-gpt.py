import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup

# --- 환경 변수 로드 ---
GOOGLE_JSON = json.loads(os.environ['GOOGLE_SHEET_KEY'])
GEMINI_API_KEY = os.environ['GEMINI_API_KEY']
SHEET_URL = os.environ['SHEET_URL']
# SLACK_WEBHOOK_URL은 당분간 사용하지 않으므로 주석 처리하거나 환경 변수에서 제외해도 됩니다.

def get_sheet_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(GOOGLE_JSON, scope)
    return gspread.authorize(creds)

def process_sheet():
    client = get_sheet_client()
    sheet = client.open_by_url(SHEET_URL).sheet1
    
    data = sheet.get_all_records()
    
    target_row_index = None
    target_row_data = None
    
    # 1. 조건 검색: publish=TRUE AND status=archived
    for i, row in enumerate(data):
        if str(row.get('publish')).upper() == 'TRUE' and row.get('status') == 'archived':
            target_row_index = i + 2 
            target_row_data = row
            break 
            
    if not target_row_data:
        print("📭 조건(publish=TRUE, status=archived)에 맞는 행이 없습니다.")
        return

    print(f"🚀 처리 시작: 행 {target_row_index} - {target_row_data.get('url')}")
    
    # 2. URL 내용 가져오기
    url = target_row_data.get('url')
    content = fetch_url_content(url)
    
    if not content:
        print("❌ URL 내용을 가져오지 못했습니다.")
        return

    # 3. Gemini 요약
    summary = summarize_with_gemini(content)
    
    # 4. H열에 메시지 저장 (슬랙 전송 대신)
    # 슬랙 포맷으로 미리 만들어 둡니다.
    final_message = f"🤖 *Daily Pick*\n{summary}\n\n🔗 {url}"
    
    try:
        # H열은 8번째 열입니다. (A=1, ... H=8)
        sheet.update_cell(target_row_index, 8, final_message)
        print(f"✅ H열(8)에 메시지 저장 완료")
        
        # 5. 상태 업데이트 (중복 방지)
        # 처리가 끝났으므로 status를 'done'으로 변경
        headers = sheet.row_values(1)
        if 'status' in headers:
            status_col_index = headers.index('status') + 1
            sheet.update_cell(target_row_index, status_col_index, 'done')
            print(f"✅ 상태 업데이트 완료: 'done'")
            
    except Exception as e:
        print(f"❌ 시트 업데이트 실패: {e}")

def fetch_url_content(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
            
        text = soup.get_text(separator=' ')
        clean_text = ' '.join(text.split())
        return clean_text[:8000]
    except Exception as e:
        print(f"URL Fetch Error: {e}")
        return None

def summarize_with_gemini(text):
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""
        당신은 전문 콘텐츠 큐레이터입니다. 아래 글을 읽고 다음 형식으로 요약해주세요.
        
        1. **3줄 요약**: 핵심 내용을 명확하게 요약 (이모지 활용)
        2. **Insight**: 이 글이 업무나 업계에 주는 시사점 한 문장
        
        [글 내용]
        {text}
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Gemini Error: {e}"

if __name__ == "__main__":
    process_sheet()
