"""LLM 응답 grounding 검증 + 실패 시 템플릿 폴백 (dev-plan §7.3).

생성 텍스트에 입력 프롬프트(결정론적으로 조립된 실측 데이터)에 없는 숫자가
섞여 있으면 근거 없는 값으로 간주한다. 재시도로도 못 걷어내면 LLM 텍스트를
버리고 이 모듈의 규칙 기반 문장으로 대체한다."""

import logging
import re
from typing import List, Optional, Tuple

from app.model.analysis import PatternWindowRow, PauseEventRow, PivotEventRow, SessionSummary
from app.model.problem import Problem
from app.model.submission import JudgeSubmission
from app.schema.baseline import ProblemBaselineResponse
from app.schema.timeline import SEGMENT_LABEL_KO

logger = logging.getLogger("app.llm.grounding")

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

# 숫자 하나가 입력 프롬프트의 어떤 값에서 유래했다고 인정할 오차범위(반올림 등)
_TOLERANCE = 1.0
# 단위 변환 허용 목록: 원본 그대로 / ms->s / ms->분
_UNIT_DIVISORS = (1.0, 1000.0, 60000.0)

_PHASE_LABELS = {
    "setup_ms": "설계(setup)",
    "formation_ms": "형성(formation)",
    "debug_ms": "디버그(debug)",
    "refine_ms": "다듬기(refine)",
}
_HIGH_BANDS = (">p90", "p75~p90")
_LOW_BANDS = ("<p10", "p10~p25")


def _extract_numbers(text: str) -> List[float]:
    return [float(m) for m in _NUMBER_RE.findall(text)]


def _allowed_values(source_numbers: List[float]) -> List[float]:
    return [n / divisor for n in source_numbers for divisor in _UNIT_DIVISORS]


def _is_grounded(value: float, allowed: List[float]) -> bool:
    return any(abs(value - a) <= _TOLERANCE for a in allowed)


def verify_grounding(response_text: str, source_prompt: str) -> Tuple[bool, List[float]]:
    """response_text의 숫자들이 source_prompt(입력 데이터 프롬프트)에서
    유래했는지 검증. 근거 없는 숫자 목록과 함께 (통과 여부)를 반환한다."""
    allowed = _allowed_values(_extract_numbers(source_prompt))
    ungrounded = [n for n in _extract_numbers(response_text) if not _is_grounded(n, allowed)]
    return (len(ungrounded) == 0, ungrounded)


def _baseline_sentence(baseline: Optional[ProblemBaselineResponse]) -> Optional[str]:
    if not baseline or not baseline.metrics:
        return None
    for m in baseline.metrics:
        if m.metric == "total_duration" and m.user_band:
            if m.user_band in _HIGH_BANDS:
                return "전체 풀이 시간은 같은 유형 문제를 푼 다른 응시자들보다 오래 걸린 편입니다."
            if m.user_band in _LOW_BANDS:
                return "전체 풀이 시간은 같은 유형 문제를 푼 다른 응시자들보다 빠른 편입니다."
    return None


def build_template_feedback(
    problem: Optional[Problem],
    summary: Optional[SessionSummary],
    pauses: List[PauseEventRow],
    pivots: List[PivotEventRow],
    windows: List[PatternWindowRow],
    latest: Optional[JudgeSubmission],
    baseline: Optional[ProblemBaselineResponse],
) -> str:
    """LLM grounding 검증에 최종 실패했을 때 쓰는 규칙 기반 폴백 문장.
    입력 데이터에 실제로 있는 값만 사용하므로 grounding 문제가 원천적으로 없다."""
    if summary is None:
        return "아직 행동 분석 데이터가 충분하지 않아 상세 피드백을 생성할 수 없습니다."

    sentences: List[str] = []

    phase_ms = {
        "setup_ms": summary.setup_ms,
        "formation_ms": summary.formation_ms,
        "debug_ms": summary.debug_ms,
        "refine_ms": summary.refine_ms,
    }
    longest_key = max(phase_ms, key=lambda k: phase_ms[k])
    sentences.append(f"이번 풀이에서는 {_PHASE_LABELS[longest_key]} 단계에 시간이 가장 많이 걸렸습니다.")

    baseline_sentence = _baseline_sentence(baseline)
    if baseline_sentence:
        sentences.append(baseline_sentence)

    if pauses:
        top_pause = max(pauses, key=lambda p: p.duration_ms)
        label = top_pause.pattern or top_pause.phase or "특정 구간"
        sentences.append(f"정지는 주로 {label} 관련 지점에 집중되었습니다.")

    if pivots:
        sentences.append(f"재작성(pivot)은 총 {len(pivots)}회 발생했습니다.")

    if latest and latest.verdict:
        sentences.append(f"최종 제출 결과는 {latest.verdict}입니다.")

    return " ".join(sentences)


_MAX_TEMPLATE_INSIGHTS = 3
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def build_timeline_template_feedback(insights: List, segments: List, summary=None) -> str:
    """git-timeline-feedback-spec.md §5.4 템플릿 폴백 (Stage C).

    LLM grounding 최종 실패 또는 인사이트 0개(분석기 실패/지연)일 때 쓰는 규칙 기반 문장.
    insights는 severity 순 최대 3개만 쓰고, 없으면 세그먼트 통계만으로 생성한다.
    입력 데이터에 실제로 있는 값만 쓰므로 grounding 문제가 원천적으로 없다.
    """
    sentences: List[str] = []

    if insights:
        ordered = sorted(
            insights,
            key=lambda i: (_SEVERITY_ORDER.get(i.severity, 3), -int(i.duration_ms or 0)),
        )[:_MAX_TEMPLATE_INSIGHTS]
        for i in ordered:
            sentences.append(
                f"{i.logic_label} 구간에서 {int(i.duration_ms or 0) // 1000}초를 사용하셨습니다."
            )
        return " ".join(sentences)

    if not segments:
        return "아직 행동 분석 데이터가 충분하지 않아 상세 피드백을 생성할 수 없습니다."

    # 인사이트가 없을 때: 결정론적 세그먼트 통계만으로 서술한다.
    # churn/burst를 "어려웠던 구간"으로 부르지 않는 라벨 문구를 그대로 쓴다 (§2.4 R1).
    by_label: dict = {}
    for s in segments:
        by_label[s.label] = by_label.get(s.label, 0) + max(0, s.t_end_ms - s.t_start_ms)
    top_label = max(by_label, key=lambda k: by_label[k])
    sentences.append(
        f"{SEGMENT_LABEL_KO.get(top_label, top_label)}에 "
        f"{by_label[top_label] // 1000}초가 쓰였습니다."
    )
    stall_ms = by_label.get("STALL_SUSPECT", 0)
    if stall_ms:
        sentences.append(f"긴 정지가 포함된 사고 구간은 총 {stall_ms // 1000}초입니다.")
    if summary is not None and getattr(summary, "total_ms", 0):
        sentences.append(f"전체 풀이 시간은 {summary.total_ms // 1000}초입니다.")
    return " ".join(sentences)
