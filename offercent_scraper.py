def get_projects():
    driver = get_driver()
    new_data = []
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        print("🌐 오퍼센트 접속 중...")
        driver.get(SCRAPE_URL)
        
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(5) 

        # 스크롤 다운
        for i in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
        
        elements = driver.find_elements(By.TAG_NAME, "a")
        print(f"🔍 발견된 전체 링크 수: {len(elements)}개")

        # 무시할 키워드 리스트
        SKIP_KEYWORDS = ["채용 중인 공고", "채용마감", "마감임박", "상시채용", "NEW", "D-"]

        for idx, elem in enumerate(elements):
            try:
                full_url = elem.get_attribute("href")
                if not full_url or full_url == SCRAPE_URL: continue
                
                raw_text = elem.text.strip()
                if not raw_text: continue

                lines = raw_text.split('\n')
                
                # [디버깅] 처음 5개 링크는 무조건 원본 텍스트를 출력해서 확인
                if idx < 5:
                    print(f"----- [링크 {idx}] 원본 텍스트 분석 -----")
                    print(f"URL: {full_url}")
                    print(f"줄바꿈 포함 내용: {lines}")

                cleaned_lines = []
                for line in lines:
                    text = line.strip()
                    if not text: continue
                    
                    # 키워드가 '포함'만 되어도 과감히 삭제
                    is_bad = False
                    for kw in SKIP_KEYWORDS:
                        if kw in text:
                            is_bad = True
                            break
                    
                    if not is_bad:
                        cleaned_lines.append(text)
                
                if idx < 5:
                    print(f"필터링 후 내용: {cleaned_lines}")

                # 데이터가 너무 적으면 스킵
                if len(cleaned_lines) < 2:
                    continue

                # 순서 결정 로직 (회사명 vs 제목)
                # 오퍼센트는 보통 [회사명, 제목] 순서임
                company = cleaned_lines[0]
                title = cleaned_lines[1]
                
                # 만약 제목이 너무 짧으면(3글자 이하) 그 다음 줄이 제목일 수 있음
                if len(title) <= 3 and len(cleaned_lines) > 2:
                    title = cleaned_lines[2]

                # 최종 저장
                if len(title) > 1 and len(company) > 1:
                    # 중복 체크
                    if not any(d['url'] == full_url for d in new_data):
                        new_data.append({
                            'title': title,
                            'company': company,
                            'url': full_url,
                            'scraped_at': today
                        })
                        # 수집 성공 시 로그 출력
                        # print(f"  ✅ 수집 성공: {title} / {company}")

            except Exception as e:
                print(f"⚠️ 파싱 에러: {e}")
                continue
                
    except Exception as e:
        print(f"❌ 크롤링 에러: {e}")
    finally:
        driver.quit()
            
    print(f"🎯 최종 수집된 공고: {len(new_data)}개")
    return new_data
