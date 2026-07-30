"""Stage B — LLM 세션 분석기 (git-timeline-feedback-spec.md §4).

커밋 로그(결정론적 Stage A 산출)와 문제 지문을 함께 넘겨, 사용자가 풀이의 "어느 논리
단계"에서 시간을 썼는지를 구조화 JSON으로 받는다. 출력은 사람에게 직접 보이지 않고
problem_feedback_insights에 쌓여 (a) 피드백 작성기의 근거, (b) 같은 문제를 푼 다른
사용자와의 비교군이 된다.

결정성: temperature=0 + seed 고정 + json_mode (§7). 프롬프트 문구가 바뀌면
ANALYZER_VERSION을 올린다 — 과거 인사이트와 비교 불가해지므로 백필 기준이 된다.
"""

import json
import logging
import re
from typing import List, Optional, Tuple

from pydantic import ValidationError

from app.llm.client import LLM_MODEL, VLLMClient
from app.schema.insight import (
    CATEGORY_ENUM,
    SEVERITY_ENUM,
    STAGE_ENUM,
    AnalyzerOutput,
    SessionInsight,
    ValidatedInsight,
)
from app.schema.timeline import Commit, TimelineResult

logger = logging.getLogger("app.llm.session_analyzer")

PROMPT_VERSION = 1
ANALYZER_VERSION = f"session-analyzer-v{PROMPT_VERSION}/{LLM_MODEL}"

_SEED = 20260730
_TEMPERATURE = 0.0
MAX_PARSE_ATTEMPTS = 2          # §4.5.1: 파싱 실패 시 1회 재시도
MIN_COMMITS = 2                 # §4: 커밋 2개 미만이면 호출하지 않음

# ---- 토큰 상한 (§4.1) ----
MAX_HUNK_LINES = 40             # 커밋당 hunk 라인 최대 40줄
MAX_LINE_CHARS = 160            # 라인당 160자 절단
MAX_STATEMENT_CHARS = 3000      # 지문 3000자 초과 시 절단
MAX_FINAL_CODE_LINES = 400      # 최종 코드 400줄 초과 시 앞 400줄
# 모델 컨텍스트의 60% 상한을 문자 예산으로 근사(qwen3-coder 32k 컨텍스트 기준).
# 초과하면 STEADY 세그먼트의 hunk 원문부터 통계 요약으로 축약한다(라벨·수치는 유지).
MAX_COMMIT_LOG_CHARS = 60000

# §4.5.4 (R1 기계 검증): category="stall" 인사이트가 근거로 인정받는 pause 하한
STALL_EVIDENCE_PAUSE_MS = 30000


# ---------------------------------------------------------------- 렌더링 (§4.1)
def _fmt_t(ms: int) -> str:
    total_s = max(0, ms) // 1000
    return f"+{total_s // 60}m{total_s % 60:02d}s"


def _trunc(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + "...(절단)"


def _hunk_text_lines(commit: Commit) -> List[str]:
    out: List[str] = []
    for h in commit.hunks:
        if h.op == "add":
            for i, line in enumerate(h.new_lines):
                out.append(f"  + L{h.new_start + i}: {_trunc(line)}")
        elif h.op == "del":
            for i, line in enumerate(h.old_lines):
                out.append(f"  - L{h.old_start + i}: {_trunc(line)}")
        else:  # mod — 겹치는 위치는 "이전 => 이후", 남는 쪽은 추가/삭제로 표기
            n = min(len(h.old_lines), len(h.new_lines))
            for i in range(n):
                out.append(
                    f"  ~ L{h.new_start + i}: {_trunc(h.old_lines[i])}"
                    f"  =>  {_trunc(h.new_lines[i])}"
                )
            for i in range(n, len(h.new_lines)):
                out.append(f"  + L{h.new_start + i}: {_trunc(h.new_lines[i])}")
            for i in range(n, len(h.old_lines)):
                out.append(f"  - L{h.old_start + i}: {_trunc(h.old_lines[i])}")
    if len(out) > MAX_HUNK_LINES:
        omitted = len(out) - MAX_HUNK_LINES
        out = out[:MAX_HUNK_LINES] + [f"  ...(+{omitted}줄 생략)"]
    return out


def _numbered(text: str, max_lines: Optional[int] = None) -> str:
    lines = text.splitlines()
    truncated = max_lines is not None and len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
    body = "\n".join(f"L{i + 1}: {_trunc(line)}" for i, line in enumerate(lines))
    return body + ("\n...(이하 생략)" if truncated else "")


def render_commit_log(result: TimelineResult, collapse_steady: bool = False) -> str:
    """§4.1 커밋 로그 렌더링. collapse_steady면 STEADY 구간 hunk 원문을 통계로 축약."""
    seg_first = {s.commit_start_seq: s.label for s in result.segments}
    out: List[str] = []
    for c in result.commits:
        if c.kind == "submit":
            out.append(f"@s{c.seq} t={_fmt_t(c.t_ms)} SUBMIT verdict={c.verdict or 'PENDING'}")
            if c.snapshot_text:
                out.append(f"\n=== 스냅샷: @s{c.seq} 제출 시점 전체 코드 ===")
                out.append(_numbered(c.snapshot_text, MAX_FINAL_CODE_LINES))
                out.append("=== 스냅샷 끝 ===\n")
            continue
        tag = f" [{seg_first[c.seq]}]" if c.seq in seg_first else ""
        out.append(
            f"@c{c.seq} t={_fmt_t(c.t_ms)} pause={c.pause_before_ms // 1000}s "
            f"dur={c.duration_ms // 1000}s{tag}"
        )
        if collapse_steady and c.segment_label == "STEADY":
            out.append(
                f"  (요약: +{c.lines_added} -{c.lines_deleted} ~{c.lines_modified}, "
                f"순증가 {c.net_lines})"
            )
        else:
            out.extend(_hunk_text_lines(c))
    return "\n".join(out)


def render_segment_summary(result: TimelineResult) -> str:
    """§4.3 [세그먼트 요약] 블록."""
    lines: List[str] = []
    for s in result.segments:
        dur = max(0, s.t_end_ms - s.t_start_ms) // 1000
        lines.append(
            f"{s.seg_id} [{s.label}] @c{s.commit_start_seq}~@c{s.commit_end_seq} "
            f"({_fmt_t(s.t_start_ms)}~{_fmt_t(s.t_end_ms)}, {dur // 60}분{dur % 60:02d}초)"
        )
        lines.append(
            f"  pause합={s.pause_ms // 1000}s, 만진라인={s.lines_touched}, 순증가={s.net_lines}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------- 프롬프트 (§4.2~§4.3)
SYSTEM_PROMPT = """당신은 알고리즘 문제 풀이(PS) 학습 플랫폼의 세션 분석 엔진입니다. 한 사용자가 한 문제를 푸는
동안의 코드 작성 기록이 git 커밋 로그와 유사한 형식으로 주어집니다. 당신의 임무는 이 기록과
문제 지문을 종합해, 사용자가 풀이의 "어느 논리 단계"에서 시간을 썼고 어디서 막혔는지를
구조화된 JSON으로 출력하는 것입니다. 당신의 출력은 사람에게 직접 보이지 않고 DB에 저장되어
이후 피드백 생성과 다른 사용자와의 비교에 사용됩니다.

[입력 형식]
1. 커밋 로그: "@c{번호}"는 편집 커밋, "@s{번호}"는 제출입니다. 커밋 경계는 5초 이상의 입력
   중단(pause)입니다. pause=N초는 그 커밋을 시작하기 "전에" 아무것도 입력하지 않은 시간,
   dur=N초는 그 커밋 안에서 실제로 타이핑한 시간입니다.
2. 라인 표기: "+ L12:"는 추가, "- L12:"는 삭제, "~ L12: A => B"는 수정입니다.
3. 라인 번호는 "그 커밋 시점의 파일 상태" 기준입니다. 이후 커밋에서 위쪽에 라인이 추가되거나
   삭제되면 같은 코드라도 물리 라인 번호가 밀립니다. 따라서 라인 번호를 세션 전체의 고정
   좌표로 취급하지 말고, 코드 내용(함수명, 변수, 구문)으로 논리적 블록을 추적하세요.
   예: @c3에서 추가된 "L7: dp[i] = ..."와 @c9에서 수정된 "L11: dp[i] = ..."는 같은 라인일
   수 있습니다.
4. 세그먼트 라벨은 결정론적 전처리기가 통계 규칙으로 부여한 것입니다:
   - STALL_SUSPECT: 긴 입력 중단 후에도 코드가 거의 늘지 않은 구간. 관찰, 접근 설계,
     점화식 도출, 탐색 정책 결정 같은 "사고" 구간일 가능성이 높습니다.
   - HIGH_CHURN: 같은 라인들을 반복해서 고치거나, 만진 라인 수에 비해 순증가가 적은 구간.
     대개 사소한 디버깅(타이포, 인덱스, 자료형, 출력 형식)입니다.
   - DEBUG_LOOP: 실패한 제출과 다음 제출 사이의 수정 구간.
   - BURST_WRITE: 짧은 시간에 코드가 폭발적으로 늘어난 구간. 이미 머릿속에 있는 설계를
     타이핑으로 옮기는 중이므로, 분량이 커도 "어려웠던 부분"이 아닙니다.
   - STEADY: 평이한 진행.

[절대 규칙]
R1. 세그먼트 라벨의 "구분"을 뒤집지 마세요. 특히:
    - HIGH_CHURN이나 BURST_WRITE 구간을 코드 분량이 크다는 이유로 "어려워했던 부분",
      "핵심 난관"으로 진단하는 것은 금지입니다. 코드가 폭발적으로 늘어난 40줄 구간은
      거의 항상 설계가 끝난 뒤의 타이핑이거나 사소한 디버깅입니다.
    - "어려웠던 부분"(category="stall") 진단은 STALL_SUSPECT 세그먼트, 또는 큰 pause 뒤에
      핵심 로직 라인이 처음 등장하는 지점에만 허용됩니다.
    - HIGH_CHURN/DEBUG_LOOP 구간은 category="churn" 또는 "debug_loop"로만 보고하고,
      무엇을 반복 수정했는지(변수 자료형, 인덱스 경계, 출력 형식 등)를 코드 내용에서
      구체적으로 특정하세요.
R2. 모든 인사이트는 실제 커밋 번호 범위와 시간 범위를 evidence로 가져야 합니다. 입력에 없는
    커밋 번호, 입력에서 계산할 수 없는 시간을 만들어내지 마세요.
R3. logic_label은 문제의 풀이 논리 수준으로 구체적이어야 합니다.
    나쁜 예: "알고리즘 작성", "디버깅", "구현".
    좋은 예: "BFS 큐 뼈대 구현", "문제 고유 탐색 정책(말이 벽을 만나면 멈춤)의 조건 처리",
    "DP 점화식 도출", "long long 오버플로 수정", "출력 형식(공백/개행) 수정".
    문제 지문의 요구사항과 코드 내용을 대조해서 단계를 명명하세요.
5초 이상 pause마다 커밋이 잘리므로, pause가 크고 직후 커밋에서 새 논리가 등장하면 그 pause를
그 논리의 "사고 시간"으로 귀속할 수 있습니다.
R4. 확신이 없으면 인사이트를 만들지 말고 빼세요. 인사이트 0개도 유효한 출력입니다.
R5. 출력은 아래 JSON 스키마를 따르는 단일 JSON 객체만 출력합니다. JSON 밖의 텍스트, 마크다운
    코드펜스, 주석을 출력하지 마세요. description/logic_label/advice는 한국어로 씁니다.

[stage 정준 값 — 이 목록 밖의 값 금지]
- PROBLEM_UNDERSTANDING  : 문제 이해·관찰 (입력 파싱 설계 이전의 긴 정지 등)
- APPROACH_DESIGN        : 알고리즘/접근 선택 (자료구조·전략 결정)
- CORE_LOGIC_DESIGN      : 핵심 논리 설계 (점화식, 탐색 정책, 불변식, 수식 유도)
- SCAFFOLD_IMPLEMENTATION: 뼈대 구현 (입출력, 자료구조 선언, 표준 알고리즘 골격)
- CORE_IMPLEMENTATION    : 핵심 논리의 코드화 (정책·점화식을 실제 조건/식으로 옮기기)
- EDGE_CASE_HANDLING     : 경계·예외 처리 (빈 입력, 범위 끝, 중복 방문 등)
- DEBUG_LOGIC            : 논리 오류 디버깅 (잘못된 점화식/정책 수정)
- DEBUG_TRIVIAL          : 사소한 디버깅 (타이포, 자료형, 인덱스 ±1, 출력 형식)
- OPTIMIZATION           : 시간/공간 최적화 (TLE 대응 등)

[출력 JSON 스키마]
{
  "insights": [
    {
      "stage": "<정준 stage 값>",
      "category": "stall" | "churn" | "debug_loop" | "smooth",
      "logic_label": "<이 문제 풀이 논리 수준의 구체적 명명>",
      "description": "<무엇이 관찰되었는지 1~2문장, 커밋/시간 근거 포함>",
      "severity": "high" | "medium" | "low",
      "commit_range": [<시작 seq>, <끝 seq>],
      "t_range_ms": [<시작 ms>, <끝 ms>],
      "evidence": ["c3 앞 64초 정지", "c4에서 dp 점화식 라인 최초 등장"],
      "advice": "<이 문제 유형에서 관찰된 행동에 근거한 개선 제안 1문장, 없으면 null>"
    }
  ],
  "overall": "<세션 전체 흐름 요약 2~3문장. 잘한 점(빠르게 진행된 stage)도 포함>"
}"""


def build_user_prompt(problem, result: TimelineResult, lang: str) -> str:
    """§4.3 사용자 프롬프트. problem은 app.model.problem.Problem (없으면 None)."""
    statement = (getattr(problem, "statement", "") or "") if problem else ""
    if len(statement) > MAX_STATEMENT_CHARS:
        statement = statement[:MAX_STATEMENT_CHARS] + "...(생략)"

    lines = [
        "[문제]",
        f"제목: {getattr(problem, 'title', '알 수 없음') if problem else '알 수 없음'}",
        "지문:",
        statement,
    ]
    for label, attr in (("제약", "constraints"), ("입력 형식", "input_format"), ("출력 형식", "output_format")):
        value = getattr(problem, attr, None) if problem else None
        if value:
            lines.append(f"{label}: {value}")

    verdict_seq = result.verdict_seq
    lines += [
        "",
        "[세션 정보]",
        f"언어: {lang}",
        f"총 소요: {result.total_ms / 60000:.1f}분, 키 입력 {result.keystroke_count}회",
        f"제출 이력: {' → '.join(verdict_seq) if verdict_seq else '제출 없음'}",
        f"최종 결과: {verdict_seq[-1] if verdict_seq else '제출 없음'}",
        "",
        "[세그먼트 요약 — 결정론적 전처리 결과, 라벨 구분을 뒤집지 말 것]",
        render_segment_summary(result),
        "",
        "[커밋 로그]",
    ]

    log = render_commit_log(result)
    if len(log) > MAX_COMMIT_LOG_CHARS:
        log = render_commit_log(result, collapse_steady=True)
    lines.append(log)

    lines += [
        "",
        "[최종 코드]",
        _numbered(result.final_code, MAX_FINAL_CODE_LINES),
        "",
        "위 기록을 분석해 시스템 지침의 JSON 스키마로 출력하세요.",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------- 출력 검증 (§4.5)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_output(text: str) -> AnalyzerOutput:
    """스키마 위반이면 ValueError — 호출부가 1회 재시도한다 (§4.5.1)."""
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    try:
        return AnalyzerOutput(**json.loads(cleaned))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ValueError(f"session analyzer output parse failed: {exc}") from exc


def validate_insight(insight: SessionInsight, result: TimelineResult) -> Tuple[bool, str]:
    """§4.5.2~§4.5.4 결정론적 검증. (통과 여부, 실패 사유)."""
    if insight.stage not in STAGE_ENUM:
        return False, f"stage {insight.stage!r} not in canonical enum"
    if insight.category not in CATEGORY_ENUM:
        return False, f"category {insight.category!r} not in enum"
    if insight.severity not in SEVERITY_ENUM:
        return False, f"severity {insight.severity!r} not in enum"

    c_start, c_end = insight.commit_range
    seqs = {c.seq for c in result.commits}
    if c_start not in seqs or c_end not in seqs:
        return False, f"commit_range {insight.commit_range} references unknown seq"
    if c_start > c_end:
        return False, f"commit_range {insight.commit_range} start > end"

    t_start, t_end = insight.t_range_ms
    if t_start > t_end:
        return False, f"t_range_ms {insight.t_range_ms} start > end"
    bound = result.t_bound_ms
    if t_start < 0 or t_end > bound:
        return False, f"t_range_ms {insight.t_range_ms} outside [0, {bound}]"

    # §4.5.4: R1 기계 검증 — stall 진단은 STALL_SUSPECT 세그먼트나
    # pause ≥ 30s 커밋을 근거로 가져야 한다 (분량 큰 구간의 오진 차단)
    if insight.category == "stall":
        overlaps_stall = any(
            s.label == "STALL_SUSPECT"
            and s.commit_start_seq <= c_end
            and c_start <= s.commit_end_seq
            for s in result.segments
        )
        has_long_pause = any(
            c.kind == "edit"
            and c_start <= c.seq <= c_end
            and c.pause_before_ms >= STALL_EVIDENCE_PAUSE_MS
            for c in result.commits
        )
        if not (overlaps_stall or has_long_pause):
            return False, "category=stall without STALL_SUSPECT overlap or >=30s pause (R1)"
    return True, ""


def validate_output(output: AnalyzerOutput, result: TimelineResult) -> List[ValidatedInsight]:
    validated: List[ValidatedInsight] = []
    for insight in output.insights:
        ok, reason = validate_insight(insight, result)
        validated.append(
            ValidatedInsight(
                insight=insight, status="valid" if ok else "discarded", reason=reason
            )
        )
    return validated


async def analyze_session_llm(
    client: VLLMClient,
    problem,
    result: TimelineResult,
    lang: str,
) -> Tuple[List[ValidatedInsight], str]:
    """세션당 1회 호출. (검증 결과가 붙은 인사이트들, overall 요약).

    §4.5.1: JSON 파싱이 두 번 다 실패하면 인사이트 없음(로그만). LLMUnavailable은
    그대로 전파 — 호출부(consumer)가 로그만 남기고 파이프라인은 계속 간다.
    """
    if result.analysis_level != "full" or len(result.commits) < MIN_COMMITS:
        return [], ""

    user_prompt = build_user_prompt(problem, result, lang)
    for attempt in range(MAX_PARSE_ATTEMPTS):
        llm_result = await client.chat(
            system=SYSTEM_PROMPT, user=user_prompt,
            temperature=_TEMPERATURE, seed=_SEED, json_mode=True,
        )
        try:
            output = parse_output(llm_result.text)
        except ValueError as exc:
            logger.warning(
                "session analyzer parse failed (attempt=%d/%d): %s",
                attempt + 1, MAX_PARSE_ATTEMPTS, exc,
            )
            continue
        validated = validate_output(output, result)
        for v in validated:
            if v.status == "discarded":
                logger.warning(
                    "insight discarded (stage=%s, category=%s): %s",
                    v.insight.stage, v.insight.category, v.reason,
                )
        return validated, output.overall
    return [], ""
