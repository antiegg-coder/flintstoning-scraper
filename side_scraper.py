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
    "name": "사이드프로젝트",
    "url": "https://sideproject.co.kr/projects",
    "gid": "1818966683" # 탭 고유 번호
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
    regions = ["서울", "경기", "인천", "대전", "대구", "부산", "광주", "울산", "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주", "온라인"]

    try:
        driver.get(CONFIG["url"])
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "a")))
        time.sleep(5)
        
        for elem in driver.find_elements(By.TAG_NAME, "a"):
            href = elem.get_attribute("href")
            if href and "idx=" in href and "bmode=view" in href:
                text = elem.text.strip()
                if not text: continue
                
                # 지역을 찾으면 해당 지역명을, 못 찾으면 빈 문자열("")을 할당합니다.
                loc = next((k for k in regions if k in text), "") 
                
                idx = re.search(r'idx=(\d+)', href).group(1)
                full_url = f"https://sideproject.co.kr/projects/?bmode=view&idx={idx}"
                
                if not any(d['url'] == full_url for d in new_data):
                    new_data.append({
                        'title': text.split('\n')[0], 
                        'url': full_url, 
                        'scraped_at': today, 
                        'location': loc
                    })
    finally: 
        driver.quit()
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
