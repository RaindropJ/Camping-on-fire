# -*- coding: utf-8 -*-
"""
사회복지 & 소셜임팩트 3대 영역(복지관·NGO·CSR) 채용공고 자동 수집기
- 대상: 서울사협(복지관), KCOC(국제구호/NGO), 사람인(기업 사회공헌 CSR)
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
AUTH_URL = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_API_KEY}"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
}

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
    url = f"{FIRESTORE_URL}?pageSize=300"
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
            "createdAt": {"stringValue": job.get("createdAt", datetime.datetime.utcnow().isoformat() + "Z")}
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

# =========================================================================
# 1. 복지관 수집기 (서울시사회복지사협회 sasw.or.kr)
# =========================================================================
def scrape_sasw():
    jobs = []
    url = "https://sasw.or.kr/recruit"
    ctx = ssl._create_unverified_context()
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            soup = BeautifulSoup(r.read().decode('utf-8', errors='ignore'), 'html.parser')
            rows = soup.select('table tbody tr')
            for row in rows[:8]: # 최근 8건
                a = row.find('a', href=True)
                if not a:
                    continue
                title = a.get_text().strip()
                href = a['href']
                full_url = f"https://sasw.or.kr{href}" if href.startswith('/') else href

                # 기관명 추출 (대괄호 [기관명] 또는 복지관 명칭 분석)
                company = "복지시설"
                bracket_match = re.search(r'\[(.*?)\]', title)
                if bracket_match:
                    company = bracket_match.group(1).replace("긴급", "").replace("끌어올림", "").replace("채용", "").strip()
                else:
                    words = title.split()
                    for w in words:
                        if any(k in w for k in ['복지관', '센터', '원', '요양원', '협회', '쉼터']):
                            company = w
                            break
                    if company == "복지시설" and len(words) > 0:
                        company = words[0]

                # 상세 페이지에서 마감일 추출 시도
                deadline = ""
                try:
                    detail_req = urllib.request.Request(full_url, headers=HEADERS)
                    with urllib.request.urlopen(detail_req, context=ctx, timeout=5) as dr:
                        dsoup = BeautifulSoup(dr.read().decode('utf-8', errors='ignore'), 'html.parser')
                        for tr in dsoup.select('table tr'):
                            txt = tr.get_text()
                            if '접수기간' in txt or '마감' in txt:
                                date_matches = re.findall(r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', txt)
                                if date_matches:
                                    last_d = date_matches[-1]
                                    deadline = f"{last_d[0]}-{int(last_d[1]):02d}-{int(last_d[2]):02d}"
                                    break
                except Exception:
                    pass

                jobs.append({
                    "category": "종합/전문복지관",
                    "company": company,
                    "role": title,
                    "deadline": deadline,
                    "url": full_url,
                    "memo": "[자동수집] 서울시사회복지사협회 공고"
                })
    except Exception as e:
        print(f"[오류] 서울사협 수집 실패: {e}")
    return jobs

# =========================================================================
# 2. 국제구호/NGO 수집기 (KCOC ngokcoc.or.kr)
# =========================================================================
def scrape_kcoc():
    jobs = []
    url = "https://www.ngokcoc.or.kr/bbs/board.php?bo_table=news07"
    ctx = ssl._create_unverified_context()
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            soup = BeautifulSoup(r.read().decode('utf-8', errors='ignore'), 'html.parser')
            rows = soup.select('table tbody tr') or soup.select('tbody tr')
            today = datetime.date.today()
            for row in rows[:8]: # 최근 8건
                tds = row.find_all('td')
                if len(tds) < 3:
                    continue
                a = row.find('a', href=True)
                if not a:
                    continue
                company = tds[1].get_text().strip()
                title = a.get_text().strip()
                full_url = a['href']
                
                # D-Day 파싱
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
                    "memo": f"[자동수집] KCOC 국제개발협력민간협의회 공고 ({dday_td})"
                })
    except Exception as e:
        print(f"[오류] KCOC 수집 실패: {e}")
    return jobs

# =========================================================================
# 3. 기업 사회공헌 CSR 수집기 (사람인 Saramin)
# =========================================================================
def scrape_saramin_csr():
    jobs = []
    url = "https://www.saramin.co.kr/zf_user/search?search_area=main&search_done=y&search_optional_100=n&searchword=%EC%82%AC%ED%9A%8C%EA%B3%B5%ED%97%8C"
    ctx = ssl._create_unverified_context()
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            soup = BeautifulSoup(r.read().decode('utf-8', errors='ignore'), 'html.parser')
            items = soup.select('.item_recruit')
            today = datetime.date.today()
            for item in items[:8]: # 최근 8건
                corp_el = item.select_one('.corp_name a')
                job_el = item.select_one('.job_tit a')
                date_el = item.select_one('.job_date .date')
                if not corp_el or not job_el:
                    continue
                company = corp_el.get_text().strip()
                role = job_el.get_text().strip()
                href = job_el['href']
                full_url = f"https://www.saramin.co.kr{href}" if href.startswith('/') else href

                # 마감일 파싱 (~ 11/30(월) 형식)
                deadline = ""
                if date_el:
                    date_txt = date_el.get_text().strip()
                    m = re.search(r'(\d{1,2})/(\d{1,2})', date_txt)
                    if m:
                        month, day = int(m.group(1)), int(m.group(2))
                        year = today.year
                        # 1월로 넘어가는 케이스
                        if today.month >= 11 and month < 3:
                            year += 1
                        deadline = f"{year}-{month:02d}-{day:02d}"

                jobs.append({
                    "category": "기업 사회공헌(CSR)",
                    "company": company,
                    "role": role,
                    "deadline": deadline,
                    "url": full_url,
                    "memo": "[자동수집] 사람인 기업 사회공헌(CSR) 채용정보"
                })
    except Exception as e:
        print(f"[오류] 사람인 CSR 수집 실패: {e}")
    return jobs

def main():
    print("=" * 60)
    print("🚀 사회복지 & 소셜임팩트 3대 영역 채용공고 자동 수집기 가동")
    print(f"⏰ 실행 시각: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. Firebase 인증 및 기존 데이터 조회
    print("\n[1/4] Firebase 인증 및 기존 등록 공고 조회 중...")
    try:
        id_token = get_firebase_token()
        existing_keys = get_existing_job_keys(id_token)
        print(f"✅ 인증 성공! 기존 등록 공고 {len(existing_keys)}건 확인됨.")
    except Exception as e:
        print(f"❌ Firebase 인증 실패: {e}")
        return

    # 2. 3대 영역 공고 크롤링
    print("\n[2/4] 3대 영역 채용공고 수집 시작...")
    all_scraped = []

    print("- [🏢 복지관] 서울시사회복지사협회 수집 중...")
    sasw_jobs = scrape_sasw()
    print(f"  -> {len(sasw_jobs)}건 수집 완료")
    all_scraped.extend(sasw_jobs)

    print("- [🌍 NGO] KCOC 국제개발협력민간협의회 수집 중...")
    kcoc_jobs = scrape_kcoc()
    print(f"  -> {len(kcoc_jobs)}건 수집 완료")
    all_scraped.extend(kcoc_jobs)

    print("- [💼 CSR] 사람인 사회공헌 채용 수집 중...")
    saramin_jobs = scrape_saramin_csr()
    print(f"  -> {len(saramin_jobs)}건 수집 완료")
    all_scraped.extend(saramin_jobs)

    print(f"\n총 {len(all_scraped)}건의 최신 공고를 수집했습니다.")

    # 3. 중복 필터링 및 신규 공고 저장
    print("\n[3/4] 중복 검사 및 Firestore 동기화 진행 중...")
    added_count = 0
    skipped_count = 0

    for job in all_scraped:
        job_url = job.get("url", "").strip()
        comp_role_key = f"{job.get('company', '').strip()}___{job.get('role', '').strip()}"

        if job_url in existing_keys or comp_role_key in existing_keys:
            skipped_count += 1
            continue

        try:
            job["createdAt"] = datetime.datetime.utcnow().isoformat() + "Z"
            success = save_job_to_firestore(job, id_token)
            if success:
                added_count += 1
                existing_keys.add(job_url)
                existing_keys.add(comp_role_key)
                print(f"  ➕ [신규 등록] [{job['category']}] {job['company']} - {job['role'][:25]}... (마감: {job['deadline'] or '미정'})")
        except Exception as e:
            print(f"  ❌ 저장 실패 ({job['company']}): {e}")

    # 4. 결과 보고
    print("\n" + "=" * 60)
    print(f"🎉 수집 및 동기화 완료!")
    print(f"  - 신규 등록된 공고: {added_count}건")
    print(f"  - 이미 존재하는 공고(중복 제외): {skipped_count}건")
    print("=" * 60)

if __name__ == "__main__":
    main()
