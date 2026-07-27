import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.auth import APIError
from app.database import get_db
from app.model.feedback import Feedback
from app.model.session import Submission
from app.model.user import User
from app.schema.feedback import (
    FeedbackRatingRequest,
    FeedbackRatingResponse,
    FeedbackRequest,
    FeedbackResponse,
)
from app.util.security import get_current_user

router = APIRouter(prefix="/feedback", tags=["feedback"])

# 팀A 프롬프트 설계 완료 전까지 mock 고정 문구 (api-spec.md 참고).
# text/model_used만 실제 생성 로직으로 교체 예정, 응답 필드 구조는 유지.
_MOCK_TEXT = "(mock) 아직 준비 중인 피드백입니다."
_MOCK_MODEL = "qwen2.5-coder:7b"


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


@router.post("", response_model=FeedbackResponse)
async def create_feedback(
    body: FeedbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _load_owned_session(body.session_id, current_user.user_id, db)

    feedback = Feedback(
        feedback_id=str(uuid.uuid4()),
        session_id=body.session_id,
        text=_MOCK_TEXT,
        model_used=_MOCK_MODEL,
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
