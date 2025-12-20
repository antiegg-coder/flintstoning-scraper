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
    
    # 아티팩트(결과물) 저장을 위한 디렉토리 생성
    # GitHub Actions에서 이 경로에 저장된 파일을 아티팩트로 업로드합니다.
    output_dir = "screenshots"
    os.makedirs(output_dir, exist_ok=True)

    try:
        driver.get(CONFIG["url"])
        wait = WebDriverWait(driver, 20)
        
        try:
            print("⏳ 공고 데이터 로딩 대기 중...")
            # 'job' 링크를 가진 공고 카드가 나타날 때까지 대기
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/job/']")))
            time.sleep(3) # 로딩 후 안정화 시간
            print("✅ 로딩 완료: 데이터를 수집합니다.")
        except:
            print("⚠️ 로딩 대기 시간이 초과되었습니다. 현재 상태에서 수집을 시도합니다.")
        
        # --- [추가된 부분] 스크린샷 찍기 ---
        screenshot_path = os.path.join(output_dir, f"offercent_page_{today}.png")
        driver.save_screenshot(screenshot_path)
        print(f"📸 현재 페이지 스크린샷 저장: {screenshot_path}")
        # --- [추가된 부분 끝] ---

        for _ in range(10): # 스크롤 및 수집 반복 횟수
            cards = driver.find_elements(By.TAG_NAME, "a")
            # 디버깅을 위해 찾은 카드 개수 출력
            print(f"DEBUG: 현재 페이지에서 찾은 'a' 태그 수: {len(cards)}")
            
            for card in cards:
                href = card.get_attribute("href")
                if not href or "/job/" not in href: continue
                
                try:
                    elements = card.find_elements(By.CSS_SELECTOR, 'span[data-variant="body-02"]')
                    if not elements: continue

                    texts = [el.text.strip() for el in elements if el.text.strip()]
                    
                    if len(texts) >= 2:
                        company = texts[0]
                        titles = texts[1:]
                        
                        for title in titles:
                            if any(x in title for x in ["전", "개월", "일", "주"]) or len(title) < 2:
                                continue
                            
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
                    # 어떤 예외가 발생했는지 출력 (디버깅용)
                    # print(f"DEBUG: 카드 처리 중 오류 발생: {e}")
                    continue
            
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3) 

    except Exception as e:
        print(f"❌ 스크래핑 과정에서 치명적인 오류 발생: {e}")
    finally: 
        driver.quit()
    
    print(f"📊 최종 수집된 공고 데이터 후보 건수: {len(new_data)}건")
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
        
        if 'status' in col_map: row[col_map['status']] = 'new'
        
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
