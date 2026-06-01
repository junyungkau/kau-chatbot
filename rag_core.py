# rag_core.py 
import os
import re
import pickle
from google import genai

from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.retrievers import BM25Retriever

# 1. 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FAISS_PATH = os.path.join(BASE_DIR, "faiss_index")
DB_BM25_PATH = os.path.join(BASE_DIR, "bm25_retriever.pkl")
EMBEDDING_MODEL = "BAAI/bge-m3"

# 2. API 키 설정
if "GOOGLE_API_KEY" in os.environ:
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
else:
    client = None


# 3. DB 로더
def load_resources():
    print("Loading Vector DB & BM25.")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    vector_db = None
    bm25_retriever = None

    if os.path.exists(os.path.join(DB_FAISS_PATH, "index.faiss")):
        vector_db = FAISS.load_local(
            DB_FAISS_PATH,
            embeddings,
            allow_dangerous_deserialization=True
        )

    if os.path.exists(DB_BM25_PATH):
        with open(DB_BM25_PATH, "rb") as f:
            bm25_retriever = pickle.load(f)
            bm25_retriever.k = 50

    return vector_db, bm25_retriever


vector_db, bm25_retriever = load_resources()

if vector_db:
    faiss_retriever = vector_db.as_retriever(search_kwargs={"k": 50})
else:
    faiss_retriever = None

# 4. 앙상블 검색기
class EnsembleRetriever:
    def __init__(self, retrievers, weights=None, k=10, c=60):
        self.retrievers = retrievers
        self.weights = weights or [1.0] * len(retrievers)
        self.k = k
        self.c = c

    def invoke(self, query):
        rrf_scores = {}
        seen_docs = {}
        date_bonus_map = {}

        def get_strong_date_multiplier(doc):
            date_str = doc.metadata.get("date")
            if not date_str or not isinstance(date_str, str) or "-" not in date_str:
                return 0.85 # 날짜 없으면 큰 패널티
            
            try:
                parts = date_str.split("-")
                # 현재 시점: 2026년 4월 (316)
                current_month_val = 316 
                doc_month_val = (int(parts[0]) - 2000) * 12 + int(parts[1])
                diff = max(0, current_month_val - doc_month_val)
                
                # [조정] 감쇄율을 0.90으로 강화 (한 달 차이도 크게 벌어짐)
                return 0.99 ** diff
            except:
                return 0.1

        for retriever, weight in zip(self.retrievers, self.weights):
            if retriever is None: continue
            try:
                docs = retriever.invoke(query)
            except:
                docs = []

            for rank, doc in enumerate(docs[:50]):
                key = (doc.metadata.get("title", ""), doc.metadata.get("date", ""), doc.metadata.get("source_file", ""))
                
                # [개선] RRF 점수에 100을 곱해 숫자를 키웁니다 (0.04 -> 4.0)
                score = weight * (100.0 / (self.c + (rank + 1)))

                if key not in rrf_scores:
                    rrf_scores[key] = score
                    seen_docs[key] = doc
                    date_bonus_map[key] = get_strong_date_multiplier(doc)
                else:
                    rrf_scores[key] += 1*score

        for key in rrf_scores:
            final_rrf = rrf_scores[key]
            # [수정] 변수명을 사용자님 코드에 맞춰 'date_bonus'로 변경
            multiplier = date_bonus_map.get(key, 0.1)
            
            total_score = final_rrf * multiplier
            
            seen_docs[key].metadata["similarity_score"] = final_rrf
            seen_docs[key].metadata["date_bonus"] = multiplier # 이제 로그에 정상 출력됩니다
            seen_docs[key].metadata["total_score"] = total_score

        sorted_keys = sorted(rrf_scores.keys(), key=lambda k: -seen_docs[k].metadata["total_score"])
        return [seen_docs[key] for key in sorted_keys[:self.k]]

ensemble = None
if vector_db and bm25_retriever:
    ensemble = EnsembleRetriever(
        retrievers=[bm25_retriever, faiss_retriever],
        weights=[0.3, 0.7],
        k=10, 
    )


def _collect_reference_links(source_matches, unique_docs):
    links = []
    seen_urls = set()
    used_indexes = set()

    if not source_matches:
        return links

    for match in source_matches:
        for idx in match.replace(" ", "").split(","):
            if not idx.isdigit():
                continue

            num = int(idx)
            if num in used_indexes:
                continue
            if not (0 <= num - 1 < len(unique_docs)):
                continue

            used_indexes.add(num)
            doc = unique_docs[num - 1]

            title = doc.metadata.get("title", "제목 없음")
            date = doc.metadata.get("date", "")
            url = doc.metadata.get("source", "")

            display_name = f"{title} ({date})" if date else title

            if url and isinstance(url, str) and url.startswith("http"):
                if url not in seen_urls:
                    seen_urls.add(url)
                    links.append({
                        "title": display_name,
                        "url": url
                    })
    return links


# 5. 핵심 질문 처리 함수
def get_ai_response(user_input, history=None):
    if not ensemble:
        return {"answer": "죄송합니다. 데이터베이스가 로드되지 않았습니다.", "links": []}

    if client is None:
        return {"answer": "죄송합니다. GOOGLE_API_KEY가 설정되지 않았습니다.", "links": []}

    # [수정] 질문 재구성 로직 및 이전 대화 참조 삭제 -> 사용자 입력을 그대로 검색어로 사용
    search_query = user_input

    # 원본 검색 수행
    docs = ensemble.invoke(search_query)

    final_seen = set()
    unique_docs = []
    for d in docs:
        key = f"{d.metadata.get('title','')}_{d.metadata.get('date','')}_{d.metadata.get('source_file','')}"
        if key not in final_seen:
            final_seen.add(key)
            unique_docs.append(d)

    # 상세 로그 출력 (유사도 및 날짜 점수 포함)
    print("\n" + "="*30)
    print(f"검색어: {search_query}")
    print("===== 검색된 문서 (점수 포함) =====")
    for i, d in enumerate(unique_docs):
        sim = d.metadata.get('similarity_score', 0)
        date_b = d.metadata.get('date_bonus', 0)
        total = d.metadata.get('total_score', 0)
        print(f"[{i+1}] 제목: {d.metadata.get('title')}") 
        print(f"{d.metadata.get('date')}")
        print(f"유사도: {sim:.2f} | 날짜가중치: {date_b:.2f} | 합계: {total:.2f}\n")
    print("="*30 + "\n")

    context = ""
    for i, d in enumerate(unique_docs):
        context += f"--- 문서 {i+1} ---\n"
        context += f"제목: {d.metadata.get('title', '제목 없음')}\n"
        if d.metadata.get("date"):
            context += f"작성일자: {d.metadata.get('date')}\n"
        if d.metadata.get("source"):
            context += f"출처: {d.metadata.get('source')}\n"
        context += d.metadata.get("raw_content", d.page_content) + "\n\n"

    # 원본 시스템 메시지
    system_message = """
    항공대 안내 봇 규칙:
    1. 형식 엄수: 반드시 '제목: ', '답변: '으로 시작할 것, 사이사이에 줄바꿈을 넣을 것.
    2. 말투: 문장 형식 금지.
    3. 근거: 답변 끝에 반드시 [근거: 번호] 포함.
    4. 분량: 200자 이내로 짧고 간결하게 답변할 것.
    

    예시:
    제목: 휴학 신청 방법 안내
    답변: 
    1. 기간
    5월 10일 ~ 5월 20일

    2. 방법
    종합정보시스템 인터넷 접수 또는 교무팀 방문. [근거: 1]
    """

    # [수정] history_context 생성 및 참조 부분 삭제
    final_prompt = f"\n\n[Context]\n{context}\n\n[질문]\n{user_input}\n{system_message}\n\n[답변]"

    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=final_prompt
        )
        full_text = response.text
        
        title_match = re.search(r"제목:\s*(.*)", full_text)
        answer_match = re.search(r"답변:\s*([\s\S]*)", full_text)
        
        gen_title = title_match.group(1).strip() if title_match else "안내 결과"
        gen_answer = answer_match.group(1).strip() if answer_match else full_text
        
    except Exception as e:
        return {"answer": f"AI 응답 생성 중 오류가 발생했습니다: {e}", "links": []}

    source_matches = re.findall(r"\[근거:\s*([\d,\s]+)\]", gen_answer)
    final_content = re.sub(r"\[근거:[^\]]*\]", "", gen_answer).strip()

    reference_links = _collect_reference_links(source_matches, unique_docs)

    return {
        "title": gen_title,
        "answer": final_content,
        "links": reference_links
    }