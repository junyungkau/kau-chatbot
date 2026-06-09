import os
import re
import json
import hashlib
import time
from urllib.parse import urljoin, urlparse, urldefrag
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from google import genai
from google.genai import types

# 차단 방지용 기본 헤더 세팅
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://kau.ac.kr/'
}

# 디렉토리 기본 경로
BASE_DIR = "./KAU_Crawling_Data"

# 본문 해시 / 변경 감지용 manifest
MANIFEST_FILE = os.path.join(BASE_DIR, "kau_manifest.json")

# 게시판 사령탑 타겟 파일
BLACKOUT_FILE = "crawler_target.txt"

# 제외할 링크 리스트
EXCLUDED_LINKS = [
    "acdnoti.php", "scholnoti.php", "event.php", "recruitment.php", "bid.php",
    "covidnoti.php", "itnoti.php", "iunoti.php", "notice.php", "speech",
    "voc.php", "lostitem.php", "labnoti.php", "labappl.php", "compliment.php",
    "budget.php", "busipromo.php", "fund.php", "tuition.php", "donation.php",
    "english", "chinese", "brochure", "provided.php"
]


def normalize_url(url):
    if url is None:
        return ""

    url = url.strip()
    url, _ = urldefrag(url)
    url = url.replace("http://", "https://")
    url = url.replace("https://www.kau.ac.kr", "https://kau.ac.kr")

    if url.endswith('?'):
        url = url[:-1]

    return url


def normalize_for_hash(text):
    if text is None:
        return ""

    if "[본문 내용]" in text:
        text = text.split("[본문 내용]", 1)[1]

    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    return text


def make_content_hash(text):
    text = normalize_for_hash(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_bytes_hash(data):
    if data is None:
        data = b""
    return hashlib.sha256(data).hexdigest()


def load_manifest():
    os.makedirs(BASE_DIR, exist_ok=True)

    print(f"[manifest 읽기 시도] {os.path.abspath(MANIFEST_FILE)}")

    if not os.path.exists(MANIFEST_FILE):
        print("[manifest 없음] 새로 시작합니다.")
        return {}

    try:
        with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        if not isinstance(manifest, dict):
            raise ValueError("manifest JSON 최상위 구조가 dict가 아닙니다.")

        print(f"[manifest 읽기 완료] {len(manifest)}개 등록됨")
        return manifest

    except Exception as e:
        print(f"[manifest 읽기 실패] {MANIFEST_FILE}")
        print(f"오류 내용: {e}")
        raise


def save_manifest(manifest):
    try:
        os.makedirs(BASE_DIR, exist_ok=True)
        with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"manifest 저장 중 오류 발생: {e}")


def make_manifest_key(url):
    return normalize_url(url)


def should_save_by_manifest(manifest, url, content_text):
    manifest_key = make_manifest_key(url)
    new_hash = make_content_hash(content_text)

    if manifest_key not in manifest:
        return True, new_hash, "new", manifest_key

    old_hash = manifest.get(manifest_key, {}).get("content_hash")
    if old_hash != new_hash:
        return True, new_hash, "modified", manifest_key

    return False, new_hash, "same", manifest_key


def update_manifest(
    manifest,
    url,
    title,
    date,
    file_path,
    content_text,
    status,
    manifest_key=None,
    content_hash_override=None
):
    if manifest_key is None:
        manifest_key = make_manifest_key(url)

    if content_hash_override is None:
        content_hash = make_content_hash(content_text)
    else:
        content_hash = content_hash_override

    manifest[manifest_key] = {
        "key": manifest_key,
        "url": normalize_url(url),
        "title": title,
        "date": date,
        "file_path": file_path,
        "content_hash": content_hash,
        "last_status": status,
        "last_checked": time.strftime("%Y-%m-%d %H:%M:%S")
    }


def load_target_urls(target_file):
    if os.path.exists(target_file):
        with open(target_file, 'r', encoding='utf-8') as f:
            return list(dict.fromkeys(line.strip() for line in f if line.strip()))
    return []


def save_target_url(target_file, url):
    with open(target_file, 'a', encoding='utf-8') as f:
        f.write(url + '\n')


def load_blackout_urls():
    if os.path.exists(BLACKOUT_FILE):
        with open(BLACKOUT_FILE, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def is_blacklisted(url, blackout_urls):
    url_base = url.split('?')[0]

    for b_url in blackout_urls:
        b_url_base = b_url.split('?')[0]
        if url_base.startswith(b_url_base):
            return True

    return False


API_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_key.txt")


def get_api_key():
    if not os.path.exists(API_KEY_FILE):
        print("API 키 파일을 찾을 수 없습니다.")
        return None

    with open(API_KEY_FILE, 'r', encoding='utf-8') as f:
        key = f.read().strip()
        print("API 키 세팅 완료.")
        return key


GEMINI_API_KEY = get_api_key()
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def process_pdf_with_gemini(pdf_url):
    try:
        response = requests.get(pdf_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        pdf_content = response.content
        pdf_hash = make_bytes_hash(pdf_content)
    except Exception as e:
        return f"(PDF 다운로드 실패: {e})", ""

    if not client:
        return "(API 키가 없어서 PDF 분석 불가)", pdf_hash

    prompt = """
    이 문서는 대학교 공지사항 문서입니다. 
    문서 내에 있는 모든 텍스트를 빠짐없이 추출해주세요.

    [매우 중요한 지시사항]
    1. 만약 문서 안에 표(Table)가 있다면, 반드시 Markdown 표 형식(| 컬럼1 | 컬럼2 |)으로 구조를 완벽하게 유지하여 작성해주세요.
    2. 병합된 셀이 있다면, 행/열 구조가 깨지지 않도록 적절히 내용을 반복하거나 분배하여 Markdown 표 문법에 맞게 맞춰주세요.
    3. 표가 아닌 일반 텍스트는 원본의 문맥과 줄바꿈을 최대한 유지하여 추출해주세요.
    4. 당신의 요약, 설명, 인사말 등은 절대 포함하지 말고, 오직 추출된 텍스트와 표(Markdown)만 반환해주세요.
    """

    try:
        ai_response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                prompt,
                types.Part.from_bytes(data=pdf_content, mime_type='application/pdf')
            ]
        )
        return ai_response.text.strip(), pdf_hash
    except Exception as e:
        return f"(Gemini PDF OCR 에러: {e})", pdf_hash


def get_safe_filename(url):
    try:
        parsed_url = urlparse(url)
        path_segments = parsed_url.path.split('/')
        query = parsed_url.query
        safe_query = "_" + re.sub(r'[^a-zA-Z0-9]', '_', query) if query else ""

        if '.pdf' in url.lower():
            base_name = path_segments[-1] if path_segments[-1] else "document"
            base_name = base_name.lower().replace('.pdf', '')
            return f"pdf_{base_name}{safe_query}.txt"

        if path_segments[-1].endswith('.php'):
            page_name = path_segments[-1].replace('.php', '')
            return f"{page_name}{safe_query}.txt"

    except Exception:
        pass

    clean_name = re.sub(r'[^a-zA-Z0-9]', '_', url.split("://")[-1])
    return f"page_{clean_name[:80]}.txt"


def crawl_page_content_perfect(driver, url):
    try:
        soup = BeautifulSoup(driver.page_source, "html.parser")
        
        garbage_css = 'header, footer, nav, #header, #gnb, .fix_menu, #quick_all, .sub_top, .sub_nav_wrap, .location_wrap, .prev_next, .board_prev_next, .board_btn_wrap, .btn_wrap'
        for garbage in soup.select(garbage_css):
            garbage.decompose()
        
        title_tag = soup.select_one('.sub_article h3, .view_header h4, .board_view_title, .title, h3')
        title = title_tag.get_text(strip=True) if title_tag else "제목 없음"
        
        content_area = soup.select_one(".sub_article")
        if not content_area:
            content_area = soup.body if soup.body else soup
            
        for tag in content_area(["script", "style", "iframe", "noscript"]):
            tag.decompose()

        for a_tag in content_area.find_all('a', href=True):
            href = a_tag['href'].strip()
            if href.startswith(('javascript:', 'mailto:', '#')) or not href:
                continue

            full_href = urljoin(url, href)
            clean_href, _ = urldefrag(full_href)
            a_tag.insert_after(f" [링크: {clean_href}] ")

        # 이미지 OCR / 이미지 해시 사용 안 함
        # 이미지는 본문에서 제거
        imgs = content_area.find_all('img')
        for img in imgs:
            img.decompose()
        
        raw_body = content_area.get_text(separator='\n', strip=True)
        raw_body = raw_body.replace("본문바로가기", "").strip()

        date = "날짜 없음"
        date_match = re.search(r'\d{4}-\d{2}-\d{2}', driver.page_source)
        if date_match:
            date = date_match.group()
            
        final_text = (
            f"출처: {url}\n"
            f"제목: {title}\n"
            f"작성일자: {date}\n\n"
            f"[본문 내용]\n"
            f"{raw_body}"
        )
        final_text = re.sub(r'\n{3,}', '\n\n', final_text)
        
        return final_text, raw_body

    except Exception as e:
        print(f"    파싱 중 내부 에러 발생: {e}")
        return "", ""


def gather_combined_links(start_url, target_file, blackout_urls):
    print(f"\n[1단계] 항공대 전체 도메인 스파이더 가동...")
    raw_target_urls = load_target_urls(target_file)
    
    known_targets = set(normalize_url(u) for u in raw_target_urls)
    start_url_norm = normalize_url(start_url)
    
    queue = []

    if start_url_norm not in known_targets:
        known_targets.add(start_url_norm)
        save_target_url(target_file, start_url_norm)

    queue.append(start_url_norm)
        
    visited_for_links = set()
    newly_found_count = 0
    
    while queue:
        current = queue.pop(0)
        
        if current in visited_for_links:
            continue

        visited_for_links.add(current)
        
        print(f"   탐색 중 (스파이더 대기열 {len(queue)}개 남음) : {current}")
        
        try:
            if '.pdf' in current.lower():
                continue
                
            res = requests.get(current, headers=HEADERS, timeout=10)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, 'html.parser')
            
            for a in soup.find_all('a', href=True):
                raw_href = a['href'].strip()

                if raw_href.startswith(('javascript:', 'mailto:', '#')) or not raw_href:
                    continue
                    
                abs_url = normalize_url(urljoin(current, raw_href))
                
                if is_blacklisted(abs_url, blackout_urls):
                    continue
                    
                if "seq=" in abs_url or "mode=" in abs_url:
                    continue

                if abs_url.startswith("https://kau.ac.kr"):
                    if any(excluded in abs_url for excluded in EXCLUDED_LINKS):
                        continue

                    ignored_exts = ['.mp4', '.jpg', '.jpeg', '.png', '.gif', '.zip', '.hwp', '.css', '.js', '.xlsx', '.doc']
                    if not any(abs_url.lower().endswith(ext) for ext in ignored_exts):
                        if abs_url not in known_targets:
                            known_targets.add(abs_url)
                            queue.append(abs_url)
                            save_target_url(target_file, abs_url)
                            newly_found_count += 1
                            print(f"      [새 링크 발견 및 리스트 추가] {abs_url}")
                            
        except Exception as e:
            print(f"      스파이더 탐색 오류: {e}")
            
    print(f"\n[1단계 완료] 총 {newly_found_count}개의 새로운 링크를 리스트에 추가했습니다.")


def crawl_kau_combined(start_url):
    os.makedirs(BASE_DIR, exist_ok=True)

    target_file = os.path.join(BASE_DIR, "combined_target_list.txt")
    blackout_urls = load_blackout_urls()
    manifest = load_manifest()

    gather_combined_links(start_url, target_file, blackout_urls)
    
    print(f"\n[2단계] 통합 크롬 브라우저 가동 및 다운로드 시작...")
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument(f"user-agent={HEADERS['User-Agent']}")
    
    driver = webdriver.Chrome(options=chrome_options)
    
    all_targets = load_target_urls(target_file)
    
    urls_to_visit = []
    seen = set()

    for u in all_targets:
        nu = normalize_url(u)

        if nu not in seen and not is_blacklisted(nu, blackout_urls):
            urls_to_visit.append(nu)
            seen.add(nu)
    
    print(f"데이터베이스 스캔 결과 -> 전체 타겟: {len(all_targets)}개 | 검사 대상: {len(urls_to_visit)}개\n")
    
    success_count = 0
    same_count = 0
    modified_count = 0
    new_count = 0
    
    try:
        for current_url in urls_to_visit:
            print(f"검사 중 [{success_count + same_count + 1}번째] : {current_url}")
            
            parsed_path = urlparse(current_url).path.strip('/')
            current_category = parsed_path.split('/')[0] if parsed_path else "main"
            current_save_dir = os.path.join(BASE_DIR, current_category)
            os.makedirs(current_save_dir, exist_ok=True)
            
            try:
                if '.pdf' in current_url.lower():
                    print(f"    [PDF 분석 크롤링] PDF 파일 해시 검사 중: {current_url[:50]}...")

                    pdf_text, pdf_hash = process_pdf_with_gemini(current_url)

                    if not pdf_hash:
                        print("    제외: PDF 파일 해시 생성 실패")
                        continue

                    manifest_key = make_manifest_key(current_url)
                    old_hash = manifest.get(manifest_key, {}).get("content_hash")

                    if old_hash == pdf_hash:
                        print(f"    [PDF 파일 변경 없음] OCR 생략: {current_url}")
                        same_count += 1
                        continue

                    final_text = (
                        f"출처: {current_url}\n"
                        f"제목: PDF 문서 추출\n"
                        f"작성일자: 날짜 없음\n\n"
                        f"[PDF 파일 해시]\n"
                        f"{pdf_hash}\n\n"
                        f"[본문 내용]\n"
                        f"{pdf_text}"
                    )
                    
                    if not pdf_text or len(pdf_text.strip()) < 15:
                        print("    제외: 유효한 알맹이 데이터가 부족한 PDF 내용")
                        continue
                        
                    file_name = get_safe_filename(current_url)
                    file_path = os.path.join(current_save_dir, file_name)

                    manifest_status = "new" if manifest_key not in manifest else "modified"

                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(final_text)

                    update_manifest(
                        manifest,
                        current_url,
                        "PDF 문서 추출",
                        "날짜 없음",
                        file_path,
                        pdf_text,
                        manifest_status,
                        manifest_key=manifest_key,
                        content_hash_override=pdf_hash
                    )
                    save_manifest(manifest)
                        
                    if manifest_status == "new":
                        print(f"    [새 PDF 저장 완료] {file_name}")
                        new_count += 1
                    elif manifest_status == "modified":
                        print(f"    [수정 PDF 갱신 완료] {file_name}")
                        modified_count += 1

                    success_count += 1
                    continue

                driver.get(current_url)
                time.sleep(1.0)
                
                final_text, raw_body = crawl_page_content_perfect(driver, current_url)
            
                if not raw_body or len(raw_body.strip()) < 15:
                    print("    제외: 유효한 알맹이 데이터가 부족한 페이지")
                    continue
                    
                file_name = get_safe_filename(current_url)
                file_path = os.path.join(current_save_dir, file_name)

                should_save, _, manifest_status, manifest_key = should_save_by_manifest(
                    manifest,
                    current_url,
                    raw_body
                )

                if not should_save:
                    print(f"    [변경 없음] 스킵: {file_name}")
                    same_count += 1
                    continue
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(final_text)

                title_match = re.search(r"제목:\s*(.*)", final_text)
                date_match = re.search(r"작성일자:\s*(.*)", final_text)
                page_title = title_match.group(1).strip() if title_match else "제목 없음"
                page_date = date_match.group(1).strip() if date_match else "날짜 없음"

                update_manifest(
                    manifest,
                    current_url,
                    page_title,
                    page_date,
                    file_path,
                    raw_body,
                    manifest_status,
                    manifest_key=manifest_key
                )
                save_manifest(manifest)
                    
                if manifest_status == "new":
                    print(f"    [새 파일 저장 완료] ({current_category} 폴더): {file_name}")
                    new_count += 1
                elif manifest_status == "modified":
                    print(f"    [수정 파일 갱신 완료] ({current_category} 폴더): {file_name}")
                    modified_count += 1

                success_count += 1
                
            except Exception as e:
                print(f"    네트워크 통신 오류: {e}")
                continue
                
        print(f"\n통합 크롤링 작업 완료")
        print(f"새로 저장: {new_count}개")
        print(f"수정 갱신: {modified_count}개")
        print(f"변경 없음: {same_count}개")
        print(f"저장/갱신 합계: {success_count}개")
        print(f"본문 manifest: {MANIFEST_FILE}")
        
    finally:
        driver.quit()


if __name__ == "__main__":
    START_URL = "https://kau.ac.kr/index/main.php"
    
    print(f"\n{'=' * 60}")
    print(f"[통합] 크롤링 파이프라인 시작 (출발지: {START_URL})")
    print(f"{'=' * 60}")
    
    crawl_kau_combined(START_URL)
        
    print("\n항공대 전체 도메인 크롤링이 종료되었습니다.")