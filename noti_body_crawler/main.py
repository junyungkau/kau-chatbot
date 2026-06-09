import os
import logging
import asyncio
import time  # [추가] 시간 측정을 위해 추가
import re    # [추가] 문장 분할을 위해 추가
from typing import Any, Dict, List

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, BackgroundTasks

logger = logging.getLogger("kakaotalk-bot")

load_dotenv()

try:
    import rag_core
except ImportError:
    logging.error("rag_core.py 파일을 찾을 수 없습니다.")
    rag_core = None

logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------
# [삭제] 사용자별 대화 세션 저장소 (기능 제거됨)
# ---------------------------------------------------------


# -----------------------------
# 공통 유틸리티
# -----------------------------
def truncate_text(text: str, max_len: int = 10000) -> str:
    if text is None:
        return ""
    text = str(text)
    return text if len(text) <= max_len else text[:max_len - 3] + "..."


def compact_lines(text: str) -> str:
    return str(text) if text else ""


def normalize_rag_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return {
            "title": str(result.get("title", "안내 결과")), # AI가 만든 제목 추가
            "answer": str(result.get("answer", "")),
            "links": result.get("links", []) if isinstance(result.get("links"), list) else []
        }
    return {
        "title": "안내 결과",
        "answer": str(result),
        "links": []
    }


# ---------------------------------------------------------
# [수정] 항목 앞 줄바꿈(\n)과 번호를 기준으로 나누는 로직
# ---------------------------------------------------------
def split_text_by_items(text: str, max_chars: int = 210) -> List[str]:
    # 항목 번호(1. 2.) 앞에 있는 줄바꿈을 기준으로 텍스트를 쪼갭니다.
    # 긍정형 전방 탐색(?=...)을 사용하여 줄바꿈(\n)은 유지하면서 그 뒤에 숫자가 올 때 자릅니다.
    parts = re.split(r'(?=\n\d{1,2}\.)', text.strip())
    
    chunks = []
    current_chunk = ""

    for part in parts:
        # 이번 항목 조각을 더했을 때 175자를 넘지 않으면 현재 카드에 계속 붙임
        if len(current_chunk) + len(part) <= max_chars:
            current_chunk += part
        else:
            # 넘어가면 지금까지 만든 덩어리를 카드로 저장
            if current_chunk:
                chunks.append(current_chunk.strip())
            
            # 항목 하나 자체가 너무 길면 줄바꿈 단위로 쪼개서 넣음
            if len(part) > max_chars:
                sub_lines = part.splitlines(keepends=True)
                temp_sub = ""
                for line in sub_lines:
                    if len(temp_sub) + len(line) <= max_chars:
                        temp_sub += line
                    else:
                        chunks.append(temp_sub.strip())
                        temp_sub = line
                current_chunk = temp_sub
            else:
                current_chunk = part
                
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks


# -----------------------------
# FastAPI 앱
# -----------------------------
app = FastAPI()


# -----------------------------
# 카카오톡 콜백 응답 처리
# -----------------------------
# [수정] rag_task 인자를 받도록 변경
async def send_kakaotalk_callback(callback_url: str, question: str, user_id: str, start_time: float, rag_task):
    try:
        rag_res = await rag_task
        norm = normalize_rag_result(rag_res)

        # AI가 지어준 제목과 본문 사용
        title_text = norm.get("title", "안내 결과")
        answer_text = compact_lines(norm.get("answer", "답변을 생성하지 못했습니다."))
        links = norm.get("links", [])

        # [삭제] 대화 내용 업데이트 로직 제거

        buttons = []
        for link in links[:3]:
            url = str(link.get("url", "")).strip()
            if url.startswith("http"):
                buttons.append({
                    "action": "webLink",
                    "label": "원본 공지 보러가기",
                    "webLinkUrl": url
                })

        # 항목 및 줄바꿈 기준 분할 실행 (안전하게 175자 기준)
        chunks = split_text_by_items(answer_text, 210)[:3]
        
        outputs = []
        for idx, chunk in enumerate(chunks):
            is_first = (idx == 0)
            is_last = (idx == len(chunks) - 1)
            
            # 모바일 UI 굵기 방지를 위해 첫 카드가 아니면 투명 문자(\u200B) 사용
            display_title = truncate_text(title_text, 40) if is_first else "\u200B"
            
            outputs.append({
                "basicCard": {
                    "title": display_title,
                    "description": chunk,
                    "buttons": buttons if is_last else []
                }
            })

        payload = {
            "version": "2.0",
            "template": {
                "outputs": outputs
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(callback_url, json=payload, timeout=10.0)

            if response.status_code == 200:
                # [추가] 답변 전송이 완료된 시점에 시간 측정 및 출력
                end_time = time.perf_counter()
                logger.info(f"콜백 답변 전송 성공 (소요 시간: {end_time - start_time:.4f}초)")
            else:
                logger.error(f"콜백 실패: {response.status_code} - {response.text}")

    except Exception as e:
        logger.error(f"Callback Error: {e}")


# -----------------------------
# 카카오톡 스킬 엔드포인트
# -----------------------------
@app.post("/kakaotalk")
async def handle_kakaotalk(request: Request, background_tasks: BackgroundTasks):
    # [추가] 사용자의 요청이 서버에 도착한 시점 기록
    start_time = time.perf_counter()
    
    try:
        body = await request.json()

        user_request = body.get("userRequest", {})
        utterance = user_request.get("utterance", "").strip()
        callback_url = user_request.get("callbackUrl")
        
        # [추가] 사용자 식별 ID 추출
        user_id = user_request.get("user", {}).get("id", "anonymous")

        # [삭제] 이전 대화 이력 가져오기 로직 제거

        # [수정] rag_core 호출 시 history 인자 제거
        rag_task = asyncio.create_task(
            asyncio.to_thread(rag_core.get_ai_response, utterance)
        )

        try:
            # 4초 동안 응답을 기다려봄
            rag_res = await asyncio.wait_for(
                asyncio.shield(rag_task),
                timeout=4
            )
            
            # 4초 이내에 답변이 나왔을 경우: 즉시 응답 생성
            norm = normalize_rag_result(rag_res)
            title_text = norm.get("title", "안내 결과")
            answer_text = compact_lines(norm.get("answer", "답변을 생성하지 못했습니다."))
            links = norm.get("links", [])

            # [삭제] 대화 이력 업데이트 로직 제거

            buttons = []
            for link in links[:3]:
                url = str(link.get("url", "")).strip()
                if url.startswith("http"):
                    buttons.append({
                        "action": "webLink",
                        "label": "원본 공지 보러가기",
                        "webLinkUrl": url
                    })

            # 항목 및 줄바꿈 기준 분할 실행
            chunks = split_text_by_items(answer_text, 210)[:3]

            # 응답 페이로드 구성 (즉시 반환)
            outputs = []
            for idx, chunk in enumerate(chunks):
                is_first = (idx == 0)
                is_last = (idx == len(chunks) - 1)
                
                display_title = truncate_text(title_text, 40) if is_first else "\u200B"
                
                outputs.append({
                    "basicCard": {
                        "title": display_title,
                        "description": chunk,
                        "buttons": buttons if is_last else []
                    }
                })

            end_time = time.perf_counter()
            logger.info(f"즉시 답변 성공 (소요 시간: {end_time - start_time:.4f}초)")
            
            return {
                "version": "2.0",
                "template": {"outputs": outputs}
            }

        except asyncio.TimeoutError:
            # 4초가 넘었을 경우: 콜백 모드로 전환
            if not callback_url:
                return {
                    "version": "2.0",
                    "template": {
                        "outputs": [{"simpleText": {"text": "답변 생성 시간이 초과되었습니다."}}]
                    }
                }

            # [수정] 이미 실행 중인 같은 작업의 결과를 콜백으로 사용
            background_tasks.add_task(send_kakaotalk_callback, callback_url, utterance, user_id, start_time, rag_task)

            return {
                "version": "2.0",
                "useCallback": True,
                "data": {
                    "text": f"'{utterance}'에 대해 확인 중입니다.\n잠시만 기다려 주세요!"
                }
            }

    except Exception as e:
        logger.error(f"Kakaotalk Error: {e}")
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"simpleText": {"text": "서버 오류가 발생했습니다."}}
                ]
            }
        }