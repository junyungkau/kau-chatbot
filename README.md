# KAU Kakao Chatbot

한국항공대학교 공지/게시판 데이터를 검색해 카카오톡 챗봇 스킬 서버로 응답하는 RAG 기반 챗봇입니다.

## 권장 배포 방식

이 프로젝트는 FAISS, HuggingFace 임베딩 모델, BM25 인덱스를 함께 사용하므로 Docker 이미지가 커질 수 있습니다.
Cloudtype 무료/소형 컨테이너보다 AWS EC2 또는 Google Cloud VM에서 Python 서버를 직접 실행하는 방식을 권장합니다.

VM 배포 방법은 `README_VM.md`를 참고하세요.

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
