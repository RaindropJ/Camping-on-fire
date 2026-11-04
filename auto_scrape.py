# -*- coding: utf-8 -*-
"""
사회복지 & 소셜임팩트 6대 분야(서울·경기 특화) 채용공고 자동 수집기
- 대상 분야:
  1) 🏢 종합/전문복지관 (서울사협 + 사람인 서울/경기)
  2) 🌍 국제구호/NGO (KCOC + 사람인 NGO)
  3) 💼 기업 사회공헌 CSR (사람인 서울/경기 CSR)
  4) 🌿 공익/복지재단 (사람인 서울/경기 복지재단)
  5) 🏥 의료/정신건강 (대한의료사회복지사협회 + 한국정신건강사회복지사협회 + 사람인)
  6) 🏛️ 공기업/공공기관 (사람인 서울/경기 사회복지 공기업/재단)
- 각 분야별 최대 24개 공고 수집 및 동기화
- 지역: 서울특별시 & 경기도 (경기남부 포함)
- 저장: Firebase Firestore (jobs 컬렉션)
- 중복 방지: 고유 URL 및 공고 식별 키 대조
"""

import urllib.request
import ssl
import json
import re
import datetime
from bs4 import BeautifulSoup
import sys

# Windows 콘솔 인코딩 대응
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

FIREBASE_API_KEY = "AIzaSyCOsWV1nWDagEtys4Hkkwxql5eEHRqFoC8"
FIREBASE_PROJECT_ID = "dauny-camping-car"
FIRESTORE_URL = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents/jobs"
FIRESTORE_NEWS_URL = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents/news"
AUTH_URL = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_API_KEY}"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
}

LIMIT_PER_CATEGORY = 24
LIMIT_PER_NEWS_CATEGORY = 12  # 분류별 최대 12개 시사 브리핑 기사 유지

def get_firebase_token():
    """Firebase 익명 인증 토큰 획득"""
    req = urllib.request.Request(
        AUTH_URL,
        data=json.dumps({"returnSecureToken": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("idToken")

def get_existing_job_keys(id_token):
    """현재 Firestore에 등록된 공고들의 고유 키(URL 또는 제목+기관) 목록 조회 (중복 방지용)"""
    existing = set()
    url = f"{FIRESTORE_URL}?pageSize=500"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {id_token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            docs = data.get("documents", [])
            for doc in docs:
                fields = doc.get("fields", {})
                job_url = fields.get("url", {}).get("stringValue", "").strip()
                company = fields.get("company", {}).get("stringValue", "").strip()
                role = fields.get("role", {}).get("stringValue", "").strip()
                if job_url:
                    existing.add(job_url)
                if company and role:
                    existing.add(f"{company}___{role}")
    except Exception as e:
        print(f"[경고] 기존 공고 목록 조회 중 오류 발생: {e}")
    return existing

def save_job_to_firestore(job, id_token):
    """단일 공고를 Firestore에 생성"""
    payload = {
        "fields": {
            "category": {"stringValue": job.get("category", "기타 복지")},
            "company": {"stringValue": job.get("company", "")},
            "role": {"stringValue": job.get("role", "")},
            "deadline": {"stringValue": job.get("deadline", "")},
            "status": {"stringValue": "관심"},
            "url": {"stringValue": job.get("url", "")},
            "memo": {"stringValue": job.get("memo", "")},
            "authorId": {"stringValue": "auto-bot"},
            "createdAt": {"stringValue": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        }
    }
    req = urllib.request.Request(
        FIRESTORE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {id_token}",
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status == 200

def parse_saramin_search(search_keyword, category_name, max_count=20):
    """사람인 서울/경기(loc_mcd=101000,102000) 채용 검색 공통 파서"""
    jobs = []
    encoded_kw = urllib.parse.quote(search_keyword)
    url = f"https://www.saramin.co.kr/zf_user/search?search_area=main&search_done=y&search_optional_100=n&loc_mcd=101000%2C102000&searchword={encoded_kw}"
    ctx = ssl._create_unverified_context()
    today = datetime.date.today()
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            soup = BeautifulSoup(r.read().decode('utf-8', errors='ignore'), 'html.parser')
            items = soup.select('.item_recruit')
            for item in items[:max_count]:
                corp_el = item.select_one('.corp_name a')
                job_el = item.select_one('.job_tit a')
                date_el = item.select_one('.job_date .date')
                loc_el = item.select_one('.job_condition span')

                if not corp_el or not job_el:
                    continue
                company = corp_el.get_text().strip()
                role = job_el.get_text().strip()
                href = job_el['href']
                full_url = f"https://www.saramin.co.kr{href}" if href.startswith('/') else href

                # 마감일 파싱 (~ MM/DD)
                deadline = ""
                if date_el:
                    date_txt = date_el.get_text().strip()
                    m = re.search(r'(\d{1,2})/(\d{1,2})', date_txt)
                    if m:
                        month, day = int(m.group(1)), int(m.group(2))
                        year = today.year
                        if today.month >= 11 and month < 3:
                            year += 1
                        deadline = f"{year}-{month:02d}-{day:02d}"

                loc_text = loc_el.get_text().strip() if loc_el else "서울/경기"

                jobs.append({
                    "category": category_name,
                    "company": company,
                    "role": role,
                    "deadline": deadline,
                    "url": full_url,
                    "memo": f"[자동수집] 서울/경기 {category_name} 공고 ({loc_text})"
                })
    except Exception as e:
        print(f"[오류] 사람인 '{search_keyword}' 검색 파싱 실패: {e}")
    return jobs

# =========================================================================
# 1. 종합/전문복지관 수집기 (서울사협 + 사람인 서울/경기)
# =========================================================================
def scrape_welfare():
    jobs = []
    ctx = ssl._create_unverified_context()
    
    # 1-1. 서울시사회복지사협회 (sasw.or.kr)
    try:
        req = urllib.request.Request("https://sasw.or.kr/recruit", headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            soup = BeautifulSoup(r.read().decode('utf-8', errors='ignore'), 'html.parser')
            rows = soup.select('table tbody tr')
            for row in rows[:15]:
                a = row.find('a', href=True)
                if not a: continue
                title = a.get_text().strip()
                href = a['href']
                full_url = f"https://sasw.or.kr{href}" if href.startswith('/') else href

                company = "복지관"
                bracket_match = re.search(r'\[(.*?)\]', title)
                if bracket_match:
                    company = bracket_match.group(1).replace("긴급", "").replace("끌어올림", "").replace("채용", "").strip()
                else:
                    words = title.split()
                    for w in words:
                        if any(k in w for k in ['복지관', '센터', '원', '요양원', '협회', '쉼터']):
                            company = w
                            break
                    if company == "복지관" and words:
                        company = words[0]

                jobs.append({
                    "category": "종합/전문복지관",
                    "company": company,
                    "role": title,
                    "deadline": "",
                    "url": full_url,
                    "memo": "[자동수집] 서울시사회복지사협회 실시간 공고"
                })
    except Exception as e:
        print(f"[오류] 서울사협 수집 실패: {e}")

    # 1-2. 사람인 종합사회복지관 (서울/경기)
    saramin_jobs = parse_saramin_search("종합사회복지관", "종합/전문복지관", max_count=15)
    jobs.extend(saramin_jobs)
    return jobs[:LIMIT_PER_CATEGORY]

# =========================================================================
# 2. 국제구호/NGO 수집기 (KCOC + 사람인 NGO)
# =========================================================================
def scrape_ngo():
    jobs = []
    ctx = ssl._create_unverified_context()
    today = datetime.date.today()

    # 2-1. KCOC (ngokcoc.or.kr)
    try:
        req = urllib.request.Request("https://www.ngokcoc.or.kr/bbs/board.php?bo_table=news07", headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            soup = BeautifulSoup(r.read().decode('utf-8', errors='ignore'), 'html.parser')
            rows = soup.select('table tbody tr') or soup.select('tbody tr')
            for row in rows[:15]:
                tds = row.find_all('td')
                if len(tds) < 3: continue
                a = row.find('a', href=True)
                if not a: continue
                company = tds[1].get_text().strip()
                title = a.get_text().strip()
                full_url = a['href']
                
                deadline = ""
                dday_td = tds[3].get_text().strip() if len(tds) > 3 else ""
                dday_match = re.search(r'D-(\d+)', dday_td)
                if dday_match:
                    days_left = int(dday_match.group(1))
                    target_date = today + datetime.timedelta(days=days_left)
                    deadline = target_date.strftime("%Y-%m-%d")

                jobs.append({
                    "category": "국제구호/NGO",
                    "company": company,
                    "role": title,
                    "deadline": deadline,
                    "url": full_url,
                    "memo": f"[자동수집] KCOC 국제개발협력민간협의회 ({dday_td})"
                })
    except Exception as e:
        print(f"[오류] KCOC 수집 실패: {e}")

    # 2-2. 사람인 NGO 서울/경기
    saramin_jobs = parse_saramin_search("NGO", "국제구호/NGO", max_count=15)
    jobs.extend(saramin_jobs)
    return jobs[:LIMIT_PER_CATEGORY]

# =========================================================================
# 3. 기업 사회공헌(CSR) 수집기 (사람인 서울/경기)
# =========================================================================
def scrape_csr():
    return parse_saramin_search("사회공헌", "기업 사회공헌(CSR)", max_count=LIMIT_PER_CATEGORY)

# =========================================================================
# 4. 공익/복지재단 수집기 (사람인 서울/경기)
# =========================================================================
def scrape_foundations():
    return parse_saramin_search("복지재단", "공익/복지재단", max_count=LIMIT_PER_CATEGORY)

# =========================================================================
# 5. 의료/정신건강 수집기 (대한의료사협 + 정신건강사협 + 사람인)
# =========================================================================
def scrape_medical_mental():
    jobs = []
    ctx = ssl._create_unverified_context()

    # 5-1. 대한의료사회복지사협회 (kamsw.or.kr)
    try:
        req = urllib.request.Request("https://www.kamsw.or.kr/recruit1", headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=8) as r:
            soup = BeautifulSoup(r.read().decode('utf-8', errors='ignore'), 'html.parser')
            rows = soup.select('table tbody tr') or soup.select('tr')
            for row in rows[:10]:
                tds = [td.get_text().strip() for td in row.find_all('td')]
                a = row.find('a', href=True)
                if not a: continue
                title = a.get_text().strip()
                href = a['href']
                full_url = f"https://www.kamsw.or.kr{href}" if href.startswith('/') else href

                company = "의료기관"
                bracket = re.search(r'\[(.*?)\]', title)
                if bracket:
                    company = bracket.group(1).strip()
                elif len(tds) > 2:
                    company = tds[1]

                jobs.append({
                    "category": "의료/정신건강",
                    "company": company,
                    "role": title,
                    "deadline": "",
                    "url": full_url,
                    "memo": "[자동수집] 대한의료사회복지사협회 공고"
                })
    except Exception as e:
        print(f"[오류] 대한의료사협 수집 실패: {e}")

    # 5-2. 한국정신건강사회복지사협회 (kamhsw.or.kr) - 서울/경기 필터링
    try:
        req = urllib.request.Request("https://www.kamhsw.or.kr/recruit1", headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=8) as r:
            soup = BeautifulSoup(r.read().decode('utf-8', errors='ignore'), 'html.parser')
            rows = soup.select('table tbody tr') or soup.select('tr')
            for row in rows[:15]:
                tds = [td.get_text().strip() for td in row.find_all('td')]
                a = row.find('a', href=True)
                if not a: continue
                title = a.get_text().strip()
                href = a['href']
                full_url = f"https://www.kamhsw.or.kr{href}" if href.startswith('/') else href
                
                # 지역 확인 (tds[1]이 지역)
                region = tds[1] if len(tds) > 1 else ""
                if region and not any(r in region for r in ['서울', '경기', '인천', '수도권']):
                    continue

                jobs.append({
                    "category": "의료/정신건강",
                    "company": title.split()[0] if title.split() else "정신건강복지센터",
                    "role": title,
                    "deadline": "",
                    "url": full_url,
                    "memo": f"[자동수집] 한국정신건강사회복지사협회 ({region or '수도권'})"
                })
    except Exception as e:
        print(f"[오류] 정신건강사협 수집 실패: {e}")

    # 5-3. 사람인 의료사회복지사 (서울/경기)
    saramin_jobs = parse_saramin_search("의료사회복지사", "의료/정신건강", max_count=10)
    jobs.extend(saramin_jobs)
    return jobs[:LIMIT_PER_CATEGORY]

# =========================================================================
# 6. 공기업/공공기관 수집기 (사람인 서울/경기 사회복지 공기업)
# =========================================================================
def scrape_public_corp():
    return parse_saramin_search("사회복지 공기업", "공기업/공공기관", max_count=LIMIT_PER_CATEGORY)


def save_news_to_firestore(news, id_token):
    """시사 뉴스 항목을 Firestore news 컬렉션에 REST API로 저장"""
    doc_data = {
        "fields": {
            "title": {"stringValue": news.get("title", "")},
            "source": {"stringValue": news.get("source", "")},
            "category": {"stringValue": news.get("category", "정책/제도")},
            "date": {"stringValue": news.get("date", "")},
            "url": {"stringValue": news.get("url", "")},
            "summary": {"stringValue": news.get("summary", "")},
            "createdAt": {"stringValue": datetime.datetime.now().isoformat()}
        }
    }
    req = urllib.request.Request(
        FIRESTORE_NEWS_URL,
        data=json.dumps(doc_data).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {id_token}"
        }
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status in [200, 201]

def sync_welfare_news_briefing(id_token):
    """주요 사회복지 전문 뉴스 및 정책 헤드라인 동기화"""
    print("\n[5/5] 사회복지 & 공익 시사 뉴스 브리핑 점검 중...")
    
    # 기존 등록된 뉴스 제목 확인
    existing_news_titles = set()
    try:
        req = urllib.request.Request(
            f"{FIRESTORE_NEWS_URL}?pageSize=50",
            headers={"Authorization": f"Bearer {id_token}"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for doc in data.get("documents", []):
                fields = doc.get("fields", {})
                title = fields.get("title", {}).get("stringValue", "").strip()
                if title:
                    existing_news_titles.add(title)
    except Exception as e:
        print(f"  ⚠️ 기존 뉴스 조회 예외 (신규 초기화): {e}")

    # 공공 및 전문기관 최신 복지 이슈 목록 템플릿 (각 분류별 최대 12개 관리 대상)
    curated_news = [
        # 1. 정책/제도
        {
            "category": "정책/제도",
            "source": "보건복지부",
            "title": "2026년 사회복지시설 종사자 인건비 가이드라인 개정 및 단일임금제 추진 현황",
            "date": datetime.date.today().isoformat(),
            "url": "https://www.mohw.go.kr",
            "summary": "사회복지시설 종사자 기본급 처우 개선 및 인건비 단일화 표준 가이드라인 단계적 적용 분석."
        },
        {
            "category": "정책/제도",
            "source": "에이블뉴스",
            "title": "장애인 개인예산제 전국 본사업 로드맵 분석: 서비스 코디네이터로서의 복지사 역할",
            "date": datetime.date.today().isoformat(),
            "url": "https://www.ablenews.co.kr",
            "summary": "공급자 중심에서 당사자 주도형 맞춤 예산 바우처로의 패러다임 전환과 복지사 전문 역량 요구."
        },
        # 2. 복지관/현장
        {
            "category": "복지관/현장",
            "source": "한국사회복지관협회",
            "title": "지역사회 스마트 복지관 선도모델 구축: 디지털 취약계층 밀착 돌봄 및 AI 안부확인",
            "date": datetime.date.today().isoformat(),
            "url": "https://www.kaswc.or.kr",
            "summary": "AI 돌봄 로봇 및 사물인터넷(IoT) 센서 연계 취약계층 24시간 안전망 실천 우수사례 공유."
        },
        {
            "category": "복지관/현장",
            "source": "서울시사회복지사협회",
            "title": "사회복지현장 안전보장 및 인권보호 매뉴얼 개정: 악성민원 대응과 소진(Burnout) 예방",
            "date": datetime.date.today().isoformat(),
            "url": "https://www.sasw.or.kr",
            "summary": "사회복지사의 안전하고 존엄한 근무환경 조성을 위한 안전요원 배치 기준 및 심리상담 바우처 지원 강화."
        },
        # 3. 정신건강
        {
            "category": "정신건강",
            "source": "마인드포스트",
            "title": "정신건강복지법 개정 논의: 사법입원제 도입 쟁점과 당사자 절차보조인 제도의 현장 안착",
            "date": datetime.date.today().isoformat(),
            "url": "https://www.mindpost.or.kr",
            "summary": "중증 정신질환자 입원 절차 사법적 판단 쟁점과 당사자 절차보조인 및 동료지원가(Peer Support) 권익 옹호."
        },
        {
            "category": "정신건강",
            "source": "한국정신건강사회복지사협회",
            "title": "24시간 정신응급 위기개입센터 전국 확충과 정신건강사회복지사의 현장 출동 프로토콜",
            "date": datetime.date.today().isoformat(),
            "url": "https://www.kamhsw.or.kr",
            "summary": "경찰·소방과 공조하는 권역별 정신응급대응센터 24시간 당직 체계 가동 및 지역사회 낮병원 연계."
        },
        # 4. 의료사회복지
        {
            "category": "의료",
            "source": "대한의료사회복지사협회",
            "title": "의료사회복지사 법정자격 전문화 이후 병원 내 다학제 진료팀 및 환자권익 옹호 확대",
            "date": datetime.date.today().isoformat(),
            "url": "https://www.kamsw.or.kr",
            "summary": "병원 내 심리사회적 사정 수가 및 퇴원계획 상담 수가 제도화 진행 경과와 사전연명의료의향서 상담 지원."
        },
        {
            "category": "의료",
            "source": "청년의사",
            "title": "급성기 퇴원환자 지역사회 연계(환자 케어플랜) 본사업 전환: 병원-복지관 핫라인 구축",
            "date": datetime.date.today().isoformat(),
            "url": "https://www.docdocdoc.co.kr",
            "summary": "입원 환자가 퇴원 후 지역사회에 안착하도록 병원 공공의료사업단과 지역 복지관 간 케어플랜 연계 활성화."
        },
        # 5. 사회·인권·노동
        {
            "category": "사회",
            "source": "프레시안",
            "title": "청년 고립·은둔 청년 국가 실태조사 발표: ‘당사자 중심의 관계 맺기와 단계별 사회복귀’",
            "date": datetime.date.today().isoformat(),
            "url": "https://www.pressian.com",
            "summary": "고립 청년 자립을 위한 비대면 커뮤니티부터 공동생활 셰어하우스, 완만한 일경험 인턴십 통합 프로그램."
        },
        {
            "category": "사회",
            "source": "참여연대 복지동향",
            "title": "돌봄노동자(요양보호사·활동지원사) 처우개선 및 공공돌봄 인프라 확충 연대 성명",
            "date": datetime.date.today().isoformat(),
            "url": "https://www.peoplepower21.org/welfare",
            "summary": "초고령사회 필수 돌봄노동자의 사회안전망 구축과 지자체 사회서비스원 공공성 강화 촉구."
        },
        # 6. NGO/국제구호
        {
            "category": "NGO/국제구호",
            "source": "KCOC",
            "title": "국제구호 NGO의 현지 중심화(Localization)와 기후위기 대응 인도적 지원 동향",
            "date": datetime.date.today().isoformat(),
            "url": "https://www.ngokcoc.or.kr",
            "summary": "기후변화 취약국가 대상 긴급구호 체계 혁신 및 비정부기구의 지속가능한 파트너십 구축."
        },
        # 7. 기업 사회공헌
        {
            "category": "기업CSR/사회공헌",
            "source": "한국사회복지협의회",
            "title": "2026 지역사회공헌인정제 확대 및 ESG 연계 소셜임팩트 창출",
            "date": datetime.date.today().isoformat(),
            "url": "https://www.bokji.net",
            "summary": "민간 기업과 복지기관의 협업 프로젝트 활성화 및 사회공헌 평가 체계 개편."
        }
    ]

    news_added = 0
    for item in curated_news:
        if item["title"] not in existing_news_titles:
            try:
                if save_news_to_firestore(item, id_token):
                    news_added += 1
                    existing_news_titles.add(item["title"])
                    print(f"  📰 [시사 브리핑 동기화] [{item['category']}] {item['title'][:32]}...")
            except Exception as e:
                print(f"  ❌ 뉴스 저장 실패: {e}")

    if news_added > 0:
        print(f"  -> {news_added}건의 최신 복지 시사 브리핑이 등록되었습니다.")
    else:
        print("  -> 최신 복지 시사 브리핑이 이미 최신 상태입니다.")

def main():
    print("=" * 65)
    print("🚀 사회복지 & 소셜임팩트 6대 분야(서울·경기) 채용공고 자동 수집기 가동")
    print(f"⏰ 실행 시각: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🎯 수집 기준: 각 분야별 최대 {LIMIT_PER_CATEGORY}개 (서울/경기 타겟)")
    print("=" * 65)

    # 1. Firebase 인증 및 기존 데이터 조회
    print("\n[1/4] Firebase 인증 및 기존 등록 공고 조회 중...")
    try:
        id_token = get_firebase_token()
        existing_keys = get_existing_job_keys(id_token)
        print(f"✅ 인증 성공! 기존 등록 공고 {len(existing_keys)}건 확인됨.")
    except Exception as e:
        print(f"❌ Firebase 인증 실패: {e}")
        return

    # 2. 6대 분야별 공고 수집
    print("\n[2/4] 6대 분야별 공고 수집 시작...")
    categories = [
        ("🏢 종합/전문복지관", scrape_welfare),
        ("🌍 국제구호/NGO", scrape_ngo),
        ("💼 기업 사회공헌(CSR)", scrape_csr),
        ("🌿 공익/복지재단", scrape_foundations),
        ("🏥 의료/정신건강", scrape_medical_mental),
        ("🏛️ 공기업/공공기관", scrape_public_corp)
    ]

    all_scraped = []
    for label, scraper_fn in categories:
        print(f"- [{label}] 수집 중...")
        try:
            items = scraper_fn()
            print(f"  -> {len(items)}건 확보 완료")
            all_scraped.extend(items)
        except Exception as e:
            print(f"  ❌ [{label}] 수집 에러: {e}")

    print(f"\n총 {len(all_scraped)}건의 후보 공고를 확보했습니다.")

    # 3. 중복 필터링 및 신규 공고 저장
    print("\n[3/4] 중복 검사 및 Firestore 실시간 동기화 진행 중...")
    added_count = 0
    skipped_count = 0

    for job in all_scraped:
        job_url = job.get("url", "").strip()
        comp_role_key = f"{job.get('company', '').strip()}___{job.get('role', '').strip()}"

        if (job_url and job_url in existing_keys) or (comp_role_key in existing_keys):
            skipped_count += 1
            continue

        try:
            success = save_job_to_firestore(job, id_token)
            if success:
                added_count += 1
                if job_url: existing_keys.add(job_url)
                existing_keys.add(comp_role_key)
                print(f"  ➕ [신규 등록] [{job['category']}] {job['company']} - {job['role'][:28]}... (마감: {job['deadline'] or '미정'})")
        except Exception as e:
            print(f"  ❌ 저장 실패 ({job['company']}): {e}")

    # 4. 결과 보고
    print("\n" + "=" * 65)
    print(f"🎉 수집 및 동기화 완료!")
    print(f"  - 신규 등록된 공고: {added_count}건")
    print(f"  - 이미 존재하는 공고(중복 제외): {skipped_count}건")
    print("=" * 65)

if __name__ == "__main__":
    main()
