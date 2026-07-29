"""vLLM(OpenAI 호환 API) 클라이언트. httpx 비동기 호출 — 원격 LLM 서버 응답 지연이 FastAPI
이벤트 루프를 막지 않도록 async로 구현(judge/client.py의 requests 동기 클라이언트와는 다름,
LLM 서버는 별도 클라우드망 호스트라 응답 지연 폭이 큼)."""

import os
from dataclasses import dataclass

import httpx

LLM_URL = os.getenv("LLM_URL", "localhost:8000")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3-coder:30b-a3b")

_CONNECT_TIMEOUT_SEC = 5.0
_READ_TIMEOUT_SEC = 120.0


class LLMUnavailable(Exception):
    """LLM 서버(vLLM)에 연결하지 못했거나 예기치 않은 응답을 받음."""


@dataclass
class LLMResult:
    text: str
    model: str


class VLLMClient:
    def __init__(self, base_url: str = LLM_URL, model: str = LLM_MODEL) -> None:
        base_url = base_url.rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            base_url = f"http://{base_url}"
        self._base_url = base_url
        self._model = model

    async def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: "float | None" = None,
        seed: "int | None" = None,
        json_mode: bool = False,
    ) -> LLMResult:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        # 구조 분류기(addendum §5)용: temperature=0 + seed 고정 + JSON 강제
        if temperature is not None:
            payload["temperature"] = temperature
        if seed is not None:
            payload["seed"] = seed
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        timeout = httpx.Timeout(connect=_CONNECT_TIMEOUT_SEC, read=_READ_TIMEOUT_SEC,
                                 write=_CONNECT_TIMEOUT_SEC, pool=_CONNECT_TIMEOUT_SEC)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{self._base_url}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMUnavailable(str(exc)) from exc

        choices = body.get("choices") or []
        text = (choices[0].get("message") or {}).get("content") if choices else None
        if not text:
            raise LLMUnavailable("empty response from LLM")
        return LLMResult(text=text, model=self._model)
