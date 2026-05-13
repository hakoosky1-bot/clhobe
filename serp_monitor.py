"""
SERP Monitor v0.1 — Sheets 연결 테스트
이거 작동하면 다음 단계로 진짜 SERP 분석 추가
"""
import os
import json
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
SHEET_ID = "1_z1uVey30SD5FjbSL5wJMDYNJfKvCeo883H6SsmjAeU"

creds = service_account.Credentials.from_service_account_info(
    json.loads(SA_JSON),
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)
service = build("sheets", "v4", credentials=creds)

# 시트 첫 번째 탭 이름 자동 감지 (한글/영문 상관없이)
meta = service.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
first_sheet = meta["sheets"][0]["properties"]["title"]

# 현재 시각 + 메시지 기록
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
row = [[now, "🎉 SERP Monitor 첫 작동 성공!", "다음 단계: 네이버 SERP 분석 추가 예정"]]

service.spreadsheets().values().append(
    spreadsheetId=SHEET_ID,
    range=f"{first_sheet}!A:C",
    valueInputOption="RAW",
    insertDataOption="INSERT_ROWS",
    body={"values": row},
).execute()

print(f"✅ 시트에 행 추가 완료: {now}")
print(f"   URL: https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit")
