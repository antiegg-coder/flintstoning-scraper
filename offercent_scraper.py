import os, sys, time, json, re
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ==========================================
# [전용] 설정 정보
# ==========================================
CONFIG = {
    "name": "오퍼센트_통합_크롤러",
    "url": "https://offercent.co.kr/list?jobCategories=0040002%2C0170004&sort=recent",
    "gid": "639559541"
}

# ==========================================
# [공통] 구글 스프레드시트 연결 로직
# ==========================================
def get_worksheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1nKPVCZ6zAOfpqCjV6WfjkzCI55FA9r2yvi9XL3iIneo/edit")
    sheet = next((s for s in spreadsheet.worksheets() if str(s.id) == CONFIG["gid"]), None)
    if not sheet: raise Exception(f"{CONFIG['gid']} 시트를 못 찾았습니다.")
    return sheet

# ==========================================
# [공통] 셀레니움 브라우저 설정 로직
# ==========================================
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver

# ==========================================
# [전용] 오퍼센트 사이트 데이터 수집 로직 (키워드 기반 분류 적용)
# ==========================================
def scrape_projects():
    driver = get_driver()
    new_data = []
    today = datetime.now().strftime("%Y-%m-%d")
    urls_check = set()
    
    try:
        print(f"🔗 접속 중: {CONFIG['url']}")
        driver.get(CONFIG["url"])
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a.xqzk367")))
        
        print("📥 실시간 누적 수집 및 데이터 분류를 시작합니다...")
        
        # 단계별로 스크롤하며 수집 (필요시 range 숫자를 높여 더 많이 수집 가능)
        for i in range(1, 21):
            current_cards = driver.find_elements(By.CSS_SELECTOR, "a.xqzk367[href*='/jd/']")
            
            for card in current_cards:
                try:
                    full_href = card.get_attribute("href")
                    clean_url = full_href.split('?')[0]
                    title = card.text.strip()
                    
                    if clean_url not in urls_check and title:
                        container = card.find_element(By.XPATH, "..")
                        company_name, location, experience = "회사명 미상", "", ""
                        
                        for _ in range(5):
                            try:
                                # 회사명 추출
                                company_el = container.find_element(By.CSS_SELECTOR, 'span[data-variant="body-02"]')
                                company_name = company_el.text.strip()
                                
                                # 지역/경력 통합 텍스트 추출
                                info_el = container.find_element(By.CSS_SELECTOR, 'span[data-variant="body-03"]')
                                info_text = info_el.text.strip()
                                
                                # ------------------------------------------------------
                                # [핵심] 키워드 기반 자동 분류 로직
                                # ------------------------------------------------------
                                if info_text:
                                    # 가운데 점(·)이 있으면 나누고, 없으면 통째로 리스트화
                                    parts = [p.strip() for p in info_text.split("·")] if "·" in info_text else [info_text]
                                    
                                    exp_keywords = ["경력", "신입", "년", "무관"]
                                    
                                    for part in parts:
                                        # 조각 내에 경력 관련 키워드가 있는지 검사
                                        if any(key in part for key in exp_keywords):
                                            experience = part
                                        else:
                                            # 키워드가 없으면 지역으로 간주 (단, 이미 채워졌다면 무시)
                                            if not location:
                                                location = part

                                if company_name != "회사명 미상": break
                            except:
                                container = container.find_element(By.XPATH, "..")

                        new_data.append({
                            'company': company_name, 'title': title, 'location': location,
                            'experience': experience, 'url': clean_url, 'scraped_at': today
                        })
                        urls_check.add(clean_url)
                        print(f"✨ 수집: {company_name} | {location} | {experience}")

                except: continue
            
            driver.execute_script("window.scrollBy(0, 1200);")
            time.sleep(2.5)

    finally: 
        driver.quit()
    
    print(f"✅ 총 {len(new_data)}건의 공고를 정확하게 분류하여 수집했습니다!")
    return new_data
    
# ==========================================
# [공통] 시트 데이터 업데이트 로직
# ==========================================
def update_sheet(ws, data):
    if not data: 
        print(f"[{CONFIG['name']}] 새로 수집된 공고가 없습니다.")
        return

    all_v = ws.get_all_values()
    headers = all_v[0] if all_v else ['company', 'title', 'location', 'experience', 'url', 'scraped_at', 'status']
    
    col_map = {name: i for i, name in enumerate(headers)}
    # 기존 데이터 중복 비교 (URL 파라미터 제외)
    existing_urls = {row[col_map['url']].split('?')[0] for row in all_v[1:] if len(row) > col_map['url']}
    
    rows_to_append = []
    for item in data:
        if item['url'] in existing_urls: continue
        row = [''] * len(headers)
        for k, v in item.items():
            if k in col_map: row[col_map[k]] = v
        if 'status' in col_map: row[col_map['status']] = 'archived'
        rows_to_append.append(row)
    
    if rows_to_append:
        ws.append_rows(rows_to_append)
        print(f"💾 {CONFIG['name']} 신규 공고 {len(rows_to_append)}건 저장 완료")

# ==========================================
# [공통] 실행 메인 루틴
# ==========================================
if __name__ == "__main__":
    try:
        ws = get_worksheet()
        data = scrape_projects()
        update_sheet(ws, data)
    except Exception as e:
        print(f"❌ 실행 중 오류 발생: {e}")
        sys.exit(1)
