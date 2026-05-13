"""
SERP Monitor v2.0 — 풀 시스템
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

기능:
1. 시드 키워드 42개 → 자동완성으로 연관검색어 자동 발굴 (풀 확장)
2. 각 키워드 네이버 검색 API 분석 (블로그/카페/웹문서)
3. 네이버 모바일 통합검색 HTML 크롤링 (광고/AI브리핑/VIEW 영역 분석)
4. 신규 도메인 캐치
5. 광고주 풀 추적 (RPM 본진 키워드 발견)
6. Google Sheets에 7개 시트:
   - 결과: 키워드별 점유율
   - 플랫폼점유율: 일별 누적
   - 신규도메인: 처음 본 도메인
   - 연관검색어: 자동 발굴된 키워드
   - 영역별점유율: SERP 영역 분석 (광고/AI브리핑/VIEW)
   - 광고주풀: 광고 영역 입찰 도메인
   - 인사이트: 한국어 자동 해석
"""
import os
import json
import re
import time
import random
import urllib.parse
import urllib.request
from datetime import datetime
from collections import Counter, defaultdict

from google.oauth2 import service_account
from googleapiclient.discovery import build

# ============================================================
# 설정
# ============================================================
SHEET_ID = "1_z1uVey30SD5FjbSL5wJMDYNJfKvCeo883H6SsmjAeU"

SEED_KEYWORDS = [
    "맞고", "유튜브", "날씨", "아이하이 영양제", "김치",
    "바둑게임", "장기게임", "기타튜닝", "트로트", "노래교실",
    "파크골프", "게이트볼", "무료게임", "포커", "시놀",
    "영양제", "갱년기", "인공관절", "척추관협착증", "도수치료", "한약",
    "요양원", "보청기", "임플란트", "백내장",
    "이혼", "변호사", "사돈 호칭", "49재 비용", "축문 양식",
    "대출", "주택연금", "농지연금", "상속세", "종신보험", "실손보험", "치매신탁",
    "노인일자리", "취업", "효도여행", "벌초 대행", "국세청 홈페이지",
]

# 시드 키워드 1개당 자동완성 몇 개 가져올지 (너무 많으면 API 부하)
AUTOCOMPLETE_PER_KEYWORD = 5

# 풀 확장 후 분석할 최대 키워드 수 (안전장치)
MAX_TOTAL_KEYWORDS = 200

# 플랫폼 분류
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
}

KNOWN_DOMAINS = set(PLATFORM_MAP.keys()) | {
    "naver.com", "m.naver.com", "search.naver.com", "m.search.naver.com",
    "daum.net", "m.daum.net", "search.daum.net",
    "google.com", "google.co.kr",
    "wikipedia.org",
}

# ============================================================
# 환경변수
# ============================================================
NAVER_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_SECRET = os.environ["NAVER_CLIENT_SECRET"]
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

# ============================================================
# 자동완성 (연관검색어 자동 수집)
# ============================================================
def fetch_autocomplete(keyword: str, max_n: int = 5) -> list:
    """네이버 자동완성 API → 연관검색어 리스트"""
    url = f"https://ac.search.naver.com/nx/ac?q={urllib.parse.quote(keyword)}&con=1&frm=nv&ans=2&r_format=json&r_enc=UTF-8&r_unicode=0&t_koreng=1&run=2&rev=4&q_enc=UTF-8&st=100"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
            "Referer": "https://m.naver.com/",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # data['items'][0] = 자동완성 리스트
        items = data.get("items", [[]])[0]
        suggestions = []
        for item in items[:max_n]:
            if isinstance(item, list) and len(item) > 0:
                kw = item[0]
                if kw and kw != keyword and kw not in suggestions:
                    suggestions.append(kw)
        return suggestions
    except Exception as e:
        print(f"    ⚠️ 자동완성 실패 ({keyword}): {str(e)[:50]}")
        return []

# ============================================================
# 네이버 검색 API
# ============================================================
def naver_search_api(category: str, query: str, display: int = 20):
    """네이버 검색 API (블로그/카페/웹문서)"""
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
    """URL → 도메인"""
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    if not m:
        return ""
    domain = m.group(1).lower()
    if domain.endswith(".tistory.com"):
        return "tistory.com"
    return domain

def classify_platform(domain: str) -> str:
    """도메인 → 플랫폼"""
    if domain in PLATFORM_MAP:
        return PLATFORM_MAP[domain]
    if domain.endswith(".tistory.com") or domain == "tistory.com":
        return "티스토리"
    return "기타"

# ============================================================
# 네이버 모바일 SERP HTML 크롤링 (광고/AI브리핑/VIEW 영역)
# ============================================================
def fetch_naver_serp_html(keyword: str) -> str:
    """네이버 모바일 통합검색 페이지 HTML"""
    url = f"https://m.search.naver.com/search.naver?query={urllib.parse.quote(keyword)}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
            "Accept-Language": "ko-KR,ko;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return ""

def parse_serp_areas(html: str) -> dict:
    """HTML → 영역별 정보 추출"""
    result = {
        "has_ai_briefing": False,
        "has_ad": False,
        "ad_count": 0,
        "ad_domains": [],
        "has_view": False,
        "has_influencer": False,
        "has_news": False,
        "has_video": False,
        "first_page_domains": [],
    }
    
    if not html or len(html) < 1000:
        return result
    
    # AI 브리핑 영역 (있으면 그 키워드는 정보 의도 강함)
    if "AI 브리핑" in html or "ai_briefing" in html.lower() or "sc_briefing" in html.lower():
        result["has_ai_briefing"] = True
    
    # 광고 영역 (파워링크 / 비즈사이트 / 쇼핑 광고 등)
    # "광고" 라벨 또는 "ad_section" 클래스
    ad_patterns = [r'class="[^"]*ad[^"]*"', r'data-area-code="\w*ad\w*"', r'>광고<', r'>스폰서<']
    ad_matches = 0
    for pattern in ad_patterns:
        ad_matches += len(re.findall(pattern, html, re.IGNORECASE))
    if ad_matches > 0:
        result["has_ad"] = True
        # 광고 도메인 추출 (광고 영역 안의 외부 링크)
        # 간단한 휴리스틱: ".kr/", ".com/" 등 외부 도메인 추출
        ad_links = re.findall(r'href="(https?://[^"]+)"[^>]*class="[^"]*(?:ad|sponsor|spnsr)[^"]*"', html, re.IGNORECASE)
        for link in ad_links[:10]:
            d = extract_domain(link)
            if d and d not in KNOWN_DOMAINS and d not in result["ad_domains"]:
                result["ad_domains"].append(d)
        result["ad_count"] = max(len(result["ad_domains"]), min(ad_matches // 3, 5))
    
    # VIEW (블로그+카페 통합) 
    if "VIEW" in html or "lst_view" in html.lower():
        result["has_view"] = True
    
    # 인플루언서
    if "인플루언서" in html or "influencer" in html.lower():
        result["has_influencer"] = True
    
    # 뉴스
    if 'class="sc_news"' in html or "news.naver.com" in html:
        result["has_news"] = True
    
    # 동영상
    if 'class="sc_video"' in html or 'data-area-code="vid' in html:
        result["has_video"] = True
    
    # 1페이지 외부 도메인 (네이버·다음 제외)
    external_links = re.findall(r'href="(https?://[^"]+)"', html)
    seen = set()
    for link in external_links:
        d = extract_domain(link)
        if d and "naver.com" not in d and "daum.net" not in d and "kakao" not in d:
            if d not in seen:
                seen.add(d)
                result["first_page_domains"].append(d)
        if len(result["first_page_domains"]) >= 20:
            break
    
    return result

# ============================================================
# 키워드 1개 분석
# ============================================================
def analyze_keyword(keyword: str, do_serp: bool = True) -> dict:
    """키워드 1개 → API 분석 + SERP HTML 분석"""
    all_domains = []
    
    # 1) 네이버 API (블로그/카페/웹문서)
    for category in ["blog", "cafearticle", "webkr"]:
        result = naver_search_api(category, keyword, display=20)
        for item in result.get("items", []):
            link = item.get("link", "")
            domain = extract_domain(link)
            if domain:
                all_domains.append(domain)
        time.sleep(0.1)
    
    platform_count = Counter(classify_platform(d) for d in all_domains)
    unique_domains = set(all_domains)
    new_domains = [d for d in unique_domains if d not in KNOWN_DOMAINS]
    
    # 2) SERP HTML 크롤링 (광고/AI브리핑/VIEW 영역)
    serp_info = {}
    if do_serp:
        html = fetch_naver_serp_html(keyword)
        serp_info = parse_serp_areas(html)
        time.sleep(random.uniform(0.5, 1.2))  # 차단 방지
    
    return {
        "keyword": keyword,
        "total": len(all_domains),
        "platforms": dict(platform_count),
        "all_domains": all_domains,
        "new_domains": new_domains,
        "serp": serp_info,
    }

# ============================================================
# Google Sheets
# ============================================================
creds = service_account.Credentials.from_service_account_info(
    json.loads(SA_JSON),
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)
sheets = build("sheets", "v4", credentials=creds).spreadsheets()

def ensure_sheet(title: str, headers: list):
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
    print(f"\n{'='*70}\n  SERP Monitor v2.0 — {now}\n{'='*70}\n")
    
    # 시트 준비
    ensure_sheet("결과", ["날짜", "키워드", "총노출", "네이버블로그%", "네이버카페%", "티스토리%", "브런치%", "네프콘%", "지식iN%", "유튜브%", "기타%", "1위플랫폼", "광고개수", "AI브리핑"])
    ensure_sheet("플랫폼점유율", ["날짜", "네이버블로그", "네이버카페", "티스토리", "브런치", "네프콘", "지식iN", "유튜브", "기타", "분석키워드수"])
    ensure_sheet("신규도메인", ["발견일자", "도메인", "발견키워드", "노출횟수"])
    ensure_sheet("연관검색어", ["수집일자", "시드키워드", "연관검색어", "신규여부"])
    ensure_sheet("영역별점유율", ["날짜", "AI브리핑노출%", "광고노출%", "VIEW노출%", "인플루언서%", "뉴스%", "동영상%"])
    ensure_sheet("광고주풀", ["발견일자", "광고도메인", "노출키워드", "키워드수"])
    ensure_sheet("인사이트", ["날짜", "한줄요약", "1위플랫폼", "RPM본진키워드", "AI브리핑키워드", "광고주수", "신규도메인수", "신규연관검색어수", "메모"])
    
    # 어제 데이터
    yesterday_data = read_all("플랫폼점유율")
    yesterday_platforms = {}
    if len(yesterday_data) > 1:
        last_row = yesterday_data[-1]
        headers = yesterday_data[0]
        for i, h in enumerate(headers[1:], 1):
            try:
                yesterday_platforms[h] = float(last_row[i].replace("%", "")) if i < len(last_row) else 0
            except (ValueError, AttributeError, IndexError):
                yesterday_platforms[h] = 0
    
    # 이미 본 신규 도메인
    known_new_data = read_all("신규도메인")
    already_seen_new = set()
    if len(known_new_data) > 1:
        already_seen_new = {row[1] for row in known_new_data[1:] if len(row) > 1}
    
    # 이미 본 연관검색어
    known_related = read_all("연관검색어")
    already_seen_related = set()
    if len(known_related) > 1:
        already_seen_related = {row[2] for row in known_related[1:] if len(row) > 2}
    
    # 이미 본 광고주
    known_ads = read_all("광고주풀")
    already_seen_ads = set()
    if len(known_ads) > 1:
        already_seen_ads = {row[1] for row in known_ads[1:] if len(row) > 1}
    
    # ========== 1. 자동완성 수집 (시드 키워드 → 연관검색어) ==========
    print(f"📌 1단계: 시드 키워드 {len(SEED_KEYWORDS)}개 → 자동완성 수집\n")
    related_map = {}  # {시드: [연관검색어들]}
    all_keywords = set(SEED_KEYWORDS)
    new_related_count = 0
    
    for i, seed in enumerate(SEED_KEYWORDS, 1):
        suggestions = fetch_autocomplete(seed, max_n=AUTOCOMPLETE_PER_KEYWORD)
        related_map[seed] = suggestions
        print(f"  [{i:2}/{len(SEED_KEYWORDS)}] {seed:18s} → {len(suggestions)}개: {', '.join(suggestions[:3])}{'...' if len(suggestions) > 3 else ''}")
        
        for s in suggestions:
            all_keywords.add(s)
            if s not in already_seen_related:
                new_related_count += 1
        
        time.sleep(random.uniform(0.3, 0.8))
    
    # 키워드 풀 제한
    final_keywords = sorted(all_keywords)
    if len(final_keywords) > MAX_TOTAL_KEYWORDS:
        # 시드는 무조건 포함, 나머지에서 랜덤 샘플링
        non_seed = [k for k in final_keywords if k not in SEED_KEYWORDS]
        random.shuffle(non_seed)
        final_keywords = list(SEED_KEYWORDS) + non_seed[:MAX_TOTAL_KEYWORDS - len(SEED_KEYWORDS)]
    
    print(f"\n  총 분석 키워드: {len(final_keywords)}개 (시드 {len(SEED_KEYWORDS)} + 연관 {len(final_keywords)-len(SEED_KEYWORDS)})\n")
    
    # 연관검색어 시트 저장
    related_rows = []
    for seed, sugs in related_map.items():
        for s in sugs:
            related_rows.append([today, seed, s, "🆕 신규" if s not in already_seen_related else "기존"])
    append_rows("연관검색어", related_rows)
    
    # ========== 2. 각 키워드 분석 ==========
    print(f"📌 2단계: 키워드 {len(final_keywords)}개 SERP 분석\n")
    
    all_results = []
    all_new_domains = defaultdict(lambda: {"count": 0, "keywords": set()})
    all_ad_domains = defaultdict(lambda: {"keywords": set()})
    total_platform_count = Counter()
    
    serp_stats = {"ai_briefing": 0, "ad": 0, "view": 0, "influencer": 0, "news": 0, "video": 0}
    serp_success = 0
    rpm_treasure = []  # 광고 3+ = RPM 본진
    ai_briefing_keywords = []
    
    for i, kw in enumerate(final_keywords, 1):
        print(f"  [{i:3}/{len(final_keywords)}] {kw:25s}", end=" → ", flush=True)
        try:
            result = analyze_keyword(kw, do_serp=True)
            all_results.append(result)
            
            for plat, cnt in result["platforms"].items():
                total_platform_count[plat] += cnt
            
            for d in result["new_domains"]:
                all_new_domains[d]["count"] += result["all_domains"].count(d)
                all_new_domains[d]["keywords"].add(kw)
            
            # SERP 영역 통계
            serp = result.get("serp", {})
            if serp:
                serp_success += 1
                if serp.get("has_ai_briefing"):
                    serp_stats["ai_briefing"] += 1
                    ai_briefing_keywords.append(kw)
                if serp.get("has_ad"):
                    serp_stats["ad"] += 1
                    if serp.get("ad_count", 0) >= 3:
                        rpm_treasure.append(kw)
                    for d in serp.get("ad_domains", []):
                        all_ad_domains[d]["keywords"].add(kw)
                if serp.get("has_view"):
                    serp_stats["view"] += 1
                if serp.get("has_influencer"):
                    serp_stats["influencer"] += 1
                if serp.get("has_news"):
                    serp_stats["news"] += 1
                if serp.get("has_video"):
                    serp_stats["video"] += 1
            
            # 1위 플랫폼
            p = result["platforms"]
            if p:
                top_plat = max(p, key=p.get)
                ad_n = serp.get("ad_count", 0)
                ai = "🤖" if serp.get("has_ai_briefing") else ""
                ad_em = f"🔥광고{ad_n}" if ad_n >= 3 else (f"⭐광고{ad_n}" if ad_n >= 1 else "")
                print(f"{top_plat[:8]:8s} {ai} {ad_em}")
            else:
                print("결과없음")
        except Exception as e:
            print(f"❌ {str(e)[:40]}")
            continue
    
    print(f"\n  SERP HTML 크롤링: {serp_success}/{len(final_keywords)} 성공")
    
    # ========== 3. 결과 시트 ==========
    result_rows = []
    for r in all_results:
        total = r["total"] if r["total"] else 1
        p = r["platforms"]
        top_plat = max(p, key=p.get) if p else "-"
        serp = r.get("serp", {})
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
            serp.get("ad_count", 0),
            "✅" if serp.get("has_ai_briefing") else "",
        ]
        result_rows.append(row)
    append_rows("결과", result_rows)
    
    # ========== 4. 플랫폼점유율 ==========
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
    append_rows("플랫폼점유율", [[today] + [f"{v:.1f}%" for v in today_platforms.values()] + [len(final_keywords)]])
    
    # ========== 5. 신규도메인 ==========
    new_today = []
    for domain, info in all_new_domains.items():
        if domain in already_seen_new:
            continue
        new_today.append([
            today, domain,
            ", ".join(sorted(info["keywords"])[:5]),
            info["count"],
        ])
    new_today.sort(key=lambda x: -x[3])
    append_rows("신규도메인", new_today[:200])  # 너무 많으면 상위 200개만
    
    # ========== 6. 영역별점유율 ==========
    if serp_success > 0:
        n = serp_success
        append_rows("영역별점유율", [[
            today,
            f"{serp_stats['ai_briefing']/n*100:.1f}%",
            f"{serp_stats['ad']/n*100:.1f}%",
            f"{serp_stats['view']/n*100:.1f}%",
            f"{serp_stats['influencer']/n*100:.1f}%",
            f"{serp_stats['news']/n*100:.1f}%",
            f"{serp_stats['video']/n*100:.1f}%",
        ]])
    
    # ========== 7. 광고주풀 ==========
    ad_rows = []
    for domain, info in all_ad_domains.items():
        if domain in already_seen_ads:
            continue
        ad_rows.append([
            today, domain,
            ", ".join(sorted(info["keywords"])[:5]),
            len(info["keywords"]),
        ])
    ad_rows.sort(key=lambda x: -x[3])
    append_rows("광고주풀", ad_rows)
    
    # ========== 8. 인사이트 ==========
    top_platform = max(today_platforms, key=today_platforms.get)
    top_pct = today_platforms[top_platform]
    
    # 한 줄 요약
    if rpm_treasure:
        summary = f"🔥 RPM 본진 발견! 광고 3+ 키워드 {len(rpm_treasure)}개. '결과' 시트에서 광고개수 정렬 확인."
    elif new_related_count > 20:
        summary = f"🆕 신규 연관검색어 {new_related_count}개 발견. 트렌드 신호 가능성 → '연관검색어' 시트 확인."
    elif serp_stats["ai_briefing"] > 0:
        summary = f"🤖 AI 브리핑 노출 키워드 {serp_stats['ai_briefing']}개. 네프콘 인용 기회."
    else:
        summary = f"📊 {top_platform} {top_pct:.0f}% 1위. 큰 변화 없음."
    
    # 메모
    notes = []
    if rpm_treasure:
        notes.append(f"🔥 RPM본진({len(rpm_treasure)}개): {', '.join(rpm_treasure[:5])}")
    if ai_briefing_keywords:
        notes.append(f"🤖 AI브리핑: {', '.join(ai_briefing_keywords[:5])}")
    if len(new_today) > 30:
        notes.append(f"🆕 신규도메인 {len(new_today)}개 → 광맥 후보")
    if len(all_ad_domains) > 0:
        notes.append(f"💰 광고주 {len(all_ad_domains)}개 발견")
    if today_platforms.get("기타", 0) > 30:
        notes.append("📍 '기타' 비중 30%+ → 신규 플랫폼 등장")
    if not notes:
        notes.append("특이사항 없음")
    
    append_rows("인사이트", [[
        today, summary, f"{top_platform} ({top_pct:.0f}%)",
        len(rpm_treasure), len(ai_briefing_keywords),
        len(all_ad_domains), len(new_today), new_related_count,
        " / ".join(notes),
    ]])
    
    # ========== 콘솔 요약 ==========
    print(f"\n{'='*70}")
    print(f"  완료. 결과 시트: https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit")
    print(f"{'='*70}")
    print(f"  분석 키워드: {len(final_keywords)}개 (시드 {len(SEED_KEYWORDS)} + 연관 {len(final_keywords)-len(SEED_KEYWORDS)})")
    print(f"  1위 플랫폼: {top_platform} ({top_pct:.1f}%)")
    print(f"  🔥 RPM 본진 (광고 3+): {len(rpm_treasure)}개")
    if rpm_treasure:
        print(f"     → {', '.join(rpm_treasure[:5])}")
    print(f"  🤖 AI 브리핑 노출: {len(ai_briefing_keywords)}개")
    print(f"  🆕 신규 도메인: {len(new_today)}개")
    print(f"  💰 광고주: {len(all_ad_domains)}개")
    print(f"  🆕 신규 연관검색어: {new_related_count}개")
    print(f"\n  📌 한 줄 요약: {summary}\n")


if __name__ == "__main__":
    main()
