import uuid
from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.auth import APIError
from app.database import get_db
from app.model.problem import Problem
from app.model.session import Submission
from app.model.user import User
from app.schema.session import (
    SessionDetailResponse,
    SessionEndRequest,
    SessionEndResponse,
    SessionStartRequest,
    SessionStartResponse,
)
from app.util.security import get_current_user

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _iso(dt: Optional[datetime]) -> Optional[str]:
    # 저장된 UTC datetime을 ISO8601 "...Z" 문자열로. None이면 그대로 None.
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")


def _load_session(session_id: str, db: Session) -> Submission:
    # 존재 확인만.
    sub = db.query(Submission).filter(Submission.session_id == session_id).first()
    if not sub:
        raise APIError(status.HTTP_404_NOT_FOUND, "SESSION_NOT_FOUND", "세션을 찾을 수 없습니다.")
    return sub


@router.post("", response_model=SessionStartResponse)
async def start_session(
    body: SessionStartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    problem = db.query(Problem).filter(Problem.problem_id == body.problem_id).first()
    if not problem:
        raise APIError(status.HTTP_404_NOT_FOUND, "PROBLEM_NOT_FOUND", "문제를 찾을 수 없습니다.")

    sub = Submission(
        session_id=str(uuid.uuid4()),
        problem_id=problem.problem_id,
        user_id=current_user.user_id,
        language=None,
        started_at=datetime.now(tz=UTC),
        ended_at=None,
        final_status=None,  # 진행 중 = active (ended_at으로 판별)
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)

    return SessionStartResponse(
        session_id=sub.session_id,
        problem_id=problem.problem_id,
        user_id=sub.user_id,
        title=problem.title,
        statement=problem.statement,
        constraints=problem.constraints,
        examples=problem.examples or [],
        source=problem.source,
    )


@router.patch("/{session_id}", response_model=SessionEndResponse)
async def end_session(
    session_id: str,
    body: SessionEndRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sub = _load_session(session_id, db)
    if sub.user_id != current_user.user_id:
        raise APIError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "접근 권한이 없습니다.")
    # 이미 종료된 세션은 불변. 어떤 수정도 거부.
    if sub.final_status is not None:
        raise APIError(
            status.HTTP_409_CONFLICT, "SESSION_ALREADY_ENDED", "이미 종료된 세션입니다."
        )

    sub.final_status = body.status
    if body.language is not None:
        sub.language = body.language
    sub.ended_at = datetime.now(tz=UTC)
    db.commit()

    return SessionEndResponse(session_id=sub.session_id, status=sub.final_status)


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sub = _load_session(session_id, db)
    # 종료된 세션(final_status 확정)은 누구나 조회 가능. 진행 중이면 소유자만.
    if sub.final_status is None and sub.user_id != current_user.user_id:
        raise APIError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "접근 권한이 없습니다.")

    current_status = sub.final_status if sub.final_status is not None else "active"
    return SessionDetailResponse(
        session_id=sub.session_id,
        user_id=sub.user_id,
        problem_id=sub.problem_id,
        language=sub.language,
        started_at=_iso(sub.started_at),
        ended_at=_iso(sub.ended_at),
        status=current_status,
    )
