import os, time, json, re
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# [설정] 이 파일 전용 정보 (기존과 동일)
CONFIG = {
    "name": "오퍼센트",
    "url": "https://offercent.co.kr/company-list?jobCategories=0040002%2C0170004",
    "gid": "639559541" # 오퍼센트 탭 GID
}

# [공통] 시트 연결 (기존과 동일)
def get_worksheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1nKPVCZ6zAOfpqCjV6WfjkzCI55FA9r2yvi9XL3iIneo/edit")
    
    sheet = next((s for s in spreadsheet.worksheets() if str(s.id) == CONFIG["gid"]), None)
    if not sheet: raise Exception(f"{CONFIG['gid']} 시트를 못 찾았습니다.")
    return sheet

# [공통] 브라우저 실행 설정 (기존과 동일)
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")  # 추가: GPU 가속 비활성화 (서버 환경 필수)
    options.add_argument("--window-size=1920,1080")
    
    # [핵심 수정] 화면 크기를 PC 규격(1920x1080)으로 강제 설정합니다.
    # 이렇게 하면 사진 속의 모바일 화면이 아닌, 우리가 처음에 본 PC 화면이 뜹니다.
    options.add_argument("--window-size=1920,1080")
    
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver
    
# [전용] 데이터 수집 로직 (스크린샷 추가)
def scrape_projects():
    driver = get_driver()
    new_data = []
    today = datetime.now().strftime("%Y-%m-%d")
    urls_check = set()
    
    try:
        driver.get(CONFIG["url"])
        wait = WebDriverWait(driver, 25) # 대기 시간 소폭 상향
        # 공고 카드들이 화면에 보일 때까지 대기
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/job/']")))
        time.sleep(5) 

        for scroll_idx in range(10):
            # 1. 개별 공고 카드(상자)를 먼저 리스트로 만듭니다.
            # 매번 새로 찾아서 'StaleElement' 오류를 방지합니다.
            job_cards = driver.find_elements(By.CSS_SELECTOR, "a[href*='/job/']")
            
            for card in job_cards:
                try:
                    # 카드가 화면에서 사라졌을 경우를 대비한 안전장치
                    if not card.is_displayed(): continue
                    
                    href = card.get_attribute("href")
                    
                    # 2. 카드 내부에서 'body-02' 속성을 가진 텍스트만 정확히 추출
                    # 이 방식은 컬럼이 섞이는 문제를 원천적으로 막아줍니다.
                    content_els = card.find_elements(By.CSS_SELECTOR, 'span[data-variant="body-02"]')
                    texts = [el.text.strip() for el in content_els if el.text.strip()]
                    
                    if len(texts) >= 2:
                        # [컬럼 고정] 첫 번째는 회사명, 나머지는 제목
                        company_name = texts[0]
                        job_titles = texts[1:]
                        
                        for title in job_titles:
                            # 날짜 정보나 너무 짧은 텍스트는 필터링
                            if any(x in title for x in ["전", "개월", "일", "주"]) or len(title) < 2:
                                continue
                            
                            data_id = f"{href}_{title}"
                            if data_id not in urls_check:
                                new_data.append({
                                    'company': company_name,
                                    'title': title,
                                    'url': href,
                                    'scraped_at': today
                                })
                                urls_check.add(data_id)
                except Exception:
                    # 개별 카드에서 문제 발생 시 프로그램 중단 없이 다음 카드로 이동
                    continue
            
            # 다음 공고 로딩을 위한 스크롤
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3)
            print(f"🔄 스크롤 {scroll_idx + 1}회 진행 중... (현재 {len(new_data)}건 발견)")

    except Exception as e:
        print(f"❌ 수집 중 오류 발생: {e}")
    finally: 
        driver.quit()
    
    return new_data
    
# [공통] 시트 데이터 업데이트 (기존과 동일)
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
        
        if 'status' in col_map: row[col_map['status']] = 'archived'
        
        rows_to_append.append(row)
    
    if rows_to_append:
        ws.append_rows(rows_to_append)
        print(f"💾 {CONFIG['name']} 신규 공고 {len(rows_to_append)}건 저장 완료")
    else:
        print(f"[{CONFIG['name']}] 시트에 이미 모두 반영되어 있습니다.")

# 메인 실행부 (기존과 동일)
if __name__ == "__main__":
    try:
        ws = get_worksheet()
        data = scrape_projects()
        update_sheet(ws, data)
    except Exception as e:
        print(f"❌ 실행 중 오류 발생: {e}")
