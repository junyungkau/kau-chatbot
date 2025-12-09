import os
import re
import glob
import pickle

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.retrievers import BM25Retriever

# -----------------------------
# 경로 / 설정
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEXT_FILES_PATH = os.path.join(BASE_DIR, "cleaned_texts") # 절대 경로로 변환
DB_FAISS_PATH = os.path.join(BASE_DIR, "faiss_index")
DB_BM25_PATH = os.path.join(BASE_DIR, "bm25_retriever.pkl")

# 최신 한국어 임베딩
EMBEDDING_MODEL = "jhgan/ko-sroberta-multitask"

# -----------------------------
# 정규식
# -----------------------------
url_regex = re.compile(r"^출처 URL: (https?://[^\s]+)")
title_regex = re.compile(r"^제목: (.+)")
image_url_regex = re.compile(r"^이미지 URL: (.+)")
attachment_regex = re.compile(r"^첨부파일: (.+)")
separator = "=" * 40


def create_vector_db():
    txt_files = glob.glob(os.path.join(TEXT_FILES_PATH, "*.txt"))
    if not txt_files:
        print(f"'{TEXT_FILES_PATH}' 폴더에 .txt 파일이 없습니다.")
        return

    documents = []

    for file_path in txt_files:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 메타데이터 / 본문 분리
        parts = content.split(separator, 1)
        if len(parts) > 1:
            metadata_part, page_content = parts[0], parts[1].strip()
        else:
            metadata_part, page_content = "", content

        if not page_content:
            continue

        # -----------------------------
        # 메타데이터 파싱
        # -----------------------------
        source_url, title, image_url_str, attachment_str = "출처 없음", "제목 없음", "", ""

        for line in metadata_part.split("\n"):
            if url_match := url_regex.search(line):
                source_url = url_match.group(1)
            if title_match := title_regex.search(line):
                title = title_match.group(1)
            if image_match := image_url_regex.search(line):
                image_url_str = image_match.group(1)
            if attachment_match := attachment_regex.search(line):
                attachment_str = attachment_match.group(1)

        metadata = {
            "source": source_url,
            "title": title,
            "raw_content": page_content,  # LLM에 보여줄 원본 텍스트
        }
        if image_url_str:
            metadata["image_urls"] = image_url_str
        if attachment_str:
            metadata["attachments"] = attachment_str

        # 검색 정확도 향상을 위해 제목을 본문 앞에 붙여둠
        doc_text = f"{title}\n{page_content}"

        documents.append(
            Document(
                page_content=doc_text,
                metadata=metadata,
            )
        )

    # -----------------------------
    # 텍스트 분할 (chunk_size=350, overlap=100)
    # -----------------------------
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=350,
        chunk_overlap=100,
    )

    print(f"총 {len(documents)}개의 문서를 로드했습니다. 텍스트 분할을 시작합니다...")
    split_docs = text_splitter.split_documents(documents)
    print(f"총 {len(split_docs)}개의 텍스트 조각(chunk)을 생성했습니다.")

    # -----------------------------
    # FAISS 생성 (bge-m3)
    # -----------------------------
    print("FAISS 인덱스를 생성합니다...(bge-m3)")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    db = FAISS.from_documents(split_docs, embeddings)
    db.save_local(DB_FAISS_PATH)
    print(f"Vector DB가 '{DB_FAISS_PATH}'에 저장되었습니다.")

    # -----------------------------
    # BM25 생성 (원본 chunk 기반)
    # -----------------------------
    print("BM25 인덱스를 생성합니다...")
    bm25_retriever = BM25Retriever.from_documents(split_docs)

    with open(DB_BM25_PATH, "wb") as f:
        pickle.dump(bm25_retriever, f)

    print(f"BM25 인덱스가 '{DB_BM25_PATH}'에 저장되었습니다.")


if __name__ == "__main__":
    create_vector_db()

