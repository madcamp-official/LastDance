"""Stage C — 피드백 작성기 (git-timeline-feedback-spec.md §5).

본인 세션의 논리 단계별 인사이트(problem_feedback_insights) + 같은 문제를 푼 다른
사용자와의 비교군 집계(app/util/cohort.py)를 프롬프트로 조립해 한국어 피드백을 만든다.
AST 패턴/pivot 기반 프롬프트(구 구현)는 폐기됐다 — 조언 문구도 LLM 자유 창작이 아니라
인사이트의 advice 필드에서만 온다 (§5.2 규칙 5, 연구 2 feed-forward 규약 유지).
"""

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.auth import APIError
from app.database import get_db
from app.llm.client import LLM_MODEL, LLMUnavailable, VLLMClient
from app.llm.grounding import build_timeline_template_feedback, verify_grounding
from app.model.analysis import SessionSummary
from app.model.feedback import Feedback
from app.model.insight import ProblemFeedbackInsight
from app.model.problem import Problem
from app.model.session import Submission
from app.model.timeline import CodeCommitRow, SessionSegmentRow
from app.model.user import User
from app.schema.feedback import (
    FeedbackRatingRequest,
    FeedbackRatingResponse,
    FeedbackRequest,
    FeedbackResponse,
)
from app.schema.insight import CohortSummary
from app.util.baseline import build_self_reference
from app.util.cohort import build_cohort, stage_ko
from app.util.security import get_current_user

router = APIRouter(prefix="/feedback", tags=["feedback"])
logger = logging.getLogger("app.api.feedback")

_FALLBACK_TEXT = "피드백 생성 서버에 연결할 수 없어 잠시 후 다시 시도해 주세요."
_MAX_GROUNDING_RETRIES = 2  # dev-plan §7.3 — 최초 1회 + 재시도 2회

# §5.2 시스템 프롬프트 (전문)
_SYSTEM_PROMPT = """당신은 알고리즘 문제 풀이(PS) 연습 플랫폼의 튜터입니다. 사용자가 문제를 푸는 동안의 행동
분석 결과(논리 단계별 인사이트, 시간 분포, 같은 문제를 푼 다른 사용자들과의 비교)가 주어지면,
한국어로 피드백을 작성합니다.

[작성 규칙]
1. 추상적 총평 금지. "디버깅이 오래 걸렸어요", "알고리즘 작성이 느렸어요" 같은 문장을 쓰지
   마세요. 반드시 [인사이트]에 있는 logic_label 수준의 구체성으로 쓰세요.
   좋은 예: "BFS 큐 뼈대는 1분 만에 빠르게 작성하셨지만, 문제 고유의 탐색 정책(벽을 만날
   때까지 미끄러지는 이동)을 조건으로 옮기는 데 78초 정지를 포함해 약 4분을 쓰셨어요.", "처음 접근 방식이
     시간복잡도 O(NlogN)이 맞는지 확인하셨으면 첫 제출에 시간초과가 나지 않았을 거에요."
2. [인사이트]에 없는 사실, [비교군]에 없는 수치를 만들어내지 마세요. 프롬프트에 있는 숫자만
   인용할 수 있습니다.
3. category를 구분해서 서술하세요:
   - stall 인사이트 = 사고 단계에서 막힌 것. "어려워한 부분"으로 표현 가능.
   - churn/debug_loop 인사이트 = 반복 수정. "어려워했다"가 아니라 "사소한 수정(자료형,
     인덱스, 출력 형식 등)에 시간이 샜다"로 표현하세요. 코드 분량이 컸다는 이유로 어려웠다고
     쓰는 것은 금지입니다.
   - smooth 인사이트 = 잘한 점. 피드백 앞부분에 1문장으로 반드시 언급하세요.
4. [비교군] 수치는 참고용 분포입니다. 본인 값이 p75보다 크면 "다른 사용자들보다 오래 걸린
   편", p25보다 작으면 "빠른 편"으로 언급하되 단정적 평가·서열 표현("하위권" 등)은 피하세요.
   "[표본 적음]" 표시가 있으면 "아직 비교 데이터가 적지만" 같은 단서를 붙이세요.
5. 개선 방향은 [인사이트]의 advice 필드에 있는 내용만 사용하고, 자연스러운 문장으로
   녹여내세요. advice가 모두 null이면 관찰된 사실만 서술하고 개선 방향은 생략합니다.
   데이터에 없는 일반론("변수명을 명확히 하세요")을 새로 만들지 마세요.
6. [커밋 로그]는 이 세션의 코드 작성 기록 전체입니다. 각 행의 hunks_json은 그 커밋에서
   바뀐 diff hunk이며, seq 순서대로 누적 적용하면 각 시점의 코드 상태가 됩니다. 코드 전문
   스냅샷은 주어지지 않으므로 필요하면 hunk를 누적해 유추하세요. 커밋 로그에서 인용할 수
   있는 것은 실제로 있는 코드 조각과 숫자(t_ms, pause_before_ms, duration_ms, verdict)뿐이며,
   없는 코드를 상상해 인용하지 마세요. 단 커밋 로그 전체를 보고 문제 접근/논리 흐름/코드의 간결성 등에 피드백할 부분이
   있다면 피드백해도 좋아요.
7. 분량: 4~7문장. 인사이트가 1~2개뿐이면 3~4문장으로 줄이세요.
8. 어조: 존댓말, 과정 중심. 정답 여부 자체를 요약하거나 점수를 평가하지 마세요."""


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


def _user_stage_durations(
    insights: List[ProblemFeedbackInsight],
) -> Dict[Tuple[str, str], int]:
    """본인 세션의 (stage, category)별 소요 시간 합 — 비교군 대조용."""
    agg: Dict[Tuple[str, str], int] = defaultdict(int)
    for i in insights:
        agg[(i.stage, i.category)] += int(i.duration_ms or 0)
    return dict(agg)


def _build_user_prompt(
    problem: Optional[Problem],
    summary: Optional[SessionSummary],
    insights: List[ProblemFeedbackInsight],
    verdict_seq: List[str],
    lang: Optional[str],
    cohort: Optional[CohortSummary],
    self_reference: Optional[dict] = None,
    commits: Optional[List[CodeCommitRow]] = None,
) -> str:
    """§5.3 사용자 프롬프트 템플릿."""
    lines: List[str] = [f"[문제] {problem.title if problem else '알 수 없음'}", ""]

    total_ms = summary.total_ms if summary else 0
    lines.append("[세션 개요]")
    lines.append(
        f"총 소요 {total_ms / 60000:.1f}분, 제출 {len(verdict_seq)}회 "
        f"({' → '.join(verdict_seq) if verdict_seq else '제출 없음'}), 언어 {lang or '알 수 없음'}"
    )
    lines.append("")

    lines.append("[인사이트 — 이 사용자의 이번 세션 분석 결과]")
    if insights:
        for i in insights:
            lines.append(
                f"- ({i.category}/{i.stage}, severity={i.severity}) {i.logic_label}"
            )
            lines.append(
                f"  구간: 세션 {i.t_start_ms / 60000:.0f}분~{i.t_end_ms / 60000:.0f}분 지점, "
                f"{i.duration_ms / 1000:.0f}초 소요"
            )
            lines.append(f"  관찰: {i.description}")
            if i.advice:
                lines.append(f"  개선 후보: {i.advice}")
    else:
        lines.append("- (분석 인사이트가 없습니다. 아래 기록만으로 서술하세요.)")
    lines.append("")

    # 커밋 로그 전체 (§3.1). sid/user_id는 세션 프롬프트에서 중복이므로 제외.
    # snapshot_text도 제외 — hunks_json 누적으로 LLM이 코드 상태를 복원한다.
    lines.append("[커밋 로그 — 이 세션의 코드 작성 기록 전체, seq 순]")
    if commits:
        lines.append(
            "형식: seq | kind | t_ms | pause_before_ms | duration_ms | verdict | hunks_json"
        )
        for c in commits:
            lines.append(
                f"- {c.seq} | {c.kind} | {c.t_ms} | {c.pause_before_ms} | "
                f"{c.duration_ms} | {c.verdict or '-'} | {c.hunks_json}"
            )
    else:
        lines.append("- (커밋 기록이 없습니다.)")
    lines.append("")

    if cohort and cohort.exposable:
        lines.append(f"[비교군 — 이 문제를 푼 다른 사용자 {cohort.session_count}명 기준]")
        for s in cohort.stages:
            user_part = (
                f" / 이번 세션 {s.user_duration_ms / 1000:.0f}초"
                if s.user_duration_ms is not None
                else " / 이번 세션 해당 단계 기록 없음"
            )
            small = " [표본 적음]" if s.small_sample else ""
            lines.append(
                f"- {stage_ko(s.stage)}({s.top_logic_label}): 소요 시간 "
                f"p25={s.p25 / 1000:.0f}초, 중앙값={s.p50 / 1000:.0f}초, p75={s.p75 / 1000:.0f}초"
                f"{user_part}{small}"
            )
            lines.append(
                f"  (이 문제 응시자의 {s.occurrence_rate:.0%}가 같은 단계에서 막힘)"
            )
        if cohort.total_ms_p50 is not None:
            lines.append(
                f"- 전체 풀이 시간: 중앙값 {cohort.total_ms_p50 / 60000:.1f}분 "
                f"/ 이번 세션 {total_ms / 60000:.1f}분"
            )
        if cohort.attempt_count_p50 is not None:
            lines.append(
                f"- 제출 횟수: 중앙값 {cohort.attempt_count_p50:.0f}회 "
                f"/ 이번 세션 {len(verdict_seq)}회"
            )
    else:
        lines.append(
            "[비교군] 아직 이 문제의 비교 데이터가 충분하지 않습니다. "
            "비교 언급 없이 본인 기록만 서술하세요."
        )
    lines.append("")

    # 연구 5 자기참조 규약: 본인 값이 중앙값보다 나쁠 때만 과거 세션 추세를 노출
    self_reference = self_reference or {}
    worse_than_median = (
        cohort is not None
        and cohort.total_ms_p50 is not None
        and total_ms > cohort.total_ms_p50
    )
    if worse_than_median and "total_duration" in self_reference:
        prev_avg, n_sessions = self_reference["total_duration"]
        cur_sec = total_ms / 1000.0
        if cur_sec < prev_avg * 0.95:
            direction = "감소"
        elif cur_sec > prev_avg * 1.05:
            direction = "증가"
        else:
            direction = "비슷"
        lines.append(
            f"[자기 참조] 최근 {n_sessions}회 세션 평균 풀이 시간 {prev_avg / 60:.1f}분 대비 "
            f"이번 세션 {total_ms / 60000:.1f}분 ({direction})"
        )
        lines.append("")

    lines.append("위 데이터로 피드백을 작성하세요.")
    return "\n".join(lines)


def _commit_rows(db: Session, session_id: str) -> List[CodeCommitRow]:
    """세션의 커밋 로그 전체 (§3.1). SUBMIT 레코드가 verdict 권위 소스 (§2.1)."""
    return (
        db.query(CodeCommitRow)
        .filter(CodeCommitRow.sid == session_id)
        .order_by(CodeCommitRow.seq.asc())
        .all()
    )


def _verdict_seq(commits: List[CodeCommitRow]) -> List[str]:
    return [c.verdict or "PENDING" for c in commits if c.kind == "submit"]


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
    insights = (
        db.query(ProblemFeedbackInsight)
        .filter(
            ProblemFeedbackInsight.sid == body.session_id,
            ProblemFeedbackInsight.status == "valid",
        )
        .order_by(ProblemFeedbackInsight.t_start_ms.asc())
        .all()
    )
    segments = (
        db.query(SessionSegmentRow)
        .filter(SessionSegmentRow.sid == body.session_id)
        .order_by(SessionSegmentRow.commit_start_seq.asc())
        .all()
    )
    commits = _commit_rows(db, body.session_id)
    verdict_seq = _verdict_seq(commits)

    cohort = build_cohort(
        db,
        problem_id=sub.problem_id,
        exclude_sid=body.session_id,
        user_durations=_user_stage_durations(insights),
        user_total_ms=summary.total_ms if summary else None,
        user_attempt_count=len(verdict_seq),
    )
    self_reference = (
        build_self_reference(db, current_user.user_id, summary.tier, body.session_id)
        if summary
        else {}
    )

    user_prompt = _build_user_prompt(
        problem, summary, insights, verdict_seq, sub.language, cohort,
        self_reference=self_reference, commits=commits,
    )

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
            # §5.4: 인사이트 기반 템플릿 폴백 (인사이트 0개면 세그먼트 통계로)
            text = build_timeline_template_feedback(insights, segments, summary)
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
