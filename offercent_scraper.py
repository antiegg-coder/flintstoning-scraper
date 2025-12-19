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
    "gid": "639559541" # 오퍼센트 탭 GID
}

# [공통] 시트 연결 (GID로 찾기)
def get_worksheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    # GitHub Actions의 Secrets 등에 저장된 JSON 인증 정보 로드
    creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1nKPVCZ6zAOfpqCjV6WfjkzCI55FA9r2yvi9XL3iIneo/edit")
    
    # GID를 기준으로 워크시트 선택 (탭 이름 변경 대비)
    sheet = next((s for s in spreadsheet.worksheets() if str(s.id) == CONFIG["gid"]), None)
    if not sheet: raise Exception(f"{CONFIG['gid']} 시트를 못 찾았습니다.")
    return sheet

# [공통] 브라우저 실행 설정
def get_driver():
    options = Options()
    options.add_argument("--headless") # 창 없이 실행
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    driver = webdriver.Chrome(options=options)
    # 봇 탐지 우회 설정
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
        
        # 페이지 로딩 대기
        wait = WebDriverWait(driver, 15)
        try:
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except:
            pass # 타임아웃 시에도 일단 진행
            
        time.sleep(5) # 동적 콘텐츠 로딩을 위한 추가 대기

        # 무한 스크롤 형태 대응 (최대 10회 스크롤)
        for _ in range(10):
            # 모든 공고 카드(a 태그) 추출
            cards = driver.find_elements(By.TAG_NAME, "a")
            
            for card in cards:
                href = card.get_attribute("href")
                if not href or "/job/" not in href: continue
                
                try:
                    # 'body-02' 변형 속성을 가진 span들이 회사명과 제목을 담고 있음
                    elements = card.find_elements(By.CSS_SELECTOR, 'span[data-variant="body-02"]')
                    if not elements: continue

                    # 텍스트 추출 및 정제
                    texts = [el.text.strip() for el in elements if el.text.strip()]
                    
                    if len(texts) >= 2:
                        company = texts[0]  # 첫 번째 span은 회사명
                        titles = texts[1:]  # 이후 span들은 해당 카드의 공고 제목들
                        
                        for title in titles:
                            # '6일 전', '1개월 이상' 등의 날짜/기간 키워드 필터링
                            if any(x in title for x in ["전", "개월", "일", "주"]): continue
                            
                            # 중복 수집 방지 (동일 URL + 동일 제목 조합)
                            data_id = f"{href}_{title}"
                            if data_id not in urls_check:
                                new_data.append({
                                    'company': company,
                                    'title': title,
                                    'url': href,
                                    'scraped_at': today
                                })
                                urls_check.add(data_id)
                except:
                    continue
            
            # 스크롤 다운하여 추가 데이터 로드
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
    
    # 헤더 인덱스 매핑
    col_map = {name: i for i, name in enumerate(headers)}
    # 기존 시트에 저장된 URL 목록 (중복 저장 방지용)
    existing_urls = {row[col_map['url']] for row in all_v[1:] if len(row) > col_map['url']}
    
    rows_to_append = []
    for item in data:
        # 이미 존재하는 URL은 제외
        if item['url'] in existing_urls: continue
        
        # 헤더 순서에 맞춰 리스트 생성
        row = [''] * len(headers)
        for k, v in item.items():
            if k in col_map: row[col_map[k]] = v
        
        # 상태값 기본 설정 (예: archived)
        if 'status' in col_map: row[col_map['status']] = 'new'
        
        rows_to_append.append(row)
    
    if rows_to_append:
        ws.append_rows(rows_to_append)
        print(f"💾 {CONFIG['name']} 신규 공고 {len(rows_to_append)}건 저장 완료")
    else:
        print(f"[{CONFIG['name']}] 시트에 이미 모두 반영되어 있습니다.")

# 메인 실행부
if __name__ == "__main__":
    try:
        ws = get_worksheet()
        data = scrape_projects()
        update_sheet(ws, data)
    except Exception as e:
        print(f"❌ 실행 중 오류 발생: {e}")
