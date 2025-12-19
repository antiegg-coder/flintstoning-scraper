import os, time, json, re
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# [설정] 이 파일 전용 정보
CONFIG = {
    "name": "오퍼센트",
    "url": "https://offercent.co.kr/company-list?jobCategories=0040002%2C0170004",
    "gid": "639559541" # 오퍼센트 탭
}

# [공통] 시트 연결 (GID로 찾기)
def get_worksheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1nKPVCZ6zAOfpqCjV6WfjkzCI55FA9r2yvi9XL3iIneo/edit")
    # 순서가 바뀌어도 ID로 탭을 찾음
    sheet = next((s for s in spreadsheet.worksheets() if str(s.id) == CONFIG["gid"]), None)
    if not sheet: raise Exception(f"{CONFIG['gid']} 시트를 못 찾았습니다.")
    return sheet

# [공통] 브라우저 실행
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-blink-features=AutomationControlled")
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"})
    return driver

for card in cards:
            href = card.get_attribute("href")
            if not href or "/job/" not in href: continue
            
            try:
                # 'body-02' 변형을 가진 모든 span 요소를 순서대로 가져옴
                elements = card.find_elements(By.CSS_SELECTOR, 'span[data-variant="body-02"]')
                if not elements: continue

                # 리스트의 첫 번째 요소는 회사명, 그 이후는 공고 제목들로 간주
                # (보내주신 구조상 회사명이 항상 상단에 위치함)
                texts = [el.text.strip() for el in elements if el.text.strip()]
                
                if len(texts) >= 2:
                    company = texts[0]  # 예: 엠피엠지(MPMG)
                    titles = texts[1:]  # 그 외 모든 텍스트는 공고 제목 리스트
                    
                    for title in titles:
                        # '6일 전', '1개월 이상' 같은 칩(Chip) 데이터와 섞이지 않도록 필터링
                        # 보통 날짜 정보는 span이 아닌 다른 태그나 클래스에 있지만, 안전을 위해 체크
                        if "전" in title or "개월" in title or "일" in title: continue
                        
                        data_id = f"{href}_{title}"
                        if data_id not in urls_check:
                            new_data.append({
                                'company': company,
                                'title': title,
                                'url': href,
                                'scraped_at': today
                            })
                            urls_check.add(data_id)
            except Exception as e:
                continue
    
# [공통] 스마트 저장 (헤더 이름 기준)
def update_sheet(ws, data):
    if not data: return print(f"[{CONFIG['name']}] 새 공고 없음")
    all_v = ws.get_all_values()
    headers = all_v[0] if all_v else ['title', 'url', 'scraped_at', 'status', 'location']
    col_map = {name: i for i, name in enumerate(headers)}
    existing_urls = {row[col_map['url']] for row in all_v[1:] if len(row) > col_map['url']}
    
    rows = []
    for item in data:
        if item['url'] in existing_urls: continue
        row = [''] * len(headers)
        for k, v in item.items():
            if k in col_map: row[col_map[k]] = v
        if 'status' in col_map: row[col_map['status']] = 'archived'
        rows.append(row)
    
    if rows: ws.append_rows(rows); print(f"💾 {CONFIG['name']} {len(rows)}건 저장")

if __name__ == "__main__":
    ws = get_worksheet(); data = scrape_projects(); update_sheet(ws, data)
