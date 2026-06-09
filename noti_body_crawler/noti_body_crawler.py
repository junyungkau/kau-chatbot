import requests
from bs4 import BeautifulSoup
import re
import os
import time
import base64
import io
import json
import hashlib
from urllib.parse import urljoin, urldefrag
from google import genai
from google.genai import types
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

# ---------------------------------------------------------
# 접속 설정
# ---------------------------------------------------------
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://kau.ac.kr/'
}

# ---------------------------------------------------------
# 경로 설정
# ---------------------------------------------------------
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# 상대경로 기준으로 통일
BASE_DIR = "./KAU_Crawling_Data"
MANIFEST_FILE = os.path.join(BASE_DIR, "kau_manifest.json")
IMAGE_MANIFEST_FILE = os.path.join(BASE_DIR, "kau_image_manifest.json")

API_KEY_FILE = os.path.join(CURRENT_DIR, "api_key.txt")


# ---------------------------------------------------------
# 기본 유틸
# ---------------------------------------------------------
def normalize_url(url):
    if url is None:
        return ""

    url = url.strip()
    url, _ = urldefrag(url)

    url = url.replace("http://", "https://")
    url = url.replace("https://www.kau.ac.kr", "https://kau.ac.kr")

    if url.endswith("?"):
        url = url[:-1]

    return url


def normalize_file_path(file_path):
    """
    manifest 안의 file_path를 항상 상대경로 형태로 저장.
    예:
    KAU_Crawling_Data/acdnoti/cleaned_txt1/acdnoti_547.txt
    """
    if not file_path:
        return ""

    file_path = file_path.replace("\\", "/")
    base_name = "KAU_Crawling_Data"

    idx = file_path.find(base_name)
    if idx != -1:
        return file_path[idx:]

    return file_path


def normalize_for_hash(text):
    if text is None:
        return ""

    if "[본문 내용]" in text:
        text = text.split("[본문 내용]", 1)[1]

    # 이미지 구간에서는 OCR 결과는 해시 비교에서 제외하고,
    # 이미지 파일 bytes 해시만 남김.
    def keep_only_image_hash(match):
        block = match.group(0)

        hash_match = re.search(r"\[이미지 해시:\s*([^\]]+)\]", block)
        if hash_match:
            image_hash = hash_match.group(1).strip()
            return f"[이미지 해시: {image_hash}]"

        return "[이미지 해시: 해시 없음]"

    text = re.sub(
        r"--- \[이미지 구간 시작\] ---.*?--- \[이미지 구간 끝\] ---",
        keep_only_image_hash,
        text,
        flags=re.DOTALL
    )

    text = re.sub(r"\s+", " ", text)
    return text.strip()

def make_content_hash(text):
    text = normalize_for_hash(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_bytes_hash(data):
    if data is None:
        data = b""
    return hashlib.sha256(data).hexdigest()


def load_json_file(path):
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("JSON 최상위 구조가 dict가 아닙니다.")

        return data

    except Exception as e:
        print(f"[JSON 읽기 실패] {path}")
        print(f"오류 내용: {e}")
        raise


def save_json_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    temp_path = path + ".tmp"
    backup_path = path + ".bak"

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as src:
            old_data = src.read()

        with open(backup_path, "w", encoding="utf-8", newline="\n") as bak:
            bak.write(old_data)

    with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    os.replace(temp_path, path)


def load_manifest():
    return load_json_file(MANIFEST_FILE)


def save_manifest(manifest):
    save_json_file(MANIFEST_FILE, manifest)


def load_image_manifest():
    return load_json_file(IMAGE_MANIFEST_FILE)


def save_image_manifest(image_manifest):
    save_json_file(IMAGE_MANIFEST_FILE, image_manifest)


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


def normalize_title_for_skip(title):
    """
    게시글 제목 비교용.
    공백 차이만 정리하고, [25.11. 수정] 같은 문구는 유지합니다.
    """
    if title is None:
        return ""

    title = str(title)
    title = re.sub(r"\s+", " ", title)
    title = title.strip()
    return title


def should_skip_by_title(manifest, url, current_title):
    """
    기존 manifest에 저장된 제목과 현재 제목이 같으면
    본문/이미지 검사 없이 스킵합니다.

    주의:
    제목은 그대로인데 본문만 수정된 경우는 감지하지 않습니다.
    """
    manifest_key = make_manifest_key(url)

    old_info = manifest.get(manifest_key)
    if not old_info:
        return False

    old_title = normalize_title_for_skip(old_info.get("title", ""))
    new_title = normalize_title_for_skip(current_title)

    if not old_title or not new_title or new_title == "제목 없음":
        return False

    if old_title == new_title:
        return True

    return False


def update_manifest(manifest, url, title, date, file_path, content_text, status, manifest_key=None):
    if manifest_key is None:
        manifest_key = make_manifest_key(url)

    manifest[manifest_key] = {
        "key": manifest_key,
        "url": normalize_url(url),
        "title": title,
        "date": date,
        "file_path": normalize_file_path(file_path),
        "content_hash": make_content_hash(content_text),
        "last_status": status,
        "last_checked": time.strftime("%Y-%m-%d %H:%M:%S")
    }


# ---------------------------------------------------------
# API 키
# ---------------------------------------------------------
def get_api_key():
    if not os.path.exists(API_KEY_FILE):
        print(f"[경고] API 키 파일을 찾을 수 없습니다: {API_KEY_FILE}")
        return None

    with open(API_KEY_FILE, 'r', encoding='utf-8') as f:
        key = f.read().strip()

        if not key:
            print(f"[경고] 파일은 찾았으나 내용이 비어있습니다: {API_KEY_FILE}")
            return None

        print(f"[성공] API 키 세팅 완료! (키 길이: {len(key)}자)")
        return key


GEMINI_API_KEY = get_api_key()
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# ---------------------------------------------------------
# 이미지 처리
# ---------------------------------------------------------
def get_image_mime_type_from_data_url(data_url):
    mime_match = re.search(r"data:(.*?);", data_url)
    if mime_match:
        return mime_match.group(1)
    return "image/jpeg"


def load_image_bytes(image_url):
    """
    일반 이미지 URL 또는 data:image base64를 bytes로 로드.
    반환: image_content, mime_type, image_hash
    """
    image_url = image_url.strip()

    if image_url.startswith("data:image"):
        header, encoded = image_url.split(",", 1)
        mime_type = get_image_mime_type_from_data_url(header)
        image_content = base64.b64decode(encoded)
        image_hash = make_bytes_hash(image_content)
        return image_content, mime_type, image_hash

    response = requests.get(image_url, headers=HEADERS, timeout=60)
    response.raise_for_status()

    image_content = response.content
    mime_type = response.headers.get("Content-Type", "image/jpeg")
    image_hash = make_bytes_hash(image_content)

    return image_content, mime_type, image_hash


def resize_image_if_needed(image_content, mime_type):
    try:
        with Image.open(io.BytesIO(image_content)) as img:
            max_dim = 2048

            if img.width > max_dim or img.height > max_dim:
                img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")

                output = io.BytesIO()
                img.save(output, format="JPEG", quality=85)

                image_content = output.getvalue()
                mime_type = "image/jpeg"

                print(f"    -> [알림] 이미지 해상도가 너무 커서 리사이징 완료 (새 해상도: {img.width}x{img.height})")

    except Exception as e:
        print(f"    -> [알림] 이미지 리사이징 생략 (원본으로 진행): {e}")

    return image_content, mime_type


def make_image_manifest_key(image_url, image_hash):
    if image_url.startswith("data:image"):
        return f"data_image:{image_hash}"

    return normalize_url(image_url)


def make_stored_image_url(image_url):
    if image_url.startswith("data:image"):
        return "data:image/...base64 생략"

    return normalize_url(image_url)


def find_cached_ocr_by_hash(image_manifest, image_hash):
    for cached_key, info in image_manifest.items():
        if isinstance(info, dict) and info.get("image_hash") == image_hash:
            return cached_key, info

    return None, None


def analyze_image_with_gemini(image_url):
    """
    이미지 파일 해시 비교 후,
    기존 이미지와 같으면 Gemini OCR 생략.
    다르면 Gemini OCR 실행 후 ./KAU_Crawling_Data/kau_image_manifest.json에 저장.
    반환: ocr_text, image_hash
    """
    if not client:
        raise ValueError("API 키가 정상적으로 로드되지 않아 이미지 분석을 수행할 수 없습니다.")

    image_url = image_url.strip()

    try:
        image_content, mime_type, image_hash = load_image_bytes(image_url)
    except Exception as e:
        print(f"    -> [알림] 이미지 로드 실패: {e}")
        return "(이미지 다운로드 실패: 만료되거나 형식이 잘못된 이미지입니다.)", ""

    image_manifest = load_image_manifest()
    image_key = make_image_manifest_key(image_url, image_hash)

    old_info = image_manifest.get(image_key)

    if old_info and old_info.get("image_hash") == image_hash:
        cached_ocr = old_info.get("ocr_text", "")

        if cached_ocr and cached_ocr.strip():
            print(f"    -> [이미지 변경 없음] OCR 생략, 캐시 사용")
            return cached_ocr, image_hash

        print(f"    -> [이미지 해시는 같지만 OCR 결과가 비어 있음] Gemini OCR 재실행")
    
    cached_key, cached_info = find_cached_ocr_by_hash(image_manifest, image_hash)

    if cached_info:
        cached_ocr = cached_info.get("ocr_text", "")

        if cached_ocr and cached_ocr.strip():
            print(f"    -> [이미지 해시 동일] OCR 생략, 기존 캐시 연결")

            image_manifest[image_key] = {
                "url": make_stored_image_url(image_url),
                "image_hash": image_hash,
                "ocr_text": cached_ocr,
                "cached_from_key": cached_key,
                "last_checked": time.strftime("%Y-%m-%d %H:%M:%S")
            }

            save_image_manifest(image_manifest)

            return cached_ocr, image_hash

        print(f"    -> [이미지 해시는 같지만 기존 OCR 결과가 비어 있음] Gemini OCR 재실행")
    
    image_content, mime_type = resize_image_if_needed(image_content, mime_type)

    print(f"    -> [새 이미지 또는 이미지 변경] Gemini OCR 실행")

    prompt = """
    이 이미지는 대학교 공지사항 문서입니다. 
    이미지 내에 있는 모든 텍스트를 빠짐없이 추출해주세요.

    [매우 중요한 지시사항]
    1. 만약 이미지 안에 표(Table)가 있다면, 반드시 **Markdown 표 형식**(| 컬럼1 | 컬럼2 |)으로 구조를 완벽하게 유지하여 작성해주세요.
    2. 병합된 셀이 있다면, 행/열 구조가 깨지지 않도록 적절히 내용을 반복하거나 분배하여 Markdown 표 문법에 맞게 맞춰주세요.
    3. 표가 아닌 일반 텍스트는 원본의 문맥과 줄바꿈을 최대한 유지하여 추출해주세요.
    4. 당신의 요약, 설명, 인사말 등은 절대 포함하지 말고, 오직 추출된 텍스트와 표(Markdown)만 반환해주세요.
    """

    try:
        ai_response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=[
                prompt,
                types.Part.from_bytes(
                    data=image_content,
                    mime_type=mime_type
                )
            ]
        )

        ocr_text = ai_response.text.strip()

    except Exception as e:
        ocr_text = f"(Gemini 이미지 OCR 에러: {e})"

    image_manifest[image_key] = {
        "url": make_stored_image_url(image_url),
        "image_hash": image_hash,
        "ocr_text": ocr_text,
        "last_checked": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    save_image_manifest(image_manifest)

    return ocr_text, image_hash


# ---------------------------------------------------------
# URL / HTML 유틸
# ---------------------------------------------------------
def clean_url(base, target):
    if not target:
        return ""

    target = target.strip()

    if target.startswith('data:image'):
        return target

    target = re.sub(r'(https?://)?kau\.ac\.kr\.\.+', 'https://kau.ac.kr', target)

    full_url = urljoin(base, target)
    full_url = re.sub(r'kau\.ac\.kr\.\.+', 'kau.ac.kr', full_url)
    full_url = normalize_url(full_url)

    return full_url


def convert_table_to_markdown(table_tag):
    rows = []

    for tr in table_tag.find_all('tr'):
        cells = []

        for cell in tr.find_all(['th', 'td']):
            cell_text = cell.get_text(separator=' ', strip=True)
            cell_text = re.sub(r'\s+', ' ', cell_text)
            cells.append(cell_text)

        if cells:
            rows.append(f"| {' | '.join(cells)} |")

    if not rows:
        return ""

    max_cols = max(row.count('|') - 1 for row in rows)
    separator = f"| {' | '.join(['---'] * max_cols)} |"

    rows.insert(1, separator)
    return "\n\n" + "\n".join(rows) + "\n\n"


# ---------------------------------------------------------
# 본문 크롤링
# ---------------------------------------------------------
def crawl_body(url, save_dir):
    from noti_list_crawler import get_board_prefix, get_seq

    url = normalize_url(url)

    prefix = get_board_prefix(url)
    seq = get_seq(url)

    file_name = f"{prefix}_{seq}.txt"
    file_path = os.path.join(save_dir, file_name)

    time.sleep(1.5)

    response = requests.get(url, headers=HEADERS, timeout=15)
    response.encoding = 'utf-8'

    soup = BeautifulSoup(response.text, 'html.parser')

    title_tag = soup.select_one('.view_header h4, .board_view_title, .title, .view_title')
    title = title_tag.get_text(strip=True) if title_tag else "제목 없음"

    date = "날짜 없음"
    date_match = re.search(r'\d{4}-\d{2}-\d{2}', response.text)
    if date_match:
        date = date_match.group()

    # -----------------------------------------------------
    # 제목이 기존 manifest와 같으면 본문/이미지 검사 생략
    # -----------------------------------------------------
    manifest = load_manifest()

    if should_skip_by_title(manifest, url, title):
        print(f"[제목 변경 없음] 본문 검사 생략: {file_name}")
        return True

    # -----------------------------------------------------
    # 첨부파일 추출
    # -----------------------------------------------------
    att_tags = []

    for selector in ['.view_file', '.board_view_file', '.file_list', '.fileDown', 'td.file', '.attach']:
        elements = soup.select(f"{selector} a[href]")
        if elements:
            att_tags.extend(elements)

    for a in soup.find_all('a', href=True):
        href_lower = a['href'].lower()

        if '/upfile/' in href_lower or 'download' in href_lower or 'filedown' in href_lower:
            if a not in att_tags:
                att_tags.append(a)

    attachments = []
    seen_links = set()

    for a in att_tags:
        a_link = a.get('href')

        if a_link and not a_link.startswith(('javascript:', 'mailto:', '#')):
            clean_link = clean_url(url, a_link)

            if "mode=read" in clean_link or "page=" in clean_link or clean_link == clean_url(url, ""):
                continue

            if clean_link not in seen_links:
                seen_links.add(clean_link)

                a_name = a.get_text(strip=True)

                if not a_name:
                    a_name = a.get('title', '').strip()

                if not a_name:
                    img = a.find('img')
                    if img:
                        a_name = img.get('alt', '').strip()

                if not a_name:
                    a_name = os.path.basename(a_link.split('?')[0])

                if not a_name:
                    a_name = "첨부파일(이름없음)"

                attachments.append(f"- {a_name} ({clean_link})")

    # -----------------------------------------------------
    # 본문 추출
    # -----------------------------------------------------
    content_area = soup.select_one('.view_conts, .view_con, .board_view_content, td.content, .view_content')
    image_links = []
    image_hashes = []

    if content_area:
        for tag in content_area.find_all(['script', 'style', 'iframe']):
            tag.decompose()

        tables = content_area.find_all('table')

        for table in tables:
            if table.parent:
                md_table = convert_table_to_markdown(table)
                table.replace_with(md_table)

        imgs = content_area.find_all('img')

        for img in imgs:
            if img.parent:
                img_src = img.get('src')

                if img_src:
                    img_url = clean_url(url, img_src)
                    image_links.append(img_url)

                    log_url = img_url[:50] + "..." if img_url.startswith('data') else img_url
                    print(f"이미지 분석 중... ({log_url})")

                    gemini_text, image_hash = analyze_image_with_gemini(img_url)

                    if image_hash:
                        image_hashes.append({
                            "url": "data:image/...base64 생략" if img_url.startswith("data:image") else img_url,
                            "hash": image_hash
                        })

                    replacement_text = (
                        f"\n\n--- [이미지 구간 시작] ---\n"
                        f"[이미지 해시: {image_hash if image_hash else '해시 없음'}]\n"
                        f"{gemini_text}\n"
                        f"--- [이미지 구간 끝] ---\n\n"
                    )

                    img.replace_with(replacement_text)

                else:
                    img.decompose()

        raw_body = content_area.get_text(separator='\n', strip=True)
        raw_body = re.sub(r'\n{3,}', '\n\n', raw_body)

    else:
        raw_body = "본문 내용을 찾을 수 없습니다."

    # -----------------------------------------------------
    # 최종 텍스트 조립
    # -----------------------------------------------------
    final_text = f"출처: {url}\n제목: {title}\n작성일자: {date}\n\n[첨부파일]\n"
    final_text += "\n".join(attachments) if attachments else "첨부파일 없음\n"

    final_text += "\n\n[본문 내용]\n"
    final_text += raw_body

    final_text += "\n\n[포함된 이미지 링크]\n"

    if image_links:
        final_text += "\n".join([
            link if not link.startswith('data') else "(긴 데이터 URL 생략)"
            for link in image_links
        ])
    else:
        final_text += "이미지 없음\n"

    final_text += "\n\n[이미지 해시]\n"

    if image_hashes:
        for item in image_hashes:
            final_text += f"{item['url']} | {item['hash']}\n"
    else:
        final_text += "이미지 해시 없음\n"

    # -----------------------------------------------------
    # 본문 해시 비교 후 저장 여부 판단
    # -----------------------------------------------------
    should_save, _, manifest_status, manifest_key = should_save_by_manifest(
        manifest,
        url,
        raw_body
    )

    if not should_save:
        print(f"[변경 없음] 스킵: {file_name}")
        return True

    os.makedirs(save_dir, exist_ok=True)

    with open(file_path, 'w', encoding='utf-8', newline="\n") as f:
        f.write(final_text)

    update_manifest(
        manifest,
        url,
        title,
        date,
        file_path,
        raw_body,
        manifest_status,
        manifest_key=manifest_key
    )

    save_manifest(manifest)

    if manifest_status == "new":
        print(f"[새 글 저장 완료] {file_name}")
    elif manifest_status == "modified":
        print(f"[수정 글 갱신 완료] {file_name}")

    return True