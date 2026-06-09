import os
import re
import time
import json
import hashlib
from urllib.parse import urljoin, urlparse, urldefrag, parse_qs
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from google import genai
from google.genai import types

# 차단 방지용 기본 헤더 세팅
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://college.kau.ac.kr/'
}

# 디렉토리 기본 경로 (단과대 전용 폴더로 분리)
BASE_DIR = "./KAU_College_Crawling_Data"
MANIFEST_FILE = os.path.join(BASE_DIR, "college_manifest.json")
IMAGE_MANIFEST_FILE = os.path.join(BASE_DIR, "college_image_manifest.json")

# URL 정규화 함수
def normalize_url(url):
    url, _ = urldefrag(url)
    url = url.replace("http://", "https://")
    url = url.replace("https://www.college.kau.ac.kr", "https://college.kau.ac.kr")
    if url.endswith('?'):
        url = url[:-1]
    return url

# manifest 읽기
def load_manifest():
    if os.path.exists(MANIFEST_FILE):
        try:
            with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

# manifest 저장
def save_manifest(manifest):
    try:
        with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"manifest 저장 중 오류 발생: {e}")

# 이미지 manifest 읽기
def load_image_manifest():
    if os.path.exists(IMAGE_MANIFEST_FILE):
        try:
            with open(IMAGE_MANIFEST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

# 이미지 manifest 저장
def save_image_manifest(image_manifest):
    try:
        with open(IMAGE_MANIFEST_FILE, "w", encoding="utf-8") as f:
            json.dump(image_manifest, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"image manifest 저장 중 오류 발생: {e}")

# 이미지 파일 해시 생성
def make_bytes_hash(data):
    if data is None:
        data = b""
    return hashlib.sha256(data).hexdigest()

# 해시용 본문 정규화
def normalize_for_hash(text):
    if text is None:
        return ""

    if "[본문 내용]" in text:
        text = text.split("[본문 내용]", 1)[1]

    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    return text

# 본문 해시 생성
def make_content_hash(text):
    text = normalize_for_hash(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

# manifest 고유키 생성
def make_manifest_key(url=None, bbs_id=None, ntt_id=None):
    if bbs_id and ntt_id:
        return f"bbs:{bbs_id}:{ntt_id}"
    return normalize_url(url)

# 새 글 / 수정 글 판단
def should_save_by_manifest(manifest, url, content_text, bbs_id=None, ntt_id=None):
    manifest_key = make_manifest_key(url, bbs_id, ntt_id)
    new_hash = make_content_hash(content_text)

    if manifest_key not in manifest:
        return True, new_hash, "new", manifest_key

    old_hash = manifest.get(manifest_key, {}).get("content_hash")
    if old_hash != new_hash:
        return True, new_hash, "modified", manifest_key

    return False, new_hash, "same", manifest_key

def normalize_title_for_skip(title):
    """
    게시판 글 제목 비교용.
    공백 차이만 정리하고, [25.11. 수정] 같은 문구는 유지합니다.
    """
    if title is None:
        return ""

    title = str(title)
    title = re.sub(r"\s+", " ", title)
    title = title.strip()
    return title

def should_skip_board_post_by_title(manifest, bbs_id, ntt_id, list_title):
    """
    게시판 형식 글에서 목록 제목이 기존 manifest 제목과 같으면
    본문 API 호출을 생략합니다.
    """
    manifest_key = make_manifest_key(bbs_id=bbs_id, ntt_id=ntt_id)

    old_info = manifest.get(manifest_key)
    if not old_info:
        return False

    old_title = normalize_title_for_skip(old_info.get("title", ""))
    new_title = normalize_title_for_skip(list_title)

    if old_title and new_title and old_title == new_title:
        return True

    return False

# manifest 업데이트
def update_manifest(manifest, url, title, date, file_path, content_text, status, manifest_key=None):
    if manifest_key is None:
        manifest_key = make_manifest_key(url)

    manifest[manifest_key] = {
        "key": manifest_key,
        "url": normalize_url(url),
        "title": title,
        "date": date,
        "file_path": file_path,
        "content_hash": make_content_hash(content_text),
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

# API 키 세팅
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

# PDF OCR 분석 엔진
def process_pdf_with_gemini(pdf_url):
    if not client:
        return "(API 키가 없어서 PDF 분석 불가)"
    try:
        response = requests.get(pdf_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        pdf_content = response.content
    except Exception as e:
        return f"(PDF 다운로드 실패: {e})"
    
    prompt = """
    이 문서는 대학교 문서입니다. 
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
        return ai_response.text.strip()
    except Exception as e:
        return (
            "[OCR 실패]\n"
            "사유: Gemini PDF OCR 처리 중 오류 발생\n"
            f"오류내용: {e}\n"
            f"원본 파일: {pdf_url}"
        )

# Content-Type으로 이미지 mime type 추정
def get_image_mime_type(content_type, image_url):
    content_type = (content_type or "").lower()
    image_url_lower = image_url.lower()

    if "png" in content_type or image_url_lower.endswith(".png"):
        return "image/png"
    elif "webp" in content_type or image_url_lower.endswith(".webp"):
        return "image/webp"
    elif "gif" in content_type or image_url_lower.endswith(".gif"):
        return "image/gif"
    else:
        return "image/jpeg"

# 이미지 해시 기준으로 기존 OCR 캐시 찾기
def find_cached_ocr_by_image_hash(image_manifest, image_hash):
    for cached_url, info in image_manifest.items():
        if isinstance(info, dict) and info.get("image_hash") == image_hash:
            return cached_url, info.get("ocr_text", "")
    return None, None

# 이미지 OCR 분석 엔진
def process_image_with_gemini(image_url):
    image_url = normalize_url(image_url)

    try:
        response = requests.get(image_url, headers=HEADERS, timeout=30)
        response.raise_for_status()

        image_content = response.content
        image_hash = make_bytes_hash(image_content)

        image_manifest = load_image_manifest()
        old_info = image_manifest.get(image_url)

        # 1. URL도 같고 이미지 해시도 같으면 OCR 안 함
        if old_info and old_info.get("image_hash") == image_hash:
            cached_ocr = old_info.get("ocr_text", "")
            print(f"    [이미지 변경 없음] OCR 생략, URL 캐시 사용: {image_url}")
            return cached_ocr, image_hash

        # 2. URL은 달라도 이미지 파일 해시가 같으면 OCR 안 함
        cached_url, cached_ocr = find_cached_ocr_by_image_hash(image_manifest, image_hash)
        if cached_url:
            print(f"    [이미지 파일 해시 동일] OCR 생략, 해시 캐시 사용: {image_url}")

            image_manifest[image_url] = {
                "url": image_url,
                "image_hash": image_hash,
                "ocr_text": cached_ocr,
                "cached_from_url": cached_url,
                "last_checked": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            save_image_manifest(image_manifest)

            return cached_ocr, image_hash

        if not client:
            return "(API 키가 없어서 이미지 분석 불가)", image_hash

        content_type = response.headers.get("Content-Type", "").lower()
        mime_type = get_image_mime_type(content_type, image_url)

    except Exception as e:
        return (
            "[OCR 실패]\n"
            "사유: Gemini 이미지 OCR 처리 중 오류 발생\n"
            f"오류내용: {e}\n"
            f"원본 이미지: {image_url}"
        ), ""

    print(f"    [이미지 변경 감지] Gemini OCR 실행: {image_url}")

    prompt = """
    이 이미지는 대학교 홈페이지 본문에 포함된 이미지입니다.
    이미지 안에 있는 모든 텍스트를 빠짐없이 추출해주세요.

    [매우 중요한 지시사항]
    1. 이미지 안에 표(Table)가 있다면 반드시 Markdown 표 형식(| 컬럼1 | 컬럼2 |)으로 구조를 최대한 유지해서 작성해주세요.
    2. 병합된 셀이나 복잡한 표가 있어도 행/열 관계가 최대한 보존되도록 작성해주세요.
    3. 표가 아닌 일반 텍스트는 원본의 문맥과 줄바꿈을 최대한 유지해서 추출해주세요.
    4. 당신의 요약, 설명, 인사말 등은 절대 포함하지 말고 오직 추출된 텍스트와 표(Markdown)만 반환해주세요.
    5. 아이콘, 로고, 단순 장식 이미지는 의미 있는 텍스트가 없으면 빈값으로 반환해주세요.
    """
    
    try:
        ai_response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=[
                prompt,
                types.Part.from_bytes(data=image_content, mime_type=mime_type)
            ]
        )

        ocr_text = ai_response.text.strip()

        image_manifest[image_url] = {
            "url": image_url,
            "image_hash": image_hash,
            "ocr_text": ocr_text,
            "last_checked": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        save_image_manifest(image_manifest)

        return ocr_text, image_hash

    except Exception as e:
        return f"(Gemini 이미지 OCR 에러: {e})", image_hash

# 이미지가 장식용인지 판단
def is_decorative_image(src):
    src_lower = src.lower()

    return any(skip_word in src_lower for skip_word in [
        'icon_', 'logo', 'btn_', 'facebook', 'twitter', 'blog',
        'arr', 'arrow', 'home', 'close', 'menu', 'page'
    ])

# HTML 안의 이미지들을 OCR 결과 + 이미지 해시 placeholder로 교체
def replace_images_with_ocr_placeholders(content_area, base_url, soup_for_new_tag):
    imgs = content_area.find_all('img')

    for idx, img in enumerate(imgs, start=1):
        src = img.get('src', '').strip()

        if not src:
            img.decompose()
            continue

        if is_decorative_image(src):
            img.decompose()
            continue

        image_url = normalize_url(urljoin(base_url, src))

        ocr_text, image_hash = process_image_with_gemini(image_url)

        # OCR 텍스트가 짧아도 이미지 해시는 남김
        # 그래야 이미지뿐인 본문에서 이미지 변경 여부를 본문 해시로 판단 가능
        if image_hash:
            image_placeholder = soup_for_new_tag.new_tag("div")

            if ocr_text and len(ocr_text.strip()) > 0:
                image_placeholder.string = (
                    f"\n\n[이미지 {idx}]\n"
                    f"이미지 URL: {image_url}\n"
                    f"이미지 해시: {image_hash}\n"
                    f"[이미지 OCR 결과]\n"
                    f"{ocr_text}\n"
                )
            else:
                image_placeholder.string = (
                    f"\n\n[이미지 {idx}]\n"
                    f"이미지 URL: {image_url}\n"
                    f"이미지 해시: {image_hash}\n"
                    f"[이미지 OCR 결과]\n"
                    f"\n"
                )

            img.replace_with(image_placeholder)
        else:
            img.decompose()

# 파일명 중복 덮어쓰기 방지
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
        if path_segments[-1].endswith('.do') or path_segments[-1].endswith('.php'):
            page_name = path_segments[-1].replace('.do', '').replace('.php', '')
            return f"{page_name}{safe_query}.txt"
    except Exception:
        pass
    clean_name = re.sub(r'[^a-zA-Z0-9]', '_', url.split("://")[-1])
    return f"page_{clean_name[:80]}.txt"

# 본문 텍스트 수집
def crawl_page_content_perfect(driver, url):
    try:
        soup = BeautifulSoup(driver.page_source, "html.parser")
        
        content_area = soup.select_one(".sub_conts")
        if not content_area:
            return "", ""

        title_tag = content_area.select_one(".sub_tit h3, h3")
        title = title_tag.get_text(strip=True) if title_tag else "제목 없음"
            
        for tag in content_area(["script", "style", "iframe", "noscript"]):
            tag.decompose()

        def table_to_markdown(table):
            rows = []

            for tr in table.find_all("tr"):
                cells = tr.find_all(["th", "td"])
                row = []

                for cell in cells:
                    text = cell.get_text(separator=" ", strip=True)
                    text = re.sub(r"\s+", " ", text)
                    text = text.replace("|", "/")
                    row.append(text)

                if row:
                    rows.append(row)

            if not rows:
                return ""

            max_cols = max(len(row) for row in rows)

            normalized_rows = []
            for row in rows:
                normalized_row = row + [""] * (max_cols - len(row))
                normalized_rows.append(normalized_row)

            header = normalized_rows[0]
            body = normalized_rows[1:]

            markdown_lines = []
            markdown_lines.append("| " + " | ".join(header) + " |")
            markdown_lines.append("| " + " | ".join(["---"] * max_cols) + " |")

            for row in body:
                markdown_lines.append("| " + " | ".join(row) + " |")

            return "\n".join(markdown_lines)

        # 표가 있으면 Markdown 표로 바꿔서 본문에 남김
        tables = content_area.find_all("table")
        for idx, table in enumerate(tables, start=1):
            markdown_table = table_to_markdown(table)

            if markdown_table:
                table_placeholder = soup.new_tag("div")
                table_placeholder.string = f"\n\n[표 {idx}]\n{markdown_table}\n"
                table.replace_with(table_placeholder)
            else:
                table.decompose()

        for a_tag in content_area.find_all('a', href=True):
            href = a_tag['href'].strip()
            if href.startswith(('javascript:', 'mailto:', '#')) or not href:
                continue
            full_href = urljoin(url, href)
            clean_href, _ = urldefrag(full_href)
            a_tag.insert_after(f" [링크: {clean_href}] ")

        # 이미지 OCR + 이미지 파일 해시를 본문에 포함
        replace_images_with_ocr_placeholders(content_area, url, soup)
        
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

# 숨겨진 게시글 본문을 호출하는 전용 API 스나이퍼 엔진
def fetch_board_post_via_api(ntt_id, bbs_id, mnu_id, site_flag, source_url=None):
    view_api_url = "https://college.kau.ac.kr/web/bbs/bbsViewApi.gen"
    api_headers = {
        'User-Agent': HEADERS['User-Agent'],
        'Content-Type': 'application/json',
        'Referer': 'https://college.kau.ac.kr/'
    }
    payload = {
        "siteFlag": site_flag,
        "bbsId": bbs_id,
        "nttId": str(ntt_id),
        "mnuId": mnu_id,
        "bbsAuth": "30"
    }
    try:
        response = requests.post(view_api_url, headers=api_headers, json=payload, timeout=10)
        response.raise_for_status()
        
        try:
            data = response.json()
        except ValueError:
            return "", ""
            
        result = data.get('result', {})
        
        title = result.get('nttSj', '제목 없음')
        content = result.get('nttCn', '본문 내용 없음')
        date = result.get('frstRegisterPnttm', '날짜 없음')
        if date != '날짜 없음':
            date = str(date).split()[0]

        # API 본문 HTML 안의 이미지도 OCR + 이미지 해시로 본문에 포함
        content_soup = BeautifulSoup(content, "html.parser")

        for tag in content_soup(["script", "style", "iframe", "noscript"]):
            tag.decompose()

        for a_tag in content_soup.find_all('a', href=True):
            href = a_tag['href'].strip()
            if href.startswith(('javascript:', 'mailto:', '#')) or not href:
                continue
            full_href = urljoin("https://college.kau.ac.kr/", href)
            clean_href, _ = urldefrag(full_href)
            a_tag.insert_after(f" [링크: {clean_href}] ")

        replace_images_with_ocr_placeholders(
            content_soup,
            "https://college.kau.ac.kr/",
            content_soup
        )

        clean_content = content_soup.get_text(separator='\n', strip=True)

        source_text = source_url if source_url else f"API 다이렉트 수집 (nttId: {ntt_id}, bbsId: {bbs_id})"
        
        final_text = (
            f"출처: {source_text}\n"
            f"게시글 식별자: nttId={ntt_id}, bbsId={bbs_id}, mnuId={mnu_id}, siteFlag={site_flag}\n"
            f"제목: {title}\n"
            f"작성일자: {date}\n\n"
            f"[본문 내용]\n"
            f"{clean_content}"
        )
        return final_text, clean_content
    except Exception as e:
        print(f"      [API 스나이퍼 에러] nttId {ntt_id} 호출 실패 또는 올바른 데이터가 아님: {e}")
        return "", ""

# [1단계] 스파이더: 무제한 college.kau.ac.kr 링크 수집
def gather_college_links(start_url, target_file, test_limit=None):
    print(f"\n[1단계] 단과대 도메인(college.kau.ac.kr) 무제한 스파이더 가동...")
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

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument(f"user-agent={HEADERS['User-Agent']}")

    link_driver = webdriver.Chrome(options=chrome_options)

    def add_target_url(found_url, label):
        nonlocal newly_found_count

        found_url = normalize_url(found_url)

        if not found_url.startswith("https://college.kau.ac.kr"):
            return

        ignored_exts = ['.mp4', '.jpg', '.jpeg', '.png', '.gif', '.zip', '.hwp', '.css', '.js', '.xlsx', '.doc']
        if any(found_url.lower().endswith(ext) for ext in ignored_exts):
            return

        if found_url not in known_targets:
            known_targets.add(found_url)
            queue.append(found_url)
            save_target_url(target_file, found_url)
            newly_found_count += 1
            print(f"      [{label}] {found_url}")

    try:
        while queue:
            if test_limit is not None and len(visited_for_links) >= test_limit:
                print(f"   [테스트 제한] 링크 탐색 {test_limit}개까지만 진행하고 중단")
                break

            current = queue.pop(0)
            
            if current in visited_for_links:
                continue
            visited_for_links.add(current)
            
            print(f"   탐색 중 (대기열 {len(queue)}개) : {current}")
            
            try:
                if '.pdf' in current.lower():
                    continue

                link_driver.get(current)
                time.sleep(2.0)

                rendered_html = link_driver.page_source
                soup = BeautifulSoup(rendered_html, 'html.parser')

                # 1. Selenium으로 렌더링된 HTML 전체에서 /web/pages/*.do 직접 추출
                page_link_patterns = [
                    r'https?://(?:www\.)?college\.kau\.ac\.kr/web/pages/[a-zA-Z0-9_-]+\.do(?:\?[^"\'>\s)]*)?',
                    r'/web/pages/[a-zA-Z0-9_-]+\.do(?:\?[^"\'>\s)]*)?',
                    r'web/pages/[a-zA-Z0-9_-]+\.do(?:\?[^"\'>\s)]*)?'
                ]

                for pattern in page_link_patterns:
                    for link in re.findall(pattern, rendered_html):
                        abs_url = normalize_url(urljoin(current, link))
                        add_target_url(abs_url, "상세 페이지 발견")

                # 2. href에 직접 들어있는 링크 수집
                for a in soup.find_all('a', href=True):
                    raw_href = a['href'].strip()

                    if not raw_href or raw_href.startswith(('mailto:', '#')):
                        continue

                    # javascript 안에 /web/pages/*.do가 숨어있는 경우 처리
                    if raw_href.startswith('javascript:'):
                        for pattern in page_link_patterns:
                            for link in re.findall(pattern, raw_href):
                                abs_url = normalize_url(urljoin(current, link))
                                add_target_url(abs_url, "JS 상세 페이지 발견")
                        continue

                    abs_url = normalize_url(urljoin(current, raw_href))
                    add_target_url(abs_url, "새 링크 발견")

                # 3. onclick, data-url, data-href 같은 속성 안에 숨어있는 .do 링크 수집
                for tag in soup.find_all(True):
                    for attr_value in tag.attrs.values():
                        if isinstance(attr_value, list):
                            attr_text = " ".join(attr_value)
                        else:
                            attr_text = str(attr_value)

                        for pattern in page_link_patterns:
                            for link in re.findall(pattern, attr_text):
                                abs_url = normalize_url(urljoin(current, link))
                                add_target_url(abs_url, "속성 상세 페이지 발견")
                                
            except Exception as e:
                print(f"      스파이더 탐색 오류: {e}")

    finally:
        link_driver.quit()
            
    print(f"\n[1단계 완료] 총 {newly_found_count}개의 새로운 링크를 추가했습니다.")

# [2단계] 메인 다운로드
def crawl_college_domain(start_url):
    os.makedirs(BASE_DIR, exist_ok=True)

    target_file = os.path.join(BASE_DIR, "college_target_list.txt")
    manifest = load_manifest()

    # 테스트 제한이 필요하면 숫자로 바꾸세요. 전체 수집은 None.
    TEST_LIMIT = None

    # 1. 싹쓸이 링크 수집
    gather_college_links(start_url, target_file, test_limit=TEST_LIMIT)
    
    print(f"\n[2단계] 크롬 브라우저 가동 및 다운로드 시작...")
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
        if nu not in seen:
            urls_to_visit.append(nu)
            seen.add(nu)

    if TEST_LIMIT is not None:
        urls_to_visit = urls_to_visit[:TEST_LIMIT]

    print(f"데이터베이스 스캔 -> 타겟: {len(all_targets)}개 | 대기열: {len(urls_to_visit)}개\n")
    
    success_count = 0
    
    try:
        for current_url in urls_to_visit:
            parsed_url = urlparse(current_url)
            query_params = parse_qs(parsed_url.query)
            site_flag = query_params.get('siteFlag', [None])[0]
            
            if site_flag:
                current_category = site_flag
            else:
                parsed_path = parsed_url.path.strip('/')
                current_category = parsed_path.split('/')[0] if parsed_path else "main"
                
            current_save_dir = os.path.join(BASE_DIR, current_category)
            os.makedirs(current_save_dir, exist_ok=True)

            print(f"다운로드 중 [{success_count+1}번째] : {current_url} -> [{current_category}] 폴더에 저장")
            
            try:
                # PDF 분기 처리
                if '.pdf' in current_url.lower():
                    print(f"    [PDF 분석] Gemini 가동: {current_url[:50]}...")
                    pdf_text = process_pdf_with_gemini(current_url)
                    
                    final_text = (
                        f"출처: {current_url}\n"
                        f"제목: PDF 문서 추출\n"
                        f"작성일자: 날짜 없음\n\n"
                        f"[본문 내용]\n"
                        f"{pdf_text}"
                    )
                    
                    if not pdf_text or len(pdf_text.strip()) < 15:
                        continue
                        
                    file_name = get_safe_filename(current_url)
                    file_path = os.path.join(current_save_dir, file_name)

                    should_save, _, manifest_status, manifest_key = should_save_by_manifest(manifest, current_url, pdf_text)
                    if not should_save:
                        print(f"    [변경 없음] 스킵: {file_name}")
                        continue

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
                        manifest_key=manifest_key
                    )
                    save_manifest(manifest)
                        
                    if manifest_status == "new":
                        print(f"    [새 PDF 저장 완료] {file_name}")
                    elif manifest_status == "modified":
                        print(f"    [수정 PDF 갱신 완료] {file_name}")

                    success_count += 1
                    continue

                # 일반 웹 페이지 먼저 로드
                driver.get(current_url)
                time.sleep(1.0)

                page_source = driver.page_source
                is_real_board = (
                    ("gfnGetBbsList" in page_source)
                    or ("board_list" in page_source)
                    or ("bbsListApi" in page_source)
                    or ("bbsViewApi" in page_source)
                    or ("bbsId" in page_source and "nttId" in page_source)
                )
                
                bbs_id = query_params.get('bbsId', [None])[0]
                mnu_id = query_params.get('mnuId', [None])[0]

                page_soup = BeautifulSoup(page_source, "html.parser")

                if not bbs_id:
                    bbs_input = page_soup.select_one("#bbsId")
                    if bbs_input:
                        bbs_id = bbs_input.get("value")

                if not mnu_id:
                    mnu_input = page_soup.select_one("#mnuId")
                    if mnu_input:
                        mnu_id = mnu_input.get("value")
                
                if is_real_board:
                    if not bbs_id:
                        bbs_match = re.search(r'["\']?bbsId["\']?\s*:\s*["\']([^"\']+)["\']', page_source)
                        if bbs_match:
                            bbs_id = bbs_match.group(1)
                    if not mnu_id:
                        mnu_match = re.search(r'["\']?mnuId["\']?\s*:\s*["\']([^"\']+)["\']', page_source)
                        if mnu_match:
                            mnu_id = mnu_match.group(1)
                
                # API로 글 수집을 시도하되, 성공 건수가 없을 때를 대비
                api_success_flag = False
                
                if is_real_board and bbs_id and mnu_id:
                    target_flag = site_flag if site_flag else current_category
                    list_api_url = "https://college.kau.ac.kr/web/bbs/bbsListApi.gen"
                    api_headers = {
                        'User-Agent': HEADERS['User-Agent'],
                        'Content-Type': 'application/json',
                        'Referer': 'https://college.kau.ac.kr/'
                    }
                    
                    print(f"    🔥 [진짜 게시판 발견] API 백도어 연동 가동 (bbsId: {bbs_id}, mnuId: {mnu_id})")
                    
                    for page in range(1, 11):
                        list_payload = {
                            "bbsId": bbs_id,
                            "mnuId": mnu_id,
                            "siteFlag": target_flag,
                            "pageIndex": page,
                            "bbsAuth": "30",
                            "pageUnit": "10",
                            "searchCnd": "",
                            "searchWrd": ""
                        }
                        try:
                            list_res = requests.post(list_api_url, headers=api_headers, json=list_payload, timeout=10)
                            if list_res.status_code == 200:
                                try:
                                    list_data = list_res.json()
                                except ValueError:
                                    break
                                    
                                post_list = list_data.get('resultList', [])
                                if not post_list:
                                    break
                                    
                                for post_item in post_list:
                                    ntt_id = post_item.get('nttId')
                                    if ntt_id:
                                        list_title = (
                                            post_item.get("nttSj")
                                            or post_item.get("title")
                                            or post_item.get("subject")
                                            or ""
                                        )

                                        post_url = normalize_url(
                                            f"https://college.kau.ac.kr/web/bbs/bbsView.do?"
                                            f"siteFlag={target_flag}&bbsId={bbs_id}&nttId={ntt_id}&mnuId={mnu_id}"
                                        )

                                        if should_skip_board_post_by_title(manifest, bbs_id, ntt_id, list_title):
                                            print(f"      -> [제목 변경 없음] 본문 검사 생략: {list_title}")
                                            api_success_flag = True
                                            continue

                                        api_final_text, api_raw_body = fetch_board_post_via_api(
                                            ntt_id,
                                            bbs_id,
                                            mnu_id,
                                            target_flag,
                                            source_url=current_url
                                        )

                                        if api_raw_body:
                                            post_file_name = f"post_{bbs_id}_{ntt_id}.txt"
                                            post_file_path = os.path.join(current_save_dir, post_file_name)

                                            should_save, _, manifest_status, manifest_key = should_save_by_manifest(
                                                manifest,
                                                post_url,
                                                api_raw_body,
                                                bbs_id=bbs_id,
                                                ntt_id=ntt_id
                                            )

                                            if not should_save:
                                                print(f"      -> [변경 없음] 스킵: {post_file_name}")
                                                api_success_flag = True
                                                continue

                                            with open(post_file_path, 'w', encoding='utf-8') as pf:
                                                pf.write(api_final_text)

                                            title_match = re.search(r"제목:\s*(.*)", api_final_text)
                                            date_match = re.search(r"작성일자:\s*(.*)", api_final_text)
                                            post_title = title_match.group(1).strip() if title_match else "제목 없음"
                                            post_date = date_match.group(1).strip() if date_match else "날짜 없음"

                                            update_manifest(
                                                manifest,
                                                post_url,
                                                post_title,
                                                post_date,
                                                post_file_path,
                                                api_raw_body,
                                                manifest_status,
                                                manifest_key=manifest_key
                                            )
                                            save_manifest(manifest)

                                            if manifest_status == "new":
                                                print(f"      -> [새 글] 내부 본문 신규 저장: {post_file_name}")
                                            elif manifest_status == "modified":
                                                print(f"      -> [수정 글] 내부 본문 갱신 완료: {post_file_name}")

                                            success_count += 1
                                            api_success_flag = True

                                time.sleep(0.3)
                            else:
                                break
                        except Exception as api_err:
                            print(f"      -> [오류] {api_err}")
                            break
                    
                    # API로 본문 수집에 성공했다면 다음 URL로 넘어감
                    if api_success_flag:
                        continue

                # API 수집이 불발되었거나 게시판이 아니라면 화면에 뜬 텍스트 그대로 저장
                final_text, raw_body = crawl_page_content_perfect(driver, current_url)
            
                if not raw_body or len(raw_body.strip()) < 15:
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
                    print(f"    [새 글 저장 완료] 백업 시스템 가동하여 화면 내용 저장: {file_name}")
                elif manifest_status == "modified":
                    print(f"    [수정 글 갱신 완료] 백업 시스템 가동하여 화면 내용 저장: {file_name}")

                success_count += 1
                
            except Exception as e:
                print(f"    네트워크 통신 오류: {e}")
                continue
                
        print(f"\n✅ 전공별 자동 폴더 분류 크롤링 작업 완료! 총 {success_count}개의 파일이 저장되었습니다.")
        
    finally:
        driver.quit()

if __name__ == "__main__":
    START_URL = "https://college.kau.ac.kr/web/index.do?siteFlag=new_major_avm"
    
    print(f"\n{'='*60}")
    print(f"🚀 [전공별 자동 폴더링] 크롤링 파이프라인 시작")
    print(f"{'='*60}")
    
    crawl_college_domain(START_URL)