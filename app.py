import importlib
import os
import threading
import traceback

from flask import Flask, jsonify, request


app = Flask(__name__)

_rag_module = None
_rag_error = None
_rag_lock = threading.Lock()


def _load_rag():
    global _rag_module, _rag_error

    if _rag_module is not None:
        return _rag_module

    with _rag_lock:
        if _rag_module is not None:
            return _rag_module

        try:
            _rag_module = importlib.import_module("rag_core")
            _rag_error = None
        except Exception as exc:
            _rag_error = f"{exc}\n{traceback.format_exc()}"
            raise

    return _rag_module


def _warmup_rag():
    try:
        _load_rag()
        app.logger.info("RAG resources loaded.")
    except Exception:
        app.logger.exception("Failed to load RAG resources.")


threading.Thread(target=_warmup_rag, daemon=True).start()


def _kakao_text(text):
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": text[:1000],
                    }
                }
            ]
        },
    }


def _format_answer(result):
    title = result.get("title") or "답변"
    answer = result.get("answer") or "답변을 만들지 못했습니다."
    links = result.get("links") or []

    text = f"{title}\n\n{answer}".strip()
    if links:
        refs = []
        for link in links[:3]:
            link_title = link.get("title", "참고 링크")
            url = link.get("url", "")
            refs.append(f"- {link_title}\n{url}")
        text += "\n\n참고\n" + "\n".join(refs)

    return text


@app.get("/")
def health():
    return jsonify({"status": "ok", "service": "kau-kakao-bot"})


@app.get("/health")
def health_check():
    status = "ready" if _rag_module is not None else "loading"
    return jsonify({"status": status, "rag_error": _rag_error})


@app.post("/kakao")
@app.post("/message")
@app.post("/webhook")
def kakao_webhook():
    body = request.get_json(silent=True) or {}
    utterance = (
        body.get("userRequest", {}).get("utterance")
        or body.get("utterance")
        or body.get("query")
        or ""
    ).strip()

    if not utterance:
        return jsonify(_kakao_text("질문 내용을 입력해 주세요."))

    if _rag_module is None:
        if _rag_error:
            app.logger.error("RAG load error: %s", _rag_error)
            return jsonify(_kakao_text("검색 데이터 로딩 중 오류가 발생했습니다. 서버 로그를 확인해 주세요."))
        return jsonify(_kakao_text("검색 데이터를 불러오는 중입니다. 잠시 후 다시 질문해 주세요."))

    try:
        result = _rag_module.get_ai_response(utterance)
        return jsonify(_kakao_text(_format_answer(result)))
    except Exception:
        app.logger.exception("Failed to handle Kakao webhook.")
        return jsonify(_kakao_text("답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
