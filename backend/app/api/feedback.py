import logging
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.auth import APIError
from app.database import get_db
from app.llm.client import LLM_MODEL, LLMUnavailable, VLLMClient
from app.llm.grounding import build_template_feedback, verify_grounding
from app.model.analysis import PatternWindowRow, PauseEventRow, PivotEventRow, SessionSummary
from app.model.feedback import Feedback
from app.model.problem import Problem
from app.model.session import Submission
from app.model.submission import JudgeSubmission
from app.model.user import User
from app.schema.baseline import ProblemBaselineResponse
from app.schema.feedback import (
    FeedbackRatingRequest,
    FeedbackRatingResponse,
    FeedbackRequest,
    FeedbackResponse,
)
from app.util.baseline import build_baseline
from app.util.security import get_current_user

router = APIRouter(prefix="/feedback", tags=["feedback"])
logger = logging.getLogger("app.api.feedback")

_FALLBACK_TEXT = "피드백 생성 서버에 연결할 수 없어 잠시 후 다시 시도해 주세요."
_MAX_GROUNDING_RETRIES = 2  # dev-plan §7.3 — 최초 1회 + 재시도 2회

# app.worker.pivot.PIVOT_APPROACH_SWITCH — 여기선 문자열 상수만 복제(워커 의존 회피)
_PIVOT_APPROACH_SWITCH = "APPROACH_SWITCH"
_APPROACH_SWITCH_CHURN_THRESHOLD = 2  # 연구 4: 정지 크기보다 회복 패턴(재시도 횟수)이 신호

# 연구 2 (Hattie & Timperley, process-level feed-forward): 조언 문구를 LLM이 자유
# 창작하게 두면 데이터에 없는 일반론("변수명을 명확히 하세요")으로 흐르기 쉽다.
# AST 라벨(app.worker.labeler)·pivot 유형(app.worker.pivot)별로 실제 관찰 가능한
# 행동에 근거한 조언 후보만 미리 정해두고, LLM은 그중에서 문장만 매끄럽게 만들게 한다.
# OTHER는 특정 행동을 가리키지 않아 제외.
_ADVICE_BANK: dict[str, List[str]] = {
    "LOOP_BOUNDARY": [
        "반복문 경계(시작값·종료조건)에서 멈춘 시간이 길었다면, 반복 범위를 먼저 손으로 적어본 뒤 코드로 옮기는 순서를 추천합니다.",
        "반복 조건을 작성하기 전에 경계값(0부터인지 1부터인지, <인지 <=인지)을 작은 예시로 검증해보세요.",
    ],
    "BRANCH_CONDITION": [
        "조건문 분기 앞에서 정지가 잦았다면, 각 case가 언제 참/거짓이 되는지 먼저 표로 정리해보는 것이 도움이 됩니다.",
        "조건식이 복잡해지기 전에 나누어서 이름 있는 변수로 표현해보는 것도 시행착오를 줄이는 방법입니다.",
    ],
    "INDEX_REASONING": [
        "배열/인덱스 접근 지점에서 멈췄다면, 인덱스가 0-based인지 1-based인지를 먼저 명시적으로 정하고 시작하세요.",
        "인덱스 계산에서 정지가 있었다면 작은 예제로 인덱스 값을 손으로 추적해보는 것이 도움이 됩니다.",
    ],
    "INTERFACE_DESIGN": [
        "함수 시그니처(매개변수) 설계에서 정지가 있었다면, 입력과 출력 타입을 먼저 정의한 뒤 구현에 들어가는 순서를 시도해보세요.",
    ],
    "ALGORITHM_ENTRY": [
        "함수 본문을 시작하기 전에 정지가 길었다면, 전체 알고리즘을 의사코드로 먼저 스케치한 뒤 코드로 옮기는 방법을 추천합니다.",
    ],
    "SETUP": [
        "초기 설계(입력 처리, 자료구조 선언) 단계에서 정지가 길었다면, 문제의 입출력 형식을 먼저 정리하고 시작하는 것이 도움이 됩니다.",
    ],
    "SYNTAX_STRUGGLE": [
        "구문 오류 상태에서 멈춘 시간이 길었다면, 에러 메시지를 한 줄씩 읽고 대응하는 습관을 들여보세요.",
        "구문 오류가 반복됐다면, 괄호·세미콜론 같은 기본 문법을 먼저 점검한 뒤 로직에 집중하는 순서를 추천합니다.",
    ],
    "APPROACH_SWITCH": [
        "접근법을 여러 번 갈아엎었다면, 코드를 작성하기 전에 접근법 후보를 두세 개 비교하고 하나를 선택한 뒤 구현하는 순서를 추천합니다.",
        "전체 재작성이 반복됐다면, 초기 접근을 정하기 전에 시간·공간 복잡도를 먼저 어림해보는 것이 도움이 됩니다.",
    ],
    "COMPLEXITY_FIX": [
        "자료구조를 교체하는 재작성이 있었다면, 구현 전에 문제의 제약조건(N의 크기 등)을 보고 적절한 자료구조를 고르는 연습을 해보세요.",
    ],
    "EDGE_CASE_FIX": [
        "조건식을 수정하는 재작성이 있었다면, 구현 전에 경계 케이스(빈 입력, 최댓값 등)를 먼저 나열하고 검증하는 습관을 들여보세요.",
    ],
    "TYPO": [
        "구조는 그대로인데 다시 작성한 부분이 있었다면, 변수명이나 사소한 문법 실수를 먼저 점검해보는 것도 도움이 됩니다.",
    ],
}
_ADVICE_CANDIDATES_PER_KEY = 2

_SYSTEM_PROMPT = (
    "당신은 코딩 테스트 연습 플랫폼의 튜터입니다. 응시자가 문제를 푸는 동안의 "
    "행동 데이터(정지, 재작성, 구조 패턴 형성 순서 등)를 보고 피드백을 작성합니다. "
    "정답 여부나 채점 결과 자체를 요약하지 말고, 문제를 풀어나간 '과정'(어디서 오래 "
    "멈췄는지, 어느 기능을 구현하는 데 어려움이 있었는지, 접근 순서가 어땠는지)에 집중해서 한국어로 "
    "4~6문장의 바람직한 개선 방향을 포함하여 피드백을 작성하되 코드가 너무 짧거나 길면 그에 맞춰서 유연하게 "
    "피드백 길이를 조정하세요. 또한 사용자의 입력 데이터에 없는 사실은 언급하지 마세요. "
    "사용자 프롬프트에 [조언 후보] 목록이 주어지면 개선 방향은 반드시 그 목록 중에서만 골라 "
    "자연스러운 문장으로 녹여내고, 목록에 없는 일반적인 조언(예: '변수명을 명확히 하세요')은 "
    "새로 만들어내지 마세요. [조언 후보]가 없다면 관찰된 사실만 언급하고 개선 방향 제시는 생략해도 됩니다."
)


def _dominant(values: List[Optional[str]]) -> Optional[str]:
    counts = Counter(v for v in values if v)
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _iso(dt: datetime) -> str:
    # SQLite는 DateTime(timezone=True)라도 round-trip 후 tzinfo를 잃는다(naive가 됨).
    # 이 컬럼은 항상 UTC로만 채워지므로 naive면 UTC로 간주.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat().replace("+00:00", "Z")


def _load_owned_session(session_id: str, user_id: str, db: Session) -> Submission:
    sub = db.query(Submission).filter(Submission.session_id == session_id).first()
    if not sub:
        raise APIError(status.HTTP_404_NOT_FOUND, "SESSION_NOT_FOUND", "세션을 찾을 수 없습니다.")
    if sub.user_id != user_id:
        raise APIError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "접근 권한이 없습니다.")
    return sub


def get_llm_client() -> VLLMClient:
    return VLLMClient()


_METRIC_KO = {
    "total_duration": "풀이 시간(초)",
    "attempt_count": "제출 횟수",
    "pivot_count": "재작성(pivot) 횟수",
}


def _build_user_prompt(
    problem: Optional[Problem],
    summary: Optional[SessionSummary],
    pauses: List[PauseEventRow],
    pivots: List[PivotEventRow],
    windows: List[PatternWindowRow],
    latest: Optional[JudgeSubmission],
    baseline: Optional[ProblemBaselineResponse] = None,
) -> str:
    lines: List[str] = []
    lines.append(f"[문제] {problem.title if problem else '알 수 없음'}")

    if summary:
        lines.append(
            "[시간 분포 ms] "
            f"설계(setup)={summary.setup_ms}, 형성(formation)={summary.formation_ms}, "
            f"디버그(debug)={summary.debug_ms}, 다듬기(refine)={summary.refine_ms}, "
            f"총={summary.total_ms}"
        )
        lines.append(f"[정지] 총 {summary.pause_count}회, 누적 {summary.pause_total_ms}ms")
        lines.append(f"[재작성(pivot)] 총 {summary.pivot_count}회")
    else:
        lines.append("[분석 데이터 없음] 아직 행동 분석이 완료되지 않았습니다.")

    if pauses:
        top_pauses = sorted(pauses, key=lambda p: p.duration_ms, reverse=True)[:5]
        pause_desc = "; ".join(
            f"{p.duration_ms}ms(phase={p.phase or '?'}, pattern={p.pattern or '?'})"
            for p in top_pauses
        )
        lines.append(f"[주요 정지 구간] {pause_desc}")

    if pivots:
        pivot_desc = "; ".join(
            f"{p.deleted_chars}자 삭제(type={p.pivot_type or '?'}, pattern={p.pattern or '?'})"
            for p in pivots[:5]
        )
        lines.append(f"[주요 재작성] {pivot_desc}")

    if windows:
        window_parts = []
        for w in windows:
            switches = sum(
                1 for p in pivots if p.pattern == w.pattern and p.pivot_type == _PIVOT_APPROACH_SWITCH
            )
            if switches == 0:
                tag = "원활한 형성"
            elif switches >= _APPROACH_SWITCH_CHURN_THRESHOLD:
                tag = f"접근법을 {switches}회 갈아엎음"
            else:
                tag = None
            desc = f"{w.pattern}({w.formation_ms}ms 형성"
            if tag:
                desc += f", {tag}"
            desc += ")"
            window_parts.append(desc)
        lines.append(f"[구조 패턴 형성 순서] {'; '.join(window_parts)}")

    candidates: List[str] = []
    dominant_label = _dominant([p.pattern for p in pauses])
    if dominant_label and dominant_label in _ADVICE_BANK:
        candidates.extend(_ADVICE_BANK[dominant_label][:_ADVICE_CANDIDATES_PER_KEY])
    dominant_pivot = _dominant([p.pivot_type for p in pivots])
    if dominant_pivot and dominant_pivot in _ADVICE_BANK:
        candidates.extend(_ADVICE_BANK[dominant_pivot][:_ADVICE_CANDIDATES_PER_KEY])
    if candidates:
        lines.append("[조언 후보] " + " / ".join(candidates))

    if latest:
        lines.append(f"[최종 제출] verdict={latest.verdict or '채점 전'}, language={latest.language}")

    if baseline and baseline.metrics:
        lines.append(f"[비교군 기준선] tier={baseline.tier}, 같은 유형 문제를 푼 다른 응시자들과의 비교:")
        for m in baseline.metrics:
            if m.metric not in _METRIC_KO:
                continue  # pause_ms@LABEL 셀은 합성 전용 — 프롬프트에서 제외
            p = m.percentiles
            line = (
                f"  - {_METRIC_KO[m.metric]}: 비교군 p25={p.p25:.0f}, 중앙값={p.p50:.0f}, p75={p.p75:.0f}"
            )
            if m.user_value is not None:
                line += f" / 이번 세션={m.user_value:.0f} (구간 {m.user_band})"
            if m.data_source == "estimated":
                line += " [추정치 기반]"
            lines.append(line)
        lines.append(
            "  비교군 수치는 참고용 분포다. 사용자의 값이 p75보다 크면 상대적으로 오래/많이 걸린 편, "
            "p25보다 작으면 빠른/적은 편으로 언급하되 단정적 평가는 피할 것."
        )

    return "\n".join(lines)


@router.post("", response_model=FeedbackResponse)
async def create_feedback(
    body: FeedbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    llm_client: VLLMClient = Depends(get_llm_client),
):
    sub = _load_owned_session(body.session_id, current_user.user_id, db)

    existing = db.query(Feedback).filter(Feedback.session_id == body.session_id).first()
    if existing:
        return FeedbackResponse(
            feedback_id=existing.feedback_id,
            text=existing.text,
            model_used=existing.model_used,
            generated_at=_iso(existing.generated_at),
        )

    problem = db.query(Problem).filter(Problem.problem_id == sub.problem_id).first()
    summary = db.query(SessionSummary).filter(SessionSummary.sid == body.session_id).first()
    pauses = db.query(PauseEventRow).filter(PauseEventRow.sid == body.session_id).all()
    pivots = db.query(PivotEventRow).filter(PivotEventRow.sid == body.session_id).all()
    windows = db.query(PatternWindowRow).filter(PatternWindowRow.sid == body.session_id).all()
    latest = (
        db.query(JudgeSubmission)
        .filter(JudgeSubmission.session_id == body.session_id)
        .order_by(JudgeSubmission.submitted_at.desc())
        .first()
    )

    # 비교군 기준선: 세션 실측값을 metric 단위에 맞춰 전달
    # (total_duration 기준선은 초 단위 — session_summaries.total_ms를 초로 변환)
    user_values = {}
    if summary:
        user_values["total_duration"] = summary.total_ms / 1000.0
        user_values["pivot_count"] = float(summary.pivot_count)
    n_judged = db.query(JudgeSubmission).filter(JudgeSubmission.session_id == body.session_id).count()
    if n_judged:
        user_values["attempt_count"] = float(n_judged)
    baseline = build_baseline(db, problem, user_values) if problem else None

    user_prompt = _build_user_prompt(problem, summary, pauses, pivots, windows, latest, baseline)

    try:
        text = model_used = None
        last_model = LLM_MODEL
        for attempt in range(_MAX_GROUNDING_RETRIES + 1):
            result = await llm_client.chat(system=_SYSTEM_PROMPT, user=user_prompt)
            last_model = result.model
            grounded, ungrounded_numbers = verify_grounding(result.text, user_prompt)
            if grounded:
                text, model_used = result.text, result.model
                break
            logger.warning(
                "grounding 검증 실패 (session=%s, attempt=%d/%d): 근거 없는 숫자=%s",
                body.session_id, attempt + 1, _MAX_GROUNDING_RETRIES + 1, ungrounded_numbers,
            )
        else:
            text = build_template_feedback(problem, summary, pauses, pivots, windows, latest, baseline)
            model_used = last_model
    except LLMUnavailable:
        text, model_used = _FALLBACK_TEXT, LLM_MODEL

    feedback = Feedback(
        feedback_id=str(uuid.uuid4()),
        session_id=body.session_id,
        text=text,
        model_used=model_used,
        generated_at=datetime.now(tz=UTC),
        rating=None,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)

    return FeedbackResponse(
        feedback_id=feedback.feedback_id,
        text=feedback.text,
        model_used=feedback.model_used,
        generated_at=_iso(feedback.generated_at),
    )


@router.get("", response_model=Optional[FeedbackResponse])
async def get_feedback(
    session_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _load_owned_session(session_id, current_user.user_id, db)

    feedback = db.query(Feedback).filter(Feedback.session_id == session_id).first()
    if not feedback:
        return None

    return FeedbackResponse(
        feedback_id=feedback.feedback_id,
        text=feedback.text,
        model_used=feedback.model_used,
        generated_at=_iso(feedback.generated_at),
    )


@router.patch("/{feedback_id}/rating", response_model=FeedbackRatingResponse)
async def rate_feedback(
    feedback_id: str,
    body: FeedbackRatingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    feedback = db.query(Feedback).filter(Feedback.feedback_id == feedback_id).first()
    if not feedback:
        raise APIError(status.HTTP_404_NOT_FOUND, "FEEDBACK_NOT_FOUND", "피드백을 찾을 수 없습니다.")

    sub = db.query(Submission).filter(Submission.session_id == feedback.session_id).first()
    if not sub or sub.user_id != current_user.user_id:
        raise APIError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "접근 권한이 없습니다.")

    feedback.rating = body.rating
    db.commit()

    return FeedbackRatingResponse(feedback_id=feedback.feedback_id, rating=feedback.rating)
