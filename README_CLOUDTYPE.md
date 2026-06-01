# KAU Kakao Bot Cloudtype 배포 메모

## Cloudtype 설정

1. 이 폴더를 GitHub 저장소로 올립니다.
2. Cloudtype에서 새 프로젝트를 만들고 해당 저장소를 연결합니다.
3. 빌드 방식은 `Dockerfile`을 사용합니다.
4. 환경 변수에 `GOOGLE_API_KEY`를 추가합니다.
5. 배포가 끝나면 카카오 i 오픈빌더 스킬 서버 URL에 아래 주소를 넣습니다.

```text
https://배포된도메인/kakao
```

## 엔드포인트

- `GET /`: Cloudtype 헬스 체크용
- `GET /health`: RAG 로딩 상태 확인용
- `POST /kakao`: 카카오톡 챗봇 스킬 웹훅
- `POST /message`, `POST /webhook`: 같은 기능의 예비 경로

## 주의할 점

처음 배포 후에는 임베딩 모델과 FAISS/BM25 인덱스를 로딩하는 데 시간이 걸릴 수 있습니다.
로딩 중 질문이 들어오면 "검색 데이터를 불러오는 중입니다"라는 응답이 나갑니다.
