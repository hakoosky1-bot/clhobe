"""
SERP Monitor v1.0 — 네이버 SERP 분석 + 플랫폼 점유율 + 신규 도메인 캐치
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

매일 새벽 6시 자동:
1. 키워드 42개 네이버 검색 (블로그·카페·웹문서 API)
2. 도메인 추출 → 플랫폼 분류
3. 신규 도메인 캐치 (화이트리스트 외)
4. 점유율 변화 추적
5. Google Sheets에 4개 시트로 정리:
   - 결과: 키워드별 점유율
   - 플랫폼점유율: 일별 누적
   - 신규도메인: 처음 본 도메인
   - 인사이트: 한국어 자동 해석
"""
import os
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from collections import Counter, defaultdict

from google.oauth2 import service_account
from googleapiclient.discovery import build

# ============================================================
# 설정
# ============================================================
SHEET_ID = "1_z1uVey30SD5FjbSL5wJMDYNJfKvCeo883H6SsmjAeU"

KEYWORDS = [
    "맞고", "유튜브", "날씨", "아이하이 영양제", "김치",
    "바둑게임", "장기게임", "기타튜닝", "트로트", "노래교실",
    "파크골프", "게이트볼", "무료게임", "포커", "시놀",
    "영양제", "갱년기", "인공관절", "척추관협착증", "도수치료", "한약",
    "요양원", "보청기", "임플란트", "백내장",
    "이혼", "변호사", "사돈 호칭", "49재 비용", "축문 양식",
    "대출", "주택연금", "농지연금", "상속세", "종신보험", "실손보험", "치매신탁",
    "노인일자리", "취업", "효도여행", "벌초 대행", "국세청 홈페이지",
]

# 플랫폼 분류 (도메인 → 플랫폼 이름)
PLATFORM_MAP = {
    "blog.naver.com": "네이버블로그",
    "m.blog.naver.com": "네이버블로그",
    "cafe.naver.com": "네이버카페",
    "m.cafe.naver.com": "네이버카페",
    "post.naver.com": "네이버포스트",
    "m.post.naver.com": "네이버포스트",
    "kin.naver.com": "지식iN",
    "m.kin.naver.com": "지식iN",
    "contents.premium.naver.com": "네프콘",
    "tv.naver.com": "네이버TV",
    "brunch.co.kr": "브런치",
    "tistory.com": "티스토리",
    "youtube.com": "유튜브",
    "m.youtube.com": "유튜브",
    "youtu.be": "유튜브",
    "instagram.com": "인스타그램",
    "facebook.com": "페이스북",
    "x.com": "X(트위터)",
    "twitter.com": "X(트위터)",
    "namu.wiki": "나무위키",
    "ko.wikipedia.org": "위키백과",
    "dcinside.com": "디시인사이드",
    "fmkorea.com": "에펨코리아",
    "ppomppu.co.kr": "뽐뿌",
    "ruliweb.com": "루리웹",
    "clien.net": "클리앙",
}

# 알려진 도메인 (신규로 분류하지 않을 거)
KNOWN_DOMAINS = set(PLATFORM_MAP.keys()) | {
    "naver.com", "m.naver.com", "search.naver.com", "m.search.naver.com",
    "daum.net", "m.daum.net", "search.daum.net",
    "google.com", "google.co.kr",
    "youtube.com",
    "wikipedia.org",
}

# ============================================================
# 네이버 API 호출
# ============================================================
NAVER_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_SECRET = os.environ["NAVER_CLIENT_SECRET"]

def naver_search(category: str, query: str, display: int = 20):
    """네이버 검색 API 호출 (블로그/카페/웹문서)"""
    url = f"https://openapi.naver.com/v1/search/{category}.json?query={urllib.parse.quote(query)}&display={display}"
    req = urllib.request.Request(url)
    req.add_header("X-Naver-Client-Id", NAVER_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_SECRET)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"items": [], "_error": str(e)}

def extract_domain(url: str) -> str:
    """URL → 도메인 추출"""
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    if not m:
        return ""
    domain = m.group(1).lower()
    # 티스토리 서브도메인은 통합
    if domain.endswith(".tistory.com"):
        return "tistory.com"
    return domain

def classify_platform(domain: str) -> str:
    """도메인 → 플랫폼 이름"""
    if domain in PLATFORM_MAP:
        return PLATFORM_MAP[domain]
    if domain.endswith(".tistory.com") or domain == "tistory.com":
        return "티스토리"
    return "기타"

# ============================================================
# 키워드 1개 분석
# ============================================================
def analyze_keyword(keyword: str) -> dict:
    """키워드 1개 → 플랫폼별 노출 수 + 도메인 리스트"""
    all_domains = []
    
    # 블로그 + 카페 + 웹문서 3개 영역 통합
    for category in ["blog", "cafearticle", "webkr"]:
        result = naver_search(category, keyword, display=20)
        for item in result.get("items", []):
            link = item.get("link", "")
            domain = extract_domain(link)
            if domain:
                all_domains.append(domain)
        time.sleep(0.1)  # API 부담 X
    
    # 플랫폼별 카운트
    platform_count = Counter(classify_platform(d) for d in all_domains)
    
    # 신규 도메인 (알려진 거 + 기타 도메인 둘 다 추출)
    unique_domains = set(all_domains)
    new_domains = [d for d in unique_domains if d not in KNOWN_DOMAINS]
    
    return {
        "keyword": keyword,
        "total": len(all_domains),
        "platforms": dict(platform_count),
        "all_domains": all_domains,
        "new_domains": new_domains,
    }

# ============================================================
# Google Sheets 연결
# ============================================================
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
creds = service_account.Credentials.from_service_account_info(
    json.loads(SA_JSON),
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)
sheets = build("sheets", "v4", credentials=creds).spreadsheets()

def ensure_sheet(title: str, headers: list):
    """시트가 없으면 만들고 헤더 박기"""
    meta = sheets.get(spreadsheetId=SHEET_ID).execute()
    existing = [s["properties"]["title"] for s in meta["sheets"]]
    if title not in existing:
        sheets.batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        ).execute()
        sheets.values().update(
            spreadsheetId=SHEET_ID,
            range=f"{title}!A1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()

def append_rows(title: str, rows: list):
    """시트에 행 추가"""
    if not rows:
        return
    sheets.values().append(
        spreadsheetId=SHEET_ID,
        range=f"{title}!A:Z",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()

def read_all(title: str):
    """시트 전체 읽기 → 2D 리스트"""
    try:
        res = sheets.values().get(spreadsheetId=SHEET_ID, range=f"{title}!A:Z").execute()
        return res.get("values", [])
    except Exception:
        return []

# ============================================================
# 메인 실행
# ============================================================
def main():
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*60}\n  SERP Monitor v1.0 — {now}\n{'='*60}\n")
    
    # 시트 준비
    ensure_sheet("결과", ["날짜", "키워드", "총노출", "네이버블로그%", "네이버카페%", "티스토리%", "브런치%", "네프콘%", "지식iN%", "유튜브%", "기타%", "1위플랫폼"])
    ensure_sheet("플랫폼점유율", ["날짜", "네이버블로그", "네이버카페", "티스토리", "브런치", "네프콘", "지식iN", "유튜브", "기타"])
    ensure_sheet("신규도메인", ["발견일자", "도메인", "발견키워드", "노출횟수"])
    ensure_sheet("인사이트", ["날짜", "한줄요약", "1위플랫폼", "급상승", "급하락", "신규도메인수", "메모"])
    
    # 어제 데이터 (변화 비교용)
    yesterday_data = read_all("플랫폼점유율")
    yesterday_platforms = {}
    if len(yesterday_data) > 1:
        last_row = yesterday_data[-1]
        headers = yesterday_data[0]
        for i, h in enumerate(headers[1:], 1):  # 첫 열은 날짜
            try:
                yesterday_platforms[h] = float(last_row[i].replace("%", "")) if i < len(last_row) else 0
            except (ValueError, AttributeError, IndexError):
                yesterday_platforms[h] = 0
    
    # 이미 본 신규 도메인 (중복 X)
    known_new_data = read_all("신규도메인")
    already_seen_new = set()
    if len(known_new_data) > 1:
        already_seen_new = {row[1] for row in known_new_data[1:] if len(row) > 1}
    
    # 키워드별 분석
    print(f"키워드 {len(KEYWORDS)}개 분석 중...\n")
    all_results = []
    all_new_domains = defaultdict(lambda: {"count": 0, "keywords": set()})
    total_platform_count = Counter()
    
    for i, kw in enumerate(KEYWORDS, 1):
        print(f"  [{i:2}/{len(KEYWORDS)}] {kw:18s}", end=" → ", flush=True)
        result = analyze_keyword(kw)
        all_results.append(result)
        
        # 전체 플랫폼 카운트 누적
        for plat, cnt in result["platforms"].items():
            total_platform_count[plat] += cnt
        
        # 신규 도메인 누적
        for d in result["new_domains"]:
            all_new_domains[d]["count"] += result["all_domains"].count(d)
            all_new_domains[d]["keywords"].add(kw)
        
        # 1위 플랫폼
        if result["platforms"]:
            top_plat = max(result["platforms"], key=result["platforms"].get)
            top_pct = result["platforms"][top_plat] / result["total"] * 100 if result["total"] else 0
            print(f"총 {result['total']}건, 1위 {top_plat} ({top_pct:.0f}%)")
        else:
            print("결과 없음")
        
        time.sleep(0.2)
    
    # ========== 결과 시트 ==========
    result_rows = []
    for r in all_results:
        total = r["total"] if r["total"] else 1  # 0 나누기 방지
        p = r["platforms"]
        top_plat = max(p, key=p.get) if p else "-"
        row = [
            today, r["keyword"], r["total"],
            f"{p.get('네이버블로그', 0)/total*100:.0f}%",
            f"{p.get('네이버카페', 0)/total*100:.0f}%",
            f"{p.get('티스토리', 0)/total*100:.0f}%",
            f"{p.get('브런치', 0)/total*100:.0f}%",
            f"{p.get('네프콘', 0)/total*100:.0f}%",
            f"{p.get('지식iN', 0)/total*100:.0f}%",
            f"{p.get('유튜브', 0)/total*100:.0f}%",
            f"{p.get('기타', 0)/total*100:.0f}%",
            top_plat,
        ]
        result_rows.append(row)
    append_rows("결과", result_rows)
    
    # ========== 플랫폼점유율 시트 (오늘 합산) ==========
    total_count = sum(total_platform_count.values()) or 1
    today_platforms = {
        "네이버블로그": total_platform_count.get("네이버블로그", 0) / total_count * 100,
        "네이버카페": total_platform_count.get("네이버카페", 0) / total_count * 100,
        "티스토리": total_platform_count.get("티스토리", 0) / total_count * 100,
        "브런치": total_platform_count.get("브런치", 0) / total_count * 100,
        "네프콘": total_platform_count.get("네프콘", 0) / total_count * 100,
        "지식iN": total_platform_count.get("지식iN", 0) / total_count * 100,
        "유튜브": total_platform_count.get("유튜브", 0) / total_count * 100,
        "기타": total_platform_count.get("기타", 0) / total_count * 100,
    }
    append_rows("플랫폼점유율", [[today] + [f"{v:.1f}%" for v in today_platforms.values()]])
    
    # ========== 신규도메인 시트 (처음 본 거만) ==========
    new_today = []
    for domain, info in all_new_domains.items():
        if domain in already_seen_new:
            continue
        new_today.append([
            today, domain,
            ", ".join(sorted(info["keywords"])[:5]),
            info["count"],
        ])
    new_today.sort(key=lambda x: -x[3])  # 노출 많은 순
    append_rows("신규도메인", new_today)
    
    # ========== 인사이트 시트 (한국어 자동 해석) ==========
    insights = []
    
    # 1위 플랫폼
    top_platform = max(today_platforms, key=today_platforms.get)
    top_pct = today_platforms[top_platform]
    
    # 급상승/급하락 (전날 대비 3%p 이상)
    rising = []
    falling = []
    if yesterday_platforms:
        for plat, today_pct in today_platforms.items():
            y_pct = yesterday_platforms.get(plat, today_pct)
            diff = today_pct - y_pct
            if diff >= 3:
                rising.append((plat, diff))
            elif diff <= -3:
                falling.append((plat, diff))
    
    rising.sort(key=lambda x: -x[1])
    falling.sort(key=lambda x: x[1])
    
    # 한 줄 요약
    if not yesterday_platforms:
        summary = f"📊 첫 측정일. 오늘 1위 플랫폼은 {top_platform} ({top_pct:.0f}%). 내일부터 변화 추적 가능."
    elif rising:
        summary = f"🔥 {rising[0][0]} 점유율 +{rising[0][1]:.1f}%p 급상승. 발행 채널 후보로 고려."
    elif falling:
        summary = f"📉 {falling[0][0]} 점유율 {falling[0][1]:.1f}%p 하락. 채널 효율 점검 필요."
    else:
        summary = f"📊 큰 변화 없음. 1위 플랫폼 {top_platform} ({top_pct:.0f}%) 유지."
    
    rising_text = ", ".join(f"{p}+{d:.1f}%p" for p, d in rising[:3]) if rising else "없음"
    falling_text = ", ".join(f"{p}{d:.1f}%p" for p, d in falling[:3]) if falling else "없음"
    
    # 메모 (해석)
    notes = []
    if len(new_today) > 0:
        notes.append(f"신규 도메인 {len(new_today)}개 발견 → '신규도메인' 시트 확인")
    if top_pct > 30:
        notes.append(f"{top_platform} 압도적 1위 — 이 채널 발행 효율 최상")
    if today_platforms.get("기타", 0) > 25:
        notes.append("'기타' 비중 높음 → 신규 플랫폼 등장 가능성. 신규도메인 시트 확인")
    if not notes:
        notes.append("특이사항 없음")
    
    append_rows("인사이트", [[
        today, summary, f"{top_platform} ({top_pct:.0f}%)",
        rising_text, falling_text, len(new_today),
        " / ".join(notes),
    ]])
    
    # ========== 콘솔 요약 ==========
    print(f"\n{'='*60}")
    print(f"  완료. 결과: https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit")
    print(f"{'='*60}")
    print(f"  오늘 1위: {top_platform} ({top_pct:.1f}%)")
    print(f"  급상승: {rising_text}")
    print(f"  급하락: {falling_text}")
    print(f"  신규도메인: {len(new_today)}개")
    print(f"  한 줄 요약: {summary}\n")


if __name__ == "__main__":
    main()
