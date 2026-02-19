import os, sys, time, json, re
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# [설정]
CONFIG = {
    "name": "렛플(Letspl)",
    "url": "https://letspl.me/project?location=KR00&type=00&recruitingType=all&jobD=0207&skill=&interest=&keyword=",
    "gid": "1669656972"
}

# [공통] 시트 연결
def get_worksheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1nKPVCZ6zAOfpqCjV6WfjkzCI55FA9r2yvi9XL3iIneo/edit")
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
    REGIONS = ["서울", "경기", "인천", "대전", "대구", "부산", "광주", "울산", "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주", "온라인", "지역무관"]

    try:
        print(f"🌐 {CONFIG['name']} 접속 중...")
        driver.get(CONFIG["url"])
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href^='/project/']")))
        
        time.sleep(5) 
        
        cards = driver.find_elements(By.CSS_SELECTOR, "a[href^='/project/']")

        for elem in cards:
            try:
                # 1. 캐로셀(주목중) 카드 제외
                card_class = elem.get_attribute("class") or ""
                if "Comment" in card_class or "newProject" in card_class:
                    continue

                # --- [수정 구간: 컨텐츠 기획 필터링] ---
                try:
                    # 이미지에서 확인된 클래스명을 사용하여 직무 텍스트 추출
                    job_info_elem = elem.find_element(By.CSS_SELECTOR, "span[class*='card_searchBottomJobinfoTxt']")
                    job_text = job_info_elem.text.strip()
                    
                    # '컨텐츠 기획'이 포함되어 있지 않으면 스킵
                    if "컨텐츠 기획" not in job_text:
                        continue
                except:
                    # 직무 정보 요소가 아예 없는 카드라면 스킵
                    continue
                # ---------------------------------------

                href = elem.get_attribute("href")
                if not re.search(r'/project/\d+', href): continue
                
                # 2. 제목 찾기 로직
                title = ""
                try:
                    h3_elem = elem.find_element(By.TAG_NAME, "h3")
                    title_elem = h3_elem.find_element(By.CSS_SELECTOR, "span[class*='TitleTxt']")
                    title = title_elem.text.strip()
                except:
                    BAD_WORDS = ["팔로우", "주목중", "D-", "NEW", "렛플이"]
                    lines = elem.text.split('\n')
                    clean_lines = [l.strip() for l in lines if len(l.strip()) > 1 
                                   and not any(bad in l for bad in BAD_WORDS)]
                    if clean_lines: title = clean_lines[0]

                if not title or len(title) < 2: continue

                loc = next((k for k in REGIONS if k in elem.text), "미정")
                
                if not any(d['url'] == href for d in new_data):
                    new_data.append({'title': title, 'url': href, 'scraped_at': today, 'location': loc})
            except Exception as e:
                # 개별 카드 처리 중 오류 시 다음 카드로 진행
                continue
    finally: 
        driver.quit()
    return new_data
# [공통] 스마트 저장
def update_sheet(ws, data):
    if not data: return print(f"[{CONFIG['name']}] 새 데이터 없음")
    all_v = ws.get_all_values()
    headers = all_v[0] if all_v else ['title', 'url', 'scraped_at', 'status', 'location']
    
    col_map = {name: i for i, name in enumerate(headers)}
    if 'url' not in col_map: return print("❌ 'url' 컬럼을 찾을 수 없습니다.")

    existing_urls = {row[col_map['url']] for row in all_v[1:] if len(row) > col_map['url']}
    
    rows = []
    for item in data:
        if item['url'] in existing_urls: continue
        row = [''] * len(headers)
        for k, v in item.items():
            if k in col_map: row[col_map[k]] = v
        if 'status' in col_map: row[col_map['status']] = 'archived'
        rows.append(row)
    
    if rows:
        ws.append_rows(rows)
        print(f"💾 {CONFIG['name']} {len(rows)}건 저장 완료!")

if __name__ == "__main__":
    try:
        ws = get_worksheet()
        data = scrape_projects()
        update_sheet(ws, data)
    except Exception as e:
        print(f"🚨 {CONFIG['name']} 실행 실패: {e}")
        sys.exit(1)
