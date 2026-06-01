import os
import re
import glob
import pickle
import hashlib
import time

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.retrievers import BM25Retriever


start_time = time.time()

# ----------------------------
# 경로 / 설정
# -----------------------------
TEXT_FILES_PATH = "refined_data"
DB_FAISS_PATH = "faiss_index"
DB_BM25_PATH = "bm25_retriever.pkl"

# 파일 내용 해시 저장용
# 구조:
# {
#   "refined_data/xxx.txt": "sha256..."
# }
INDEXED_FILES_LOG = "indexed_files.pkl"

# FAISS에 들어간 파일별 chunk id 저장용
# 구조:
# {
#   "refined_data/xxx.txt": {
#       "file_hash": "sha256...",
#       "chunk_ids": ["refined_data/xxx.txt::chunk::0", ...],
#       "chunk_count": 3,
#       "updated_at": "..."
#   }
# }
EMBEDDING_MANIFEST_LOG = "embedding_manifest.pkl"

# 최신 한국어 임베딩
EMBEDDING_MODEL = "BAAI/bge-m3"

# -----------------------------
# 정규식
# -----------------------------
source_regex = re.compile(r"^출처:\s*(.+)", re.MULTILINE)
title_regex = re.compile(r"^제목:\s*(.+)", re.MULTILINE)
date_regex = re.compile(r"^작성일자:\s*(.+)", re.MULTILINE)


def normalize_text(text):
    # 줄바꿈 → 공백
    text = text.replace("\n", " ")

    # 다중 공백 제거
    text = re.sub(r"\s+", " ", text)

    # 마침표 정리
    text = re.sub(r"\s*\.\s*", ". ", text)

    # 괄호 정리
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)

    return text.strip()


def make_file_hash(file_path):
    h = hashlib.sha256()

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def normalize_file_path(file_path):
    return file_path.replace("\\", "/")


def make_chunk_ids(file_path, split_docs):
    safe_file_path = normalize_file_path(file_path)
    return [f"{safe_file_path}::chunk::{i}" for i in range(len(split_docs))]


def load_pickle_dict(path):
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "rb") as f:
            data = pickle.load(f)

        if isinstance(data, dict):
            return data

        # 예전 indexed_files.pkl은 set일 수 있음
        if isinstance(data, set):
            print(f"[알림] {path} 파일이 구버전 set 형식입니다.")
            print("[알림] 파일 해시 정보가 없으므로 현재 파일들을 새로 판단합니다.")
            return {}

        return {}

    except Exception as e:
        print(f"[경고] {path} 읽기 실패: {e}")
        return {}


def save_pickle(path, data):
    with open(path, "wb") as f:
        pickle.dump(data, f)


def get_all_txt_files():
    all_txt_files = glob.glob(os.path.join(TEXT_FILES_PATH, "**", "*.txt"), recursive=True)
    EXCLUDE_KEYWORDS = ["completed", "error_log", "list"]

    all_txt_files = [
        f for f in all_txt_files
        if not any(keyword in os.path.basename(f) for keyword in EXCLUDE_KEYWORDS)
    ]

    return sorted(all_txt_files)


def get_current_file_hashes(all_txt_files):
    current_hashes = {}

    for file_path in all_txt_files:
        try:
            current_hashes[file_path] = make_file_hash(file_path)
        except Exception as e:
            print(f"[해시 생성 실패] {file_path}: {e}")

    return current_hashes


def analyze_file_changes(current_hashes, indexed_hashes):
    current_files = set(current_hashes.keys())
    indexed_files = set(indexed_hashes.keys())

    new_files = sorted(current_files - indexed_files)
    deleted_files = sorted(indexed_files - current_files)

    modified_files = []
    same_files = []

    for file_path in sorted(current_files & indexed_files):
        if current_hashes[file_path] != indexed_hashes[file_path]:
            modified_files.append(file_path)
        else:
            same_files.append(file_path)

    return new_files, modified_files, deleted_files, same_files


def extract_section(content, start_marker, end_markers=None):
    """
    start_marker 다음부터 end_markers 중 하나가 나오기 전까지의 내용을 추출
    """
    if start_marker not in content:
        return ""

    start_idx = content.find(start_marker) + len(start_marker)
    remain = content[start_idx:]

    end_idx = len(remain)
    if end_markers:
        for marker in end_markers:
            pos = remain.find(marker)
            if pos != -1 and pos < end_idx:
                end_idx = pos

    return remain[:end_idx].strip()


def parse_attachments(attachment_text):
    """
    [첨부파일] 구간 파싱 - 남겨진 빈 괄호 () 문제 해결
    """
    attachments = []
    if not attachment_text:
        return ""

    for line in attachment_text.splitlines():
        line = line.strip()
        if not line or line == "첨부파일 없음":
            continue

        # 1. 이미 '파일명|URL' 형식이면 그대로 사용
        if "|" in line:
            attachments.append(line)
            continue

        # 2. '파일명 (URL)' 형태에서 추출
        url_match = re.search(r"(https?://[^\s)]+)", line)
        if url_match:
            url = url_match.group(1).strip()

            # URL을 지우고 남은 텍스트
            name = line.replace(url, "")

            # 빈 괄호와 앞뒤 불필요 기호 제거
            name = re.sub(r'\s*\(\s*\)', '', name)
            name = name.strip(" -•\t\n\r")

            if name:
                attachments.append(f"{name}|{url}")
            else:
                attachments.append(url)
        else:
            clean_line = re.sub(r'\s*\(\s*\)', '', line).strip(" -•\t")
            if clean_line:
                attachments.append(clean_line)

    return ";".join(attachments)


def parse_image_urls(image_link_text):
    """
    [포함된 이미지 링크] 구간에서 URL만 추출
    여러 줄이면 ; 로 연결
    """
    image_urls = []

    if not image_link_text:
        return ""

    for line in image_link_text.splitlines():
        line = line.strip()
        if not line:
            continue

        urls = re.findall(r"https?://[^\s]+", line)
        image_urls.extend(urls)

    return ";".join(image_urls)


def load_documents_from_files(file_list):
    """파일 리스트로부터 Document 객체 생성"""
    documents = []

    for file_path in file_list:
        file_name = os.path.basename(file_path)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
        except Exception as e:
            print(f"[읽기 실패] {file_path}: {e}")
            continue

        if not content:
            continue

        # -----------------------------
        # 1. 기본 메타데이터 추출
        # -----------------------------
        source_match = source_regex.search(content)
        title_match = title_regex.search(content)
        date_match = date_regex.search(content)

        source_url = source_match.group(1).strip() if source_match else ""
        title = title_match.group(1).strip() if title_match else "제목 없음"
        written_date = date_match.group(1).strip() if date_match else ""

        # -----------------------------
        # 2. 섹션별 추출
        # -----------------------------
        attachment_text = extract_section(
            content,
            "[첨부파일]",
            end_markers=["[본문 내용]", "--- [이미지 구간 시작] ---", "[포함된 이미지 링크]"]
        )

        body_text = extract_section(
            content,
            "[본문 내용]",
            end_markers=["--- [이미지 구간 시작] ---", "[포함된 이미지 링크]"]
        )

        image_ocr_text = extract_section(
            content,
            "--- [이미지 구간 시작] ---",
            end_markers=["--- [이미지 구간 끝] ---"]
        )

        image_link_text = extract_section(
            content,
            "[포함된 이미지 링크]",
            end_markers=None
        )

        # -----------------------------
        # 3. 본문 구성
        # -----------------------------
        full_body_parts = []

        if body_text:
            full_body_parts.append(body_text)

        if image_ocr_text:
            full_body_parts.append("[이미지 추출 텍스트]")
            full_body_parts.append(image_ocr_text)

        page_content = "\n\n".join(full_body_parts).strip()
        page_content = normalize_text(page_content)

        if not page_content:
            continue

        # -----------------------------
        # 4. 메타데이터 구성
        # -----------------------------
        attachment_str = parse_attachments(attachment_text)
        image_url_str = parse_image_urls(image_link_text)

        metadata = {
            "doc_id": normalize_file_path(file_path),
            "title": title,
            "source_file": file_name,
            "file_path": normalize_file_path(file_path),
            "raw_content": page_content
        }

        if source_url:
            metadata["source"] = source_url

        if written_date:
            metadata["date"] = written_date

        if attachment_str:
            metadata["attachments"] = attachment_str

        if image_url_str:
            metadata["image_urls"] = image_url_str

        documents.append(
            Document(
                page_content=f"{title}\n{page_content}",
                metadata=metadata
            )
        )

    return documents


def load_faiss_db(embeddings):
    if os.path.exists(DB_FAISS_PATH):
        try:
            return FAISS.load_local(DB_FAISS_PATH, embeddings, allow_dangerous_deserialization=True)
        except Exception as e:
            print(f"[FAISS 로드 실패] 새로 생성합니다: {e}")
            return None
    return None


def delete_chunk_ids_from_faiss(db, chunk_ids):
    if db is None or not chunk_ids:
        return

    try:
        db.delete(ids=chunk_ids)
    except Exception as e:
        print(f"[FAISS chunk 삭제 실패] {e}")


def upsert_files_to_faiss(file_list, current_hashes, embeddings, text_splitter, embedding_manifest):
    """
    새 파일 또는 수정 파일만 FAISS에 반영.
    기존 chunk가 있으면 해당 chunk만 삭제하고 다시 추가.
    """
    if not file_list:
        return

    db = load_faiss_db(embeddings)

    for i, file_path in enumerate(file_list):
        try:
            old_info = embedding_manifest.get(file_path, {})
            old_chunk_ids = old_info.get("chunk_ids", [])

            if db is not None and old_chunk_ids:
                delete_chunk_ids_from_faiss(db, old_chunk_ids)
                print(f"[기존 chunk 삭제] {file_path} / {len(old_chunk_ids)}개")

            single_doc = load_documents_from_files([file_path])
            if not single_doc:
                embedding_manifest.pop(file_path, None)
                continue

            split_docs = text_splitter.split_documents(single_doc)
            if not split_docs:
                embedding_manifest.pop(file_path, None)
                continue

            chunk_ids = make_chunk_ids(file_path, split_docs)

            if db is None:
                db = FAISS.from_documents(split_docs, embeddings, ids=chunk_ids)
            else:
                db.add_documents(split_docs, ids=chunk_ids)

            embedding_manifest[file_path] = {
                "file_hash": current_hashes.get(file_path, ""),
                "chunk_ids": chunk_ids,
                "chunk_count": len(chunk_ids),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }

            print(f"[부분 임베딩 완료] {file_path} / chunk {len(chunk_ids)}개")

            if (i + 1) % 5 == 0 or (i + 1) == len(file_list):
                if db is not None:
                    db.save_local(DB_FAISS_PATH)
                save_pickle(EMBEDDING_MANIFEST_LOG, embedding_manifest)
                print(f"[중간 저장] {i + 1}/{len(file_list)}개 처리 완료")

        except Exception as e:
            print(f"[부분 임베딩 실패] {file_path}: {e}")
            continue

    if db is not None:
        db.save_local(DB_FAISS_PATH)

    save_pickle(EMBEDDING_MANIFEST_LOG, embedding_manifest)


def delete_files_from_faiss(deleted_files, embeddings, embedding_manifest):
    """
    삭제된 txt 파일에 해당하는 기존 FAISS chunk만 삭제.
    """
    if not deleted_files:
        return

    db = load_faiss_db(embeddings)
    if db is None:
        return

    for file_path in deleted_files:
        old_info = embedding_manifest.get(file_path, {})
        old_chunk_ids = old_info.get("chunk_ids", [])

        if old_chunk_ids:
            delete_chunk_ids_from_faiss(db, old_chunk_ids)
            print(f"[삭제 파일 chunk 제거] {file_path} / {len(old_chunk_ids)}개")

        embedding_manifest.pop(file_path, None)

    db.save_local(DB_FAISS_PATH)
    save_pickle(EMBEDDING_MANIFEST_LOG, embedding_manifest)


def rebuild_bm25(all_txt_files, text_splitter):
    """
    BM25는 전체 문서 기준으로 재생성.
    텍스트 기반이라 FAISS보다 훨씬 빠름.
    """
    print("BM25 인덱스 전체 업데이트 중...")

    all_documents = load_documents_from_files(all_txt_files)
    if not all_documents:
        print("[BM25] 문서가 없습니다.")
        return

    all_split_docs = text_splitter.split_documents(all_documents)
    if not all_split_docs:
        print("[BM25] split_docs가 비어 있습니다.")
        return

    bm25_retriever = BM25Retriever.from_documents(all_split_docs)

    with open(DB_BM25_PATH, "wb") as f:
        pickle.dump(bm25_retriever, f)

    print(f"BM25 인덱스 저장 완료: '{DB_BM25_PATH}'")


def create_vector_db():
    all_txt_files = get_all_txt_files()

    if not all_txt_files:
        print("txt 파일이 없습니다. 작업을 종료합니다.")
        return

    indexed_hashes = load_pickle_dict(INDEXED_FILES_LOG)
    embedding_manifest = load_pickle_dict(EMBEDDING_MANIFEST_LOG)

    current_hashes = get_current_file_hashes(all_txt_files)

    new_files, modified_files, deleted_files, same_files = analyze_file_changes(
        current_hashes,
        indexed_hashes
    )

    print(f"전체 txt 파일: {len(all_txt_files)}개")
    print(f"새 파일: {len(new_files)}개")
    print(f"수정 파일: {len(modified_files)}개")
    print(f"삭제 파일: {len(deleted_files)}개")
    print(f"변경 없음: {len(same_files)}개")

    if not new_files and not modified_files and not deleted_files:
        print("변경된 파일이 없습니다. FAISS/BM25 업데이트를 종료합니다.")
        return

    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)

    if deleted_files:
        print("\n[삭제 파일 목록]")
        for file_path in deleted_files:
            print(f" - {file_path}")

    if new_files:
        print("\n[새 파일 목록]")
        for file_path in new_files:
            print(f" - {file_path}")

    if modified_files:
        print("\n[수정 파일 목록]")
        for file_path in modified_files:
            print(f" - {file_path}")

    # 삭제된 파일은 기존 chunk만 삭제
    delete_files_from_faiss(deleted_files, embeddings, embedding_manifest)

    # 새 파일 + 수정 파일만 부분 임베딩
    files_to_upsert = new_files + modified_files
    upsert_files_to_faiss(files_to_upsert, current_hashes, embeddings, text_splitter, embedding_manifest)

    # BM25는 전체 재생성
    rebuild_bm25(all_txt_files, text_splitter)

    # 현재 파일 해시 저장
    save_pickle(INDEXED_FILES_LOG, current_hashes)

    print(f"\nVector DB 부분 업데이트 완료: '{DB_FAISS_PATH}'")
    print(f"파일 해시 로그 저장 완료: '{INDEXED_FILES_LOG}'")
    print(f"임베딩 manifest 저장 완료: '{EMBEDDING_MANIFEST_LOG}'")


if __name__ == "__main__":
    create_vector_db()
    end_time = time.time()
    print(f"소요 시간: {end_time - start_time:.4f}초")