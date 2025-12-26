import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests
from bs4 import BeautifulSoup
from openai import OpenAI


def ensure_env_var(name: str) -> str:
    if name not in os.environ:
        raise EnvironmentError(f"환경변수 {name}가 없습니다.")
    return os.environ[name]


def main():
    print("--- [Mix Sender] 시작 ---")
    key_points = []
    recommendations = []

    try:
        google_credentials = ensure_env_var("GOOGLE_CREDENTIALS")
        openai_api_key = ensure_env_var("OPENAI_API_KEY")
        slack_webhook_url = ensure_env_var("SLACK_WEBHOOK_URL")

        creds_dict = json.loads(google_credentials)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)

        spreadsheet = client.open('플린트스토닝 소재 DB')

        try:
            # sheet = spreadsheet.worksheet('실제_탭_이름')  # <- 가장 권장하는 방식
            sheet = spreadsheet.get_worksheet(2)
            print(f"📂 연결된 시트: {sheet.title}")
        except Exception as e:  # noqa: PERF203 - 명확한 오류 메시지 필요
            raise RuntimeError(f"❌ 시트를 찾을 수 없습니다: {e}")

        data = sheet.get_all_values()
        if not data:
            print("데이터가 없습니다.")
            return

        headers = data.pop(0)
        df = pd.DataFrame(data, columns=headers)
        df.columns = df.columns.str.strip()

        COL_STATUS = 'status'
        COL_PUBLISH = 'publish'
        COL_TITLE = 'title'
        COL_URL = 'url'

        required_cols = [COL_STATUS, COL_PUBLISH, COL_TITLE, COL_URL]
        for col in required_cols:
            if col not in df.columns:
                raise KeyError(f"❌ 오류: 시트에 '{col}' 헤더가 없습니다.")

        condition = (
            df[COL_STATUS].str.strip().str.lower() == 'archived'
        ) & (
            df[COL_PUBLISH].str.strip().str.upper() == 'TRUE'
        )
        target_rows = df[condition]

        if target_rows.empty:
            print("ℹ️ 발송할 대상(archived & publish=TRUE)이 없습니다.")
            return

        row = target_rows.iloc[0]
        update_row_index = row.name + 2

        project_title = row[COL_TITLE]
        target_url = row[COL_URL]

        print(f"▶ 선택된 행 번호: {update_row_index}")
        print(f"▶ 제목: {project_title}")
        print(f"▶ URL: {target_url}")

        print("--- 스크래핑 시작 ---")
        headers_ua = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(target_url, headers=headers_ua, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        article = soup.find('article')
        if article:
            paragraphs = article.find_all('p')
        else:
            paragraphs = soup.find_all('p')

        text_list = [p.get_text().strip() for p in paragraphs if p.get_text().strip()]
        full_text = " ".join(text_list)

        if len(full_text) < 50:
            print("⚠️ 본문 내용이 너무 짧습니다. (스크래핑 실패 가능성)")

        truncated_text = full_text[:3000]

        print("--- GPT 요약 요청 ---")
        client_openai = OpenAI(api_key=openai_api_key)

        gpt_prompt = f"""
        너는 IT/테크 트렌드를 분석해주는 '인사이트 큐레이터'야.
        아래 [글 내용]을 읽고, 팀원들에게 공유할 수 있게 핵심 내용을 요약해줘.

        [출력 양식 (반드시 아래 JSON 형식으로만 응답할 것)]
        {{
          "key_points": ["핵심 내용 1", "핵심 내용 2", "핵심 내용 3", "핵심 내용 4"],
          "recommendations": ["추천 이유 1", "추천 이유 2", "추천 이유 3"]
        }}

        [글 내용]
        {truncated_text}
        """

        completion = client_openai.chat.completions.create(
            model="gpt-3.5-turbo-0125",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a helpful assistant that outputs JSON."},
                {"role": "user", "content": gpt_prompt},
            ],
        )

        try:
            gpt_res = json.loads(completion.choices[0].message.content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"GPT 응답을 JSON으로 파싱하지 못했습니다: {exc}")

        key_points = gpt_res.get("key_points", [])
        recommendations = gpt_res.get("recommendations", [])

        print("--- 슬랙 전송 시작 (Block Kit) ---")

        key_points_text = "\n".join([f"• {point}" for point in key_points])
        recommend_text = "\n".join([f"• {rec}" for rec in recommendations])

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "지금 주목해야 할 아티클",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{project_title}*",  # 제목 강조
                },
            },
            {
                "type": "divider",  # 구분선
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"📌 *이 글에서 이야기하는 것들*\n{key_points_text}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"📌 *이런 분께 추천해요*\n{recommend_text}",
                },
            },
            {
                "type": "divider",
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "아티클 보러가기",
                            "emoji": True,
                        },
                        "style": "primary",  # 초록색 버튼
                        "url": target_url,
                    },
                ],
            },
        ]

        slack_res = requests.post(slack_webhook_url, json={"blocks": blocks}, timeout=10)
        if slack_res.status_code == 200:
            print("✅ 슬랙 전송 성공!")
        else:
            raise RuntimeError(
                f"❌ 슬랙 전송 실패 (status={slack_res.status_code}): {slack_res.text}"
            )

    except Exception as error:
        print(f"❌ 오류 발생: {error}")


if __name__ == "__main__":
    main()
