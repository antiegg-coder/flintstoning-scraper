import os, time, json, re
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
# [전용] 오퍼센트 사이트 데이터 수집 로직
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
        # [전용 선택자] 제목 클래스 xqzk367가 나타날 때까지 대기
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a.xqzk367")))
        
        # 데이터 로드를 위한 스크롤
        for _ in range(5):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

        # [제목 로직] 클래스 xqzk367 기반 추출
        cards = driver.find_elements(By.CSS_SELECTOR, "a.xqzk367[href*='/jd/']")
        print(f"🔍 발견된 공고 카드 개수: {len(cards)}개")

        for card in cards:
            try:
                title = card.text.strip()
                full_href = card.get_attribute("href")
                clean_url = full_href.split('?')[0]
                
                # [수정 포인트] 특정 클래스명 대신, a태그를 감싸고 있는 
                # 가장 가까운 div(공고 카드 덩어리)를 유연하게 찾습니다.
                # 보통 제목 -> 부모(div) -> 부모(div) 구조에 회사명이 있습니다.
                
                # a태그의 부모 요소부터 차례로 탐색
                container = card.find_element(By.XPATH, "..") 
                
                company_name = "회사명 미상"
                location = ""
                experience = ""

                # 상위로 5단계까지만 올라가며 회사명(body-02)과 정보(body-03)가 있는지 확인
                for _ in range(5):
                    try:
                        # 1. 회사명 찾기 (body-02)
                        company_el = container.find_element(By.CSS_SELECTOR, 'span[data-variant="body-02"]')
                        company_name = company_el.text.strip()
                        
                        # 2. 지역/경력 찾기 (body-03)
                        info_el = container.find_element(By.CSS_SELECTOR, 'span[data-variant="body-03"]')
                        info_text = info_el.text.strip()
                        
                        if "·" in info_text:
                            parts = info_text.split("·")
                            location, experience = parts[0].strip(), parts[1].strip()
                        else:
                            location = info_text
                        
                        # 회사명과 지역 정보가 모두 확보되면 탐색 중단
                        if company_name != "회사명 미상" and location:
                            break
                    except:
                        # 정보를 못 찾으면 한 단계 더 위 부모로 이동
                        container = container.find_element(By.XPATH, "..")

                data_id = f"{clean_url}_{title}"
                if data_id not in urls_check:
                    new_data.append({
                        'company': company_name,
                        'title': title,
                        'location': location,
                        'experience': experience,
                        'url': clean_url,
                        'scraped_at': today
                    })
                    urls_check.add(data_id)
                    print(f"✅ 추출 성공: {company_name} | {title}")

            except Exception as e:
                # print(f"❌ 개별 카드 오류: {e}") # 필요 시 주석 해제하여 상세 오류 확인
                continue

    finally: 
        driver.quit()
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
        if 'status' in col_map: row[col_map['status']] = 'new'
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
