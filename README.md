# KAU Kakao Chatbot

한국항공대학교 공지/게시판 데이터를 검색해 카카오톡 챗봇 스킬 서버로 응답하는 RAG 기반 챗봇입니다.

## Cloudtype 배포

Cloudtype에서는 Dockerfile 기반으로 배포합니다.

필수 환경 변수:

```text
GOOGLE_API_KEY=Gemini API 키
```

카카오 i 오픈빌더 스킬 서버 URL:

```text
https://배포된-cloudtype-도메인/kakao
```

## 주요 파일

- `app.py`: Flask 기반 카카오톡 스킬 웹훅 서버
- `rag_core.py`: FAISS/BM25 검색 및 Gemini 응답 생성
- `faiss_index/`: FAISS 벡터 인덱스
- `bm25_retriever.pkl`: BM25 검색 인덱스
- `Dockerfile`: Cloudtype 배포 설정

자세한 배포 메모는 `README_CLOUDTYPE.md`를 참고하세요.
