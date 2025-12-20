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
    
    # 디버깅용 스크린샷 저장 경로
    output_dir = "screenshots"
    os.makedirs(output_dir, exist_ok=True)

    try:
        driver.get(CONFIG["url"])
        
        # 1. 공고 데이터 로딩 대기 (최대 20초)
        wait = WebDriverWait(driver, 20)
        try:
            print("⏳ 모바일 레이아웃 데이터 로딩 대기 중...")
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'span[data-variant="body-02"]')))
            time.sleep(5) # 렌더링 안정을 위한 추가 시간
        except:
            print("⚠️ 로딩 시간이 초과되었습니다. 현재 화면에서 수집을 시도합니다.")

        # 진단용 스크린샷 찍기
        driver.save_screenshot(os.path.join(output_dir, f"offercent_check_{today}.png"))

        # 2. 스크롤하며 데이터 수집 (최대 10회)
        for scroll_idx in range(10):
            # 핵심 타겟: 회사명과 제목이 공통으로 사용하는 속성
            elements = driver.find_elements(By.CSS_SELECTOR, 'span[data-variant="body-02"]')
            
            # 스크린샷 구조 분석 결과: [회사명, 제목, 회사명, 제목...] 순서로 배치됨
            # 2개씩 짝을 지어 처리 (Step 2)
            for i in range(0, len(elements) - 1, 2):
                try:
                    company_el = elements[i]
                    title_el = elements[i+1]
                    
                    company_txt = company_el.text.strip()
                    title_txt = title_el.text.strip()

                    # 데이터 정제: 날짜 정보가 제목으로 들어오는 것 방지
                    if any(x in title_txt for x in ["전", "개월", "일", "주"]) or len(title_txt) < 2:
                        continue
                    
                    # 제목 바로 위의 부모 'a' 태그에서 링크 추출
                    # 모바일 구조상 제목을 감싸는 가장 가까운 링크를 찾습니다.
                    try:
                        href = title_el.find_element(By.XPATH, "./ancestor::a").get_attribute("href")
                    except:
                        href = CONFIG["url"]

                    # 중복 체크 및 저장
                    data_id = f"{href}_{title_txt}"
                    if data_id not in urls_check:
                        new_data.append({
                            'company': company_txt,
                            'title': title_txt,
                            'url': href,
                            'scraped_at': today
                        })
                        urls_check.add(data_id)
                except:
                    continue
            
            # 다음 공고를 위해 스크롤 다운
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3)
            print(f"🔄 스크롤 {scroll_idx + 1}회 완료 (현재까지 발견: {len(new_data)}건)")

    except Exception as e:
        print(f"❌ 수집 중 오류 발생: {e}")
    finally: 
        driver.quit()
    
    print(f"📊 최종 수집 성공: {len(new_data)}건")
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
