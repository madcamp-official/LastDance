"""Judge0(자체 호스팅) 실행 클라이언트. docs/CLAUDE.md: 사용자 코드는 반드시 Judge0(동급
샌드박스) 경유로만 실행하고 백엔드가 직접 eval/exec 하지 않는다.
"""

import base64
import os
from dataclasses import dataclass
from typing import Optional

import requests

JUDGE0_URL = os.getenv("JUDGE0_URL", "http://localhost:2358")
_REQUEST_TIMEOUT_SEC = 20


class Judge0Unavailable(Exception):
    """Judge0 서버에 연결하지 못했거나 예기치 않은 응답을 받음."""


@dataclass
class Judge0Result:
    status_id: int
    status_description: str
    stdout: str
    stderr: str
    compile_output: str
    time_sec: Optional[float]   # None이면 실행 자체가 안 됨(컴파일 에러 등)
    memory_kb: Optional[int]


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _unb64(text: Optional[str]) -> str:
    if not text:
        return ""
    return base64.b64decode(text).decode("utf-8", errors="replace")


class Judge0Client:
    def __init__(self, base_url: str = JUDGE0_URL) -> None:
        self._base_url = base_url.rstrip("/")

    def run(
        self,
        *,
        source_code: str,
        language_id: int,
        stdin: str,
        cpu_time_limit: float,
        memory_limit_kb: int,
        compiler_options: Optional[str] = None,
    ) -> Judge0Result:
        payload = {
            "source_code": _b64(source_code),
            "language_id": language_id,
            "stdin": _b64(stdin),
            "cpu_time_limit": cpu_time_limit,
            "memory_limit": memory_limit_kb,
        }
        if compiler_options:
            payload["compiler_options"] = compiler_options

        try:
            resp = requests.post(
                f"{self._base_url}/submissions",
                params={"base64_encoded": "true", "wait": "true"},
                json=payload,
                timeout=_REQUEST_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            body = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise Judge0Unavailable(str(exc)) from exc

        status = body.get("status") or {}
        time_raw = body.get("time")
        memory_raw = body.get("memory")
        return Judge0Result(
            status_id=status.get("id", 13),
            status_description=status.get("description", "Unknown"),
            stdout=_unb64(body.get("stdout")),
            stderr=_unb64(body.get("stderr")),
            compile_output=_unb64(body.get("compile_output")),
            time_sec=float(time_raw) if time_raw is not None else None,
            memory_kb=int(memory_raw) if memory_raw is not None else None,
        )
