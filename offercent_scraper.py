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

# [전용] 데이터 수집
def scrape_projects():
    driver = get_driver()
    new_data = []
    today = datetime.now().strftime("%Y-%m-%d")
    urls = set() # URL 중복 방지 (카드 기준)
    
    try:
        driver.get(CONFIG["url"])
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "a")))
        
        for _ in range(10):
            # 1. 공고 카드(a 태그)들을 먼저 찾음
            cards = driver.find_elements(By.TAG_NAME, "a")
            
            for card in cards:
                href = card.get_attribute("href")
                if not href or "company-list" in href: continue
                
                try:
                    # 2. 회사명 추출 (제목 클래스가 없는 greet-typography 찾기)
                    # 카드 전체 텍스트에서 회사명은 보통 상단에 위치함
                    all_spans = card.find_elements(By.CSS_SELECTOR, "span.greet-typography")
                    
                    company = ""
                    # 제목 요소들만 따로 리스트로 수집
                    title_elements = []
                    
                    for s in all_spans:
                        class_attr = s.get_attribute("class")
                        txt = s.text.strip()
                        if not txt: continue
                        
                        if "xlyipyv" in class_attr: # 제목 클래스 발견 시
                            title_elements.append(txt)
                        elif not company: # 제목 클래스가 없고 아직 회사명을 못찾았다면
                            company = txt

                    # 3. 발견된 모든 제목을 각각의 데이터로 저장
                    # 중복 방지를 위해 (URL + 제목) 조합으로 체크하는 것이 안전함
                    for title in title_elements:
                        data_id = f"{href}_{title}"
                        if data_id not in urls:
                            new_data.append({
                                'company': company,
                                'title': title,
                                'url': href,
                                'scraped_at': today
                            })
                            urls.add(data_id)
                            
                except Exception as e:
                    continue
            
            # 스크롤 후 대기
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2.5)
            
    finally: driver.quit()
    return new_data

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
