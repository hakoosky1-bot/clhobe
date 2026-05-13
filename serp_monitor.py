"""
SERP Monitor v3.0 — Selenium 정밀 분석
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

핵심 변경:
1. 시드 42개 → Selenium으로 m.search.naver.com 실제 렌더링 후 분석
2. 연관검색어 162개는 기존 API 분석 유지
3. 첫 실행에서 시드 1개("임플란트")의 렌더링된 HTML을 "디버그" 시트에 저장
   → 그 HTML 보고 다음 버전에서 영역별 셀렉터 정확히 잡음
4. 영역 추출 — 일단 알려진 패턴 시도, 안 잡혀도 도메인은 무조건 추출
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

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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

AUTOCOMPLETE_PER_KEYWORD = 5
MAX_TOTAL_KEYWORDS = 200
DEBUG_DUMP_KEYWORD = "임플란트"

PLATFORM_MAP = {
    "blog.naver.com": "네이버블로그", "m.blog.naver.com": "네이버블로그",
    "cafe.naver.com": "네이버카페", "m.cafe.naver.com": "네이버카페",
    "post.naver.com": "네이버포스트", "m.post.naver.com": "네이버포스트",
    "kin.naver.com": "지식iN", "m.kin.naver.com": "지식iN",
    "contents.premium.naver.com": "네프콘",
    "tv.naver.com": "네이버TV",
    "brunch.co.kr": "브런치",
    "tistory.com": "티스토리",
    "youtube.com": "유튜브", "m.youtube.com": "유튜브", "youtu.be": "유튜브",
    "instagram.com": "인스타그램", "facebook.com": "페이스북",
    "x.com": "X(트위터)", "twitter.com": "X(트위터)",
    "namu.wiki": "나무위키", "ko.wikipedia.org": "위키백과",
}

KNOWN_DOMAINS = set(PLATFORM_MAP.keys()) | {
    "naver.com", "m.naver.com", "search.naver.com", "m.search.naver.com",
    "shopping.naver.com", "m.shopping.naver.com", "place.naver.com", "m.place.naver.com",
    "map.naver.com", "m.map.naver.com", "news.naver.com", "m.news.naver.com",
    "terms.naver.com", "m.terms.naver.com", "dict.naver.com", "m.dict.naver.com",
    "image.naver.com", "m.image.naver.com",
    "daum.net", "m.daum.net", "search.daum.net",
    "google.com", "google.co.kr", "wikipedia.org",
    "pstatic.net", "naver.net",
}

NAVER_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_SECRET = os.environ["NAVER_CLIENT_SECRET"]
SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

def fetch_autocomplete(keyword, max_n=5):
    url = f"https://ac.search.naver.com/nx/ac?q={urllib.parse.quote(keyword)}&con=1&frm=nv&ans=2&r_format=json&r_enc=UTF-8&r_unicode=0&t_koreng=1&run=2&rev=4&q_enc=UTF-8&st=100"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
            "Referer": "https://m.naver.com/",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("items", [[]])[0]
        suggestions = []
        for item in items[:max_n]:
            if isinstance(item, list) and len(item) > 0:
                kw = item[0]
                if kw and kw != keyword and kw not in suggestions:
                    suggestions.append(kw)
        return suggestions
    except Exception:
        return []

def extract_domain(url):
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    if not m:
        return ""
    domain = m.group(1).lower()
    if domain.endswith(".tistory.com"):
        return "tistory.com"
    return domain

def classify_platform(domain):
    if domain in PLATFORM_MAP:
        return PLATFORM_MAP[domain]
    if domain.endswith(".tistory.com") or domain == "tistory.com":
        return "티스토리"
    return "기타"

def make_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=420,900")
    opts.add_argument("--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")
    opts.add_argument("--lang=ko-KR")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    })
    return driverdef selenium_serp(driver, keyword):
    url = f"https://m.search.naver.com/search.naver?query={urllib.parse.quote(keyword)}"
    result = {
        "keyword": keyword, "domains": [], "platform_count": Counter(),
        "has_ai_briefing": False, "has_ad": False, "ad_count": 0, "ad_domains": [],
        "has_네프콘": False, "has_브런치": False, "has_지식iN": False,
        "has_VIEW": False, "has_인플루언서": False, "has_뉴스": False,
        "has_동영상": False, "has_지도": False,
        "html_size": 0, "html": "", "_error": "",
    }
    try:
        driver.get(url)
        time.sleep(2.5)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight/3);")
        time.sleep(0.7)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight*2/3);")
        time.sleep(0.7)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.7)
        html = driver.page_source
        result["html_size"] = len(html)
        if "안정적인 검색" in html or "잠시 후 다시" in html or "보안 절차" in html:
            result["_error"] = "BOT_BLOCKED"
            return result
        if keyword == DEBUG_DUMP_KEYWORD:
            result["html"] = html[:45000]
        if "AI 브리핑" in html or "ai_briefing" in html.lower() or "aigen" in html.lower():
            result["has_ai_briefing"] = True
        if "contents.premium.naver.com" in html or "프리미엄콘텐츠" in html or "프리미엄 콘텐츠" in html:
            result["has_네프콘"] = True
        if "brunch.co.kr" in html:
            result["has_브런치"] = True
        if "kin.naver.com" in html and "지식iN" in html:
            result["has_지식iN"] = True
        if "VIEW" in html and ("blog.naver.com" in html or "cafe.naver.com" in html):
            result["has_VIEW"] = True
        if "인플루언서" in html and ("influencer" in html.lower() or "in.naver.com" in html):
            result["has_인플루언서"] = True
        if "news.naver.com" in html or 'class="news_' in html or "news_tit" in html:
            result["has_뉴스"] = True
        if "tv.naver.com" in html or "youtube.com/watch" in html:
            result["has_동영상"] = True
        if "map.naver.com" in html or "place.naver.com" in html:
            result["has_지도"] = True
        ad_count = 0
        ad_count += len(re.findall(r'>광고<', html))
        ad_count += len(re.findall(r'class="[^"]*spnsr[^"]*"', html, re.IGNORECASE))
        ad_count += len(re.findall(r'class="[^"]*sponsor[^"]*"', html, re.IGNORECASE))
        if "파워링크" in html or "비즈사이트" in html:
            ad_count = max(ad_count, 1)
        result["ad_count"] = min(ad_count // 2, 5)
        result["has_ad"] = result["ad_count"] > 0
        urls = re.findall(r'href="(https?://[^"]+)"', html)
        seen = set()
        for u in urls:
            d = extract_domain(u)
            if not d:
                continue
            if "pstatic.net" in d or "naver.net" in d:
                continue
            if d in seen:
                continue
            seen.add(d)
            result["domains"].append(d)
            result["platform_count"][classify_platform(d)] += 1
    except Exception as e:
        result["_error"] = str(e)[:200]
    return result

creds = service_account.Credentials.from_service_account_info(
    json.loads(SA_JSON),
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)
sheets = build("sheets", "v4", credentials=creds).spreadsheets()

def ensure_sheet(title, headers):
    meta = sheets.get(spreadsheetId=SHEET_ID).execute()
    existing = [s["properties"]["title"] for s in meta["sheets"]]
    if title not in existing:
        sheets.batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        ).execute()
        sheets.values().update(
            spreadsheetId=SHEET_ID, range=f"{title}!A1",
            valueInputOption="RAW", body={"values": [headers]},
        ).execute()

def append_rows(title, rows):
    if not rows:
        return
    sheets.values().append(
        spreadsheetId=SHEET_ID, range=f"{title}!A:Z",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()

def read_all(title):
    try:
        res = sheets.values().get(spreadsheetId=SHEET_ID, range=f"{title}!A:Z").execute()
        return res.get("values", [])
    except Exception:
        return []

def main():
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*70}\n  SERP Monitor v3.0 (Selenium) — {now}\n{'='*70}\n")
    
    ensure_sheet("시드SERP결과", ["날짜","키워드","총도메인","AI브리핑","네프콘","브런치","지식iN","VIEW","인플루언서","뉴스","동영상","지도","광고개수","1위플랫폼"])
    ensure_sheet("영역별노출률", ["날짜","AI브리핑%","네프콘%","브런치%","지식iN%","VIEW%","인플루언서%","뉴스%","동영상%","지도%","광고있음%","키워드수"])
    ensure_sheet("시드신규도메인", ["발견일자","도메인","발견키워드","노출수"])
    ensure_sheet("연관검색어", ["수집일자","시드키워드","연관검색어","신규여부"])
    ensure_sheet("인사이트v3", ["날짜","한줄요약","RPM본진키워드","AI브리핑키워드","네프콘노출","브런치노출","신규도메인수","신규연관검색어수","메모"])
    ensure_sheet("디버그_HTML", ["날짜","키워드","HTML_크기","HTML"])
    
    known_new = read_all("시드신규도메인")
    seen_domains = set()
    if len(known_new) > 1:
        seen_domains = {r[1] for r in known_new[1:] if len(r) > 1}
    
    known_related = read_all("연관검색어")
    seen_related = set()
    if len(known_related) > 1:
        seen_related = {r[2] for r in known_related[1:] if len(r) > 2}
    
    print(f"📌 1단계: 시드 {len(SEED_KEYWORDS)}개 → 자동완성 수집\n")
    related_map = {}
    new_related = 0
    related_rows = []
    for i, seed in enumerate(SEED_KEYWORDS, 1):
        sugs = fetch_autocomplete(seed, AUTOCOMPLETE_PER_KEYWORD)
        related_map[seed] = sugs
        print(f"  [{i:2}/{len(SEED_KEYWORDS)}] {seed:18s} → {len(sugs)}개")
        for s in sugs:
            is_new = s not in seen_related
            if is_new:
                new_related += 1
            related_rows.append([today, seed, s, "🆕 신규" if is_new else "기존"])
        time.sleep(random.uniform(0.3, 0.7))
    append_rows("연관검색어", related_rows)
    print(f"\n  연관검색어 총 {len(related_rows)}개, 신규 {new_related}개\n")
    
    print(f"📌 2단계: Selenium SERP 분석\n")
    driver = make_driver()
    print(f"  ✅ Chrome 드라이버 준비\n")
    
    serp_results = []
    new_domains_today = defaultdict(lambda: {"count": 0, "keywords": set()})
    area_stats = Counter()
    rpm_treasure = []
    ai_briefing_kws = []
    netcon_kws = []
    brunch_kws = []
    error_count = 0
    bot_block_count = 0
    debug_html_saved = False
    
    for i, kw in enumerate(SEED_KEYWORDS, 1):
        print(f"  [{i:2}/{len(SEED_KEYWORDS)}] {kw:18s}", end=" → ", flush=True)
        result = selenium_serp(driver, kw)
        if result["_error"]:
            if result["_error"] == "BOT_BLOCKED":
                bot_block_count += 1
                print(f"🚫 봇차단 ({bot_block_count})")
                if bot_block_count >= 3:
                    print(f"\n  [!] 봇 차단 3회 — 60초 휴식")
                    time.sleep(60)
                    bot_block_count = 0
            else:
                error_count += 1
                print(f"❌ {result['_error'][:40]}")
            continue
        serp_results.append(result)
        bot_block_count = 0
        if kw == DEBUG_DUMP_KEYWORD and result["html"] and not debug_html_saved:
            try:
                append_rows("디버그_HTML", [[today, kw, result["html_size"], result["html"]]])
                debug_html_saved = True
                print(f"📝 ", end="")
            except Exception:
                pass
        if result["has_ai_briefing"]:
            area_stats["AI브리핑"] += 1
            ai_briefing_kws.append(kw)
        if result["has_네프콘"]:
            area_stats["네프콘"] += 1
            netcon_kws.append(kw)
        if result["has_브런치"]:
            area_stats["브런치"] += 1
            brunch_kws.append(kw)
        if result["has_지식iN"]:
            area_stats["지식iN"] += 1
        if result["has_VIEW"]:
            area_stats["VIEW"] += 1
        if result["has_인플루언서"]:
            area_stats["인플루언서"] += 1
        if result["has_뉴스"]:
            area_stats["뉴스"] += 1
        if result["has_동영상"]:
            area_stats["동영상"] += 1
        if result["has_지도"]:
            area_stats["지도"] += 1
        if result["has_ad"]:
            area_stats["광고"] += 1
            if result["ad_count"] >= 3:
                rpm_treasure.append(kw)
        for d in set(result["domains"]):
            if d in KNOWN_DOMAINS:
                continue
            new_domains_today[d]["count"] += result["domains"].count(d)
            new_domains_today[d]["keywords"].add(kw)
        top = result["platform_count"].most_common(1)
        top_plat = top[0][0] if top else "-"
        flags = []
        if result["has_ai_briefing"]: flags.append("🤖AI")
        if result["has_네프콘"]: flags.append("⭐네프콘")
        if result["has_브런치"]: flags.append("📝브런치")
        if result["ad_count"] >= 3: flags.append(f"🔥광고{result['ad_count']}")
        elif result["ad_count"] >= 1: flags.append(f"광고{result['ad_count']}")
        print(f"{top_plat[:7]:7s} {' '.join(flags)}")
        time.sleep(random.uniform(2.0, 4.0))
        if i % 10 == 0 and i < len(SEED_KEYWORDS):
            print(f"\n  [i] {i}개 처리 — 8초 휴식")
            time.sleep(8)
            print()
    
    driver.quit()
    print(f"\n  분석 완료: {len(serp_results)}/{len(SEED_KEYWORDS)}\n")
    
    rows = []
    for r in serp_results:
        top = r["platform_count"].most_common(1)
        rows.append([
            today, r["keyword"], len(r["domains"]),
            "✅" if r["has_ai_briefing"] else "",
            "✅" if r["has_네프콘"] else "",
            "✅" if r["has_브런치"] else "",
            "✅" if r["has_지식iN"] else "",
            "✅" if r["has_VIEW"] else "",
            "✅" if r["has_인플루언서"] else "",
            "✅" if r["has_뉴스"] else "",
            "✅" if r["has_동영상"] else "",
            "✅" if r["has_지도"] else "",
            r["ad_count"],
            top[0][0] if top else "-",
        ])
    append_rows("시드SERP결과", rows)
    
    n = len(serp_results) or 1
    append_rows("영역별노출률", [[
        today,
        f"{area_stats['AI브리핑']/n*100:.0f}%",
        f"{area_stats['네프콘']/n*100:.0f}%",
        f"{area_stats['브런치']/n*100:.0f}%",
        f"{area_stats['지식iN']/n*100:.0f}%",
        f"{area_stats['VIEW']/n*100:.0f}%",
        f"{area_stats['인플루언서']/n*100:.0f}%",
        f"{area_stats['뉴스']/n*100:.0f}%",
        f"{area_stats['동영상']/n*100:.0f}%",
        f"{area_stats['지도']/n*100:.0f}%",
        f"{area_stats['광고']/n*100:.0f}%",
        n,
    ]])
    
    new_today = []
    for d, info in new_domains_today.items():
        if d in seen_domains:
            continue
        new_today.append([today, d, ", ".join(sorted(info["keywords"])[:5]), info["count"]])
    new_today.sort(key=lambda x: -x[3])
    append_rows("시드신규도메인", new_today[:200])
    
    if rpm_treasure:
        summary = f"🔥 RPM 본진! 광고 3+ 키워드 {len(rpm_treasure)}개: {', '.join(rpm_treasure[:3])}"
    elif netcon_kws:
        summary = f"⭐ 네프콘 노출 {len(netcon_kws)}개 — 발행 채널 후보"
    elif brunch_kws:
        summary = f"📝 브런치 노출 {len(brunch_kws)}개"
    elif ai_briefing_kws:
        summary = f"🤖 AI 브리핑 {len(ai_briefing_kws)}개"
    else:
        summary = f"📊 첫 정밀 측정. 디버그_HTML 시트 분석 필요."
    
    notes = []
    if rpm_treasure:
        notes.append(f"🔥 RPM본진: {', '.join(rpm_treasure[:5])}")
    if netcon_kws:
        notes.append(f"⭐ 네프콘: {', '.join(netcon_kws[:5])}")
    if brunch_kws:
        notes.append(f"📝 브런치: {', '.join(brunch_kws[:5])}")
    if ai_briefing_kws:
        notes.append(f"🤖 AI브리핑: {', '.join(ai_briefing_kws[:5])}")
    if len(new_today) > 0:
        notes.append(f"🆕 신규도메인 {len(new_today)}개")
    if error_count > 0:
        notes.append(f"⚠️ 에러 {error_count}개")
    if not notes:
        notes.append("특이사항 없음")
    
    append_rows("인사이트v3", [[
        today, summary,
        len(rpm_treasure), len(ai_briefing_kws),
        len(netcon_kws), len(brunch_kws),
        len(new_today), new_related,
        " / ".join(notes),
    ]])
    
    print(f"{'='*70}")
    print(f"  완료")
    print(f"{'='*70}")
    print(f"  분석: {len(serp_results)}/{len(SEED_KEYWORDS)}")
    print(f"  🔥 RPM본진: {len(rpm_treasure)}개")
    print(f"  ⭐ 네프콘: {len(netcon_kws)}개")
    print(f"  📝 브런치: {len(brunch_kws)}개")
    print(f"  🤖 AI브리핑: {len(ai_briefing_kws)}개")
    print(f"  🆕 신규도메인: {len(new_today)}개")
    print(f"  디버그HTML: {'✅' if debug_html_saved else '❌'}")


if __name__ == "__main__":
    main()
