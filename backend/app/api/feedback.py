import uuid
from datetime import UTC, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.auth import APIError
from app.database import get_db
from app.llm.client import LLM_MODEL, LLMUnavailable, VLLMClient
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

_FALLBACK_TEXT = "피드백 생성 서버에 연결할 수 없어 잠시 후 다시 시도해 주세요."

_SYSTEM_PROMPT = (
    "당신은 코딩 테스트 연습 플랫폼의 튜터입니다. 응시자가 문제를 푸는 동안의 "
    "행동 데이터(정지, 재작성, 구조 패턴 형성 순서 등)를 보고 피드백을 작성합니다. "
    "정답 여부나 채점 결과 자체를 요약하지 말고, 문제를 풀어나간 '과정'(어디서 오래 "
    "멈췄는지, 어느 기능을 구현하는 데 어려움이 있었는지, 접근 순서가 어땠는지)에 집중해서 한국어로 "
    "4~6문장의 바람직한 개선 방향을 포함하여 피드백을 작성하되 코드가 너무 짧거나 길면 그에 맞춰서 유연하게 "
    "피드백 길이를 조정하세요. 또한 사용자의 입력 데이터에 없는 사실은 언급하지 마세요."
)


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
        window_desc = "; ".join(f"{w.pattern}({w.formation_ms}ms 형성)" for w in windows)
        lines.append(f"[구조 패턴 형성 순서] {window_desc}")

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
        result = await llm_client.chat(system=_SYSTEM_PROMPT, user=user_prompt)
        text, model_used = result.text, result.model
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
