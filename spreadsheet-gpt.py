import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from openai import OpenAI

# -----------------------------
# 1) 기본 설정
# -----------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 시트 정보 하드코딩
SPREADSHEET_ID = "1nKPVCZ6zAOfpqCjV6WfjkzCI55FA9r2yvi9XL3iIneo"
SHEET_NAME = "사이드"

SERVICE_ACCOUNT_FILE = "service_account.json"

client = OpenAI(api_key=OPENAI_API_KEY)

# -----------------------------
# 2) 출력 형식 정의
# -----------------------------
FORMAT_INSTRUCTIONS = """
다음 정보를 바탕으로, 사이드 프로젝트/채용/커뮤니티 정보를 공유하는 짧은 소개 문구를 한국어로 작성해 주세요.

출력 형식:

[회사명 또는 출처] 제목
- 한 줄 요약: (이 글/공고가 어떤 사람에게 좋은지, 1문장 40자 이내)
- 포인트: 2~3개의 핵심 포인트를 불릿으로 정리
- 링크: URL 그대로 붙여쓰기

규칙:
- 톤은 담백하고 정보 전달 위주
- 과한 이모지 사용 금지 (필요하면 1개 이하)
- 마크다운 문법(#, *, - 등)은 위에 제시된 정도만 사용
"""

# -----------------------------
# 3) Google Sheets 서비스
# -----------------------------
def get_sheet_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds)

# -----------------------------
# 4) 시트 읽기 (A2:G)
# -----------------------------
def read_sheet():
    service = get_sheet_service()
    range_ = f"{SHEET_NAME}!A2:G"
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=range_
    ).execute()
    return result.get("values", [])

# -----------------------------
# 5) OpenAI 호출
# -----------------------------
def generate_output(title, subtitle, url, created_at, company):
    base_info = f"""
제목: {title}
부제목: {subtitle}
회사/출처: {company}
생성일: {created_at}
URL: {url}
"""

    prompt = f"""
아래는 어떤 사이드 프로젝트/채용/커뮤니티 관련 글의 메타데이터입니다.

{base_info}

---

이 정보를 바탕으로, 아래 조건을 지켜서 소개 문구를 작성해 주세요:

{FORMAT_INSTRUCTIONS}
"""

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": "You write concise Korean copy for sharing interesting opportunities in a fixed format.",
            },
            {"role": "user", "content": prompt},
        ],
    )

    return completion.choices[0].message.content.strip()

# -----------------------------
# 6) 시트 업데이트
# -----------------------------
def update_sheet(rows):
    service = get_sheet_service()
    range_ = f"{SHEET_NAME}!A2:G"
    body = {"values": rows}
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=range_,
        valueInputOption="RAW",
        body=body,
    ).execute()

# -----------------------------
# 7) 메인 로직
# -----------------------------
def main():
    rows = read_sheet()
    updated = []

    for row in rows:
        # row 길이가 7보다 짧으면 빈 칸으로 패딩
        while len(row) < 7:
            row.append("")

        title = row[0]      # A: title
        subtitle = row[1]   # B: subtitle
        url = row[2]        # C: url
        created_at = row[3] # D: created_at
        company = row[4]    # E: company
        status = row[5]     # F: status (지금은 사용 X)
        publish = row[6]    # G: publish (여기에 결과 기록)

        # title이 있고, publish가 비어 있는 행만 처리
        if title and not publish:
            generated = generate_output(title, subtitle, url, created_at, company)
            row[6] = generated  # G열 publish에 기록

        updated.append(row)

    update_sheet(updated)
    print("✔ Daily automation completed for sheet:", SHEET_NAME)

if __name__ == "__main__":
    main()
