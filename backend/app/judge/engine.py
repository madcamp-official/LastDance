"""제출 채점 오케스트레이션. 실제 코드 실행은 전부 Judge0Client에 위임한다.

정책(요청 범위): AC/WA/기타 에러 판정과, AC일 때의 총 실행 시간/메모리 소모량만 본다.
테스트케이스 idx 오름차순으로 하나씩 채점하다가 첫 실패(AC가 아님)에서 즉시 중단한다.
"""

import re
from dataclasses import dataclass
from typing import Optional

from app.judge.client import Judge0Client, Judge0Result, Judge0Unavailable
from app.judge.languages import resolve_language, UnsupportedLanguage
from app.judge.testcases import iter_testcases, TestcasesNotFound

# AtCoder 표준 기본값. problems.time_limit/memory_limit이 NULL이거나 파싱 실패할 때만 fallback.
DEFAULT_CPU_TIME_LIMIT_SEC = 2.0
DEFAULT_MEMORY_LIMIT_KB = 1024 * 1024  # 1024 MB

_RUNTIME_ERROR_STATUS_IDS = {7, 8, 9, 10, 11, 12, 14}
_TIME_LIMIT_RE = re.compile(r"(\d+(?:\.\d+)?)")  # "2 sec" -> 2.0
_MEMORY_LIMIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*MB", re.IGNORECASE)  # "1024MB" -> 1024


def _parse_time_limit_sec(time_limit: Optional[str]) -> float:
    # problems.time_limit 형식: docs/api-spec.md 예시 "1 sec".
    if time_limit:
        m = _TIME_LIMIT_RE.search(time_limit)
        if m:
            return float(m.group(1))
    return DEFAULT_CPU_TIME_LIMIT_SEC


def _parse_memory_limit_kb(memory_limit: Optional[str]) -> int:
    # problems.memory_limit 형식: docs/api-spec.md 예시 "1024MB".
    if memory_limit:
        m = _MEMORY_LIMIT_RE.search(memory_limit)
        if m:
            return round(float(m.group(1)) * 1024)
    return DEFAULT_MEMORY_LIMIT_KB


class Judge0InternalError(Exception):
    """Judge0 자체가 처리에 실패함(사용자 코드 문제가 아님)."""


@dataclass
class JudgeOutcome:
    verdict: str  # "AC" | "WA" | "TLE" | "RE" | "CE" | "MLE"
    runtime_ms: Optional[int]
    memory_kb: Optional[int]


def _normalize(text: str) -> str:
    # 대회 채점 관례: 줄 끝 공백/파일 끝 개행 차이는 무시하고 비교.
    return "\n".join(line.rstrip() for line in text.strip("\n").splitlines())


def _verdict_for(result: Judge0Result, expected_output: str, memory_limit_kb: int) -> Optional[str]:
    """실행 결과 하나를 판정. AC면 None을 반환(호출부가 누적 후 다음 케이스로 진행).

    Judge0(isolate)에는 별도의 MLE status_id가 없다 — 메모리 초과로 강제 종료되면
    보통 Runtime Error 계열(SIGKILL 등)로 잡힌다. 그래서 status와 무관하게 실측
    memory가 설정한 memory_limit_kb 이상이면 우선적으로 MLE로 판정한다.
    """
    if result.status_id == 6:
        return "CE"
    if result.memory_kb is not None and result.memory_kb >= memory_limit_kb:
        return "MLE"
    if result.status_id == 5:
        return "TLE"
    if result.status_id in _RUNTIME_ERROR_STATUS_IDS:
        return "RE"
    if result.status_id not in (3, 4):
        # 1/2(큐/처리중)은 wait=true라 오면 안 되고, 13(Internal Error)은 judge0 인프라 문제.
        raise Judge0InternalError(f"unexpected judge0 status_id={result.status_id}")
    if _normalize(result.stdout) != _normalize(expected_output):
        return "WA"
    return None


def judge_submission(
    *,
    code: str,
    language: str,
    problem_source: str,
    client: Judge0Client,
    time_limit: Optional[str] = None,
    memory_limit: Optional[str] = None,
) -> JudgeOutcome:
    """UnsupportedLanguage / TestcasesNotFound / Judge0Unavailable / Judge0InternalError를
    던질 수 있음 — 호출부(API 라우터)가 적절한 HTTP 에러로 변환한다.

    time_limit/memory_limit은 problems.time_limit/memory_limit(docs/api-spec.md 형식,
    예: "2 sec"/"1024MB") 원문 그대로 받아 여기서 파싱한다. None이거나 파싱 실패 시
    DEFAULT_CPU_TIME_LIMIT_SEC/DEFAULT_MEMORY_LIMIT_KB로 fallback.
    """
    lang_spec = resolve_language(language)
    cpu_time_limit_sec = _parse_time_limit_sec(time_limit)
    memory_limit_kb = _parse_memory_limit_kb(memory_limit)

    total_time_sec = 0.0
    max_memory_kb = 0
    ran_any = False

    for tc in iter_testcases(problem_source):
        result = client.run(
            source_code=code,
            language_id=lang_spec.judge0_id,
            stdin=tc.input,
            cpu_time_limit=cpu_time_limit_sec,
            memory_limit_kb=memory_limit_kb,
            compiler_options=lang_spec.compiler_options,
        )
        verdict = _verdict_for(result, tc.output, memory_limit_kb)
        if verdict is not None:
            return JudgeOutcome(verdict=verdict, runtime_ms=None, memory_kb=None)

        ran_any = True
        total_time_sec += result.time_sec or 0.0
        max_memory_kb = max(max_memory_kb, result.memory_kb or 0)

    if not ran_any:
        # 테스트케이스가 0개인 문제 데이터 — 채점 불능이므로 내부 오류로 취급.
        raise Judge0InternalError(f"no testcases for problem_source={problem_source}")

    return JudgeOutcome(
        verdict="AC",
        runtime_ms=round(total_time_sec * 1000),
        memory_kb=max_memory_kb,
    )


__all__ = [
    "judge_submission",
    "JudgeOutcome",
    "Judge0InternalError",
    "UnsupportedLanguage",
    "TestcasesNotFound",
    "Judge0Unavailable",
]
