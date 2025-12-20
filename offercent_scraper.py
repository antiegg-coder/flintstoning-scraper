import os, time, json, re
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# [설정] 오퍼센트 전용 정보
CONFIG = {
    "name": "오퍼센트",
    "url": "https://offercent.co.kr/company-list?jobCategories=0040002%2C0170004",
    "gid": "639559541"
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

# [공통] 브라우저 실행 설정
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver

# [전용] 데이터 수집 로직
def scrape_projects():
    driver = get_driver()
    new_data = []
    today = datetime.now().strftime("%Y-%m-%d")
    urls_check = set()
    
    try:
        driver.get(CONFIG["url"])
        wait = WebDriverWait(driver, 25)
        # 공고 카드들이 나타날 때까지 대기
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/job/']")))
        time.sleep(5)

        for _ in range(10):
            # 1. 공고 카드(a 태그) 전체를 먼저 확보합니다.
            cards = driver.find_elements(By.CSS_SELECTOR, "a[href*='/job/']")
            
            for card in cards:
                try:
                    href = card.get_attribute("href")
                    
                    # 회사명과 제목 리스트 초기화
                    company_name = ""
                    job_titles = []

                    # 2. 카드 내부의 모든 div를 조사하여 클래스별로 역할을 나눕니다.
                    divs = card.find_elements(By.TAG_NAME, "div")
                    
                    for div in divs:
                        class_name = div.get_attribute("class") or ""
                        
                        # [핵심] 클래스가 x6s0dn4로 시작하면 회사명 컨테이너입니다.
                        if class_name.startswith("x6s0dn4"):
                            try:
                                # 해당 컨테이너 내부의 회사명 텍스트 추출
                                company_el = div.find_element(By.CSS_SELECTOR, 'span[data-variant="body-02"]')
                                company_name = company_el.text.strip()
                            except:
                                continue
                        
                        # [핵심] 클래스가 xn25gh9로 시작하면 제목 묶음 컨테이너입니다.
                        elif class_name.startswith("xn25gh9"):
                            # 해당 컨테이너 내부의 모든 공고 제목들을 추출
                            title_elements = div.find_elements(By.CSS_SELECTOR, 'span[data-variant="body-02"]')
                            for t_el in title_elements:
                                txt = t_el.text.strip()
                                # '4일 전', '채용 중인 공고' 등 불필요한 텍스트 필터링
                                if not any(x in txt for x in ["전", "개월", "일", "주", "채용"]) and len(txt) > 2:
                                    job_titles.append(txt)

                    # 3. 수집된 정보를 매칭하여 저장
                    if company_name and job_titles:
                        for title in job_titles:
                            data_id = f"{href}_{title}"
                            if data_id not in urls_check:
                                new_data.append({
                                    'company': company_name,
                                    'title': title,
                                    'url': href,
                                    'scraped_at': today
                                })
                                urls_check.add(data_id)
                except:
                    continue
            
            # 다음 로딩을 위한 스크롤
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3)

    finally: 
        driver.quit()
    
    return new_data
    
# [공통] 시트 데이터 업데이트
def update_sheet(ws, data):
    if not data: 
        print(f"[{CONFIG['name']}] 새로 수집된 공고가 없습니다.")
        return

    all_v = ws.get_all_values()
    headers = all_v[0] if all_v else ['company', 'title', 'url', 'scraped_at', 'status']
    
    col_map = {name: i for i, name in enumerate(headers)}
    existing_urls = {row[col_map['url']] for row in all_v[1:] if len(row) > col_map['url']}
    
    rows_to_append = []
    for item in data:
        if item['url'] in existing_urls: continue
        
        row = [''] * len(headers)
        for k, v in item.items():
            if k in col_map: row[col_map[k]] = v
        
        if 'status' in col_map: row[col_map['status']] = 'new'
        rows_to_append.append(row)
    
    if rows_to_append:
        ws.append_rows(rows_to_append)
        print(f"💾 {CONFIG['name']} 신규 공고 {len(rows_to_append)}건 저장 완료")

if __name__ == "__main__":
    try:
        ws = get_worksheet()
        data = scrape_projects()
        update_sheet(ws, data)
    except Exception as e:
        print(f"❌ 실행 중 오류 발생: {e}")
