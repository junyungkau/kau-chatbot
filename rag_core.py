# rag_core.py (AI 두뇌 전용 파일)
import os
import re
import pickle
import google.generativeai as genai

from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.retrievers import BM25Retriever

# 1. 경로 설정 (상대 경로 적용!)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FAISS_PATH = os.path.join(BASE_DIR, "faiss_index")
DB_BM25_PATH = os.path.join(BASE_DIR, "bm25_retriever.pkl")
EMBEDDING_MODEL = "jhgan/ko-sroberta-multitask"

# 2. API 키 설정 (환경변수에서 가져오거나 직접 입력)
# Render 배포 시에는 Environment Variable 설정을 권장합니다.
if "GOOGLE_API_KEY" in os.environ:
    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
else:
    # 테스트용 (조원 키가 있다면 여기에 잠시 넣어서 테스트)
    # genai.configure(api_key="여기에_API_KEY_입력")
    pass

# 3. DB 로더 (전역 변수로 한 번만 로딩)
def load_resources():
    print("Loading Vector DB & BM25...")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    
    vector_db = None
    bm25_retriever = None

    if os.path.exists(DB_FAISS_PATH):
        vector_db = FAISS.load_local(DB_FAISS_PATH, embeddings, allow_dangerous_deserialization=True)
    
    if os.path.exists(DB_BM25_PATH):
        with open(DB_BM25_PATH, "rb") as f:
            bm25_retriever = pickle.load(f)
            bm25_retriever.k = 10
            
    return vector_db, bm25_retriever

# 리소스 로딩 (서버 시작할 때 1번 실행됨)
vector_db, bm25_retriever = load_resources()

if vector_db:
    faiss_retriever = vector_db.as_retriever(search_kwargs={"k": 10})
else:
    faiss_retriever = None


# 4. 앙상블 검색기 클래스
class EnsembleRetriever:
    def __init__(self, retrievers, weights=None, k=3):
        self.retrievers = retrievers
        self.weights = weights or [1.0] * len(retrievers)
        self.k = k

    def invoke(self, query):
        scored = {}
        seen_docs = {}

        for retriever, weight in zip(self.retrievers, self.weights):
            if retriever is None: continue
            try:
                # invoke 또는 get_relevant_documents 사용
                if hasattr(retriever, "invoke"):
                    docs = retriever.invoke(query)
                else:
                    docs = retriever.get_relevant_documents(query)
            except:
                docs = []
            
            docs = docs[:10]
            for rank, doc in enumerate(docs):
                # 키 생성 (내용 + 메타데이터)
                key = (doc.page_content, tuple(sorted(doc.metadata.items())))
                score = weight * (10 - rank)
                if key not in scored:
                    scored[key] = score
                    seen_docs[key] = doc
                else:
                    scored[key] += score
        
        sorted_keys = sorted(scored.keys(), key=lambda k: -scored[k])
        return [seen_docs[key] for key in sorted_keys[: self.k]]

# 앙상블 검색기 초기화
ensemble = None
if vector_db and bm25_retriever:
    ensemble = EnsembleRetriever(
        retrievers=[bm25_retriever, faiss_retriever],
        weights=[0.3, 0.7],
        k=5,
    )

# 5. 핵심 질문 처리 함수 (Dash에서 호출할 함수!)
def get_ai_response(user_input):
    if not ensemble:
        return "죄송합니다. 데이터베이스가 로드되지 않았습니다."

    # (1) 검색
    docs = ensemble.invoke(user_input)
    
    # 중복 제거
    final_seen = set()
    unique_docs = []
    for d in docs:
        key = f"{d.metadata.get('source','')}_{d.metadata.get('title','')}"
        if key not in final_seen:
            final_seen.add(key)
            unique_docs.append(d)

    # (2) 프롬프트 구성
    context = ""
    for i, d in enumerate(unique_docs):
        context += f"--- 문서 {i+1} ---\n"
        context += f"제목: {d.metadata.get('title')}\n"
        context += f"출처: {d.metadata.get('source')}\n"
        context += d.metadata.get("raw_content", d.page_content) + "\n\n"

    system_message = """
    당신은 한국항공대학교 안내 챗봇입니다.
    문서의 내용에 기반하여 답변하세요.
    답변 뒤에 [근거: 1, 2] 형태로 문서 번호를 붙이세요.
    """
    
    final_prompt = f"{system_message}\n\n[Context]\n{context}\n\n[질문]\n{user_input}\n\n[답변]"

    # (3) Gemini 호출
    try:
        model = genai.GenerativeModel("gemini-2.5-pro")
        response = model.generate_content(final_prompt)
        full_text = response.text
    except Exception as e:
        return f"AI 응답 생성 중 오류가 발생했습니다: {e}"

    # (4) 출처 및 근거 정리 (조원 코드 로직 이식)
    # [근거: N] 파싱 및 링크 생성 로직
    source_matches = re.findall(r"\[근거:\s*([\d,\s]+)\]", full_text)
    final_content = re.sub(r"\[근거:[^\]]*\]", "", full_text).strip()
    
    footer_items = []
    used_indexes = set()

    if source_matches:
        for match in source_matches:
            indexes = match.replace(" ", "").split(",")
            for idx in indexes:
                if idx.isdigit():
                    num = int(idx)
                    if num not in used_indexes:
                        used_indexes.add(num)
                        doc_index = num - 1
                        if 0 <= doc_index < len(unique_docs):
                            doc = unique_docs[doc_index]
                            title = doc.metadata.get("title", "제목 없음")
                            url = doc.metadata.get("source", "")
                            if url:
                                footer_items.append(f"- [{title}]({url})")

    if footer_items:
        final_content += "\n\n---\n**참고한 출처:**\n" + "\n".join(footer_items)

    return final_content
