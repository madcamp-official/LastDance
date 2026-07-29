import uuid
from datetime import UTC, datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.auth import APIError
from app.database import get_db
from app.model.problem import Problem
from app.model.session import Submission
from app.model.submission import JudgeSubmission
from app.model.user import User
from app.schema.session import (
    SessionDetailResponse,
    SessionEndRequest,
    SessionEndResponse,
    SessionStartRequest,
    SessionStartResponse,
    UserSessionListItem,
    UserSessionListResponse,
)
from app.util.security import get_current_user

router = APIRouter(prefix="/sessions", tags=["sessions"])
users_router = APIRouter(prefix="/users/me/sessions", tags=["sessions"])


def _iso(dt: Optional[datetime]) -> Optional[str]:
    # 저장된 UTC datetime을 ISO8601 "...Z" 문자열로. None이면 그대로 None.
    if dt is None:
        return None
    # SQLite는 DateTime(timezone=True)라도 round-trip 후 tzinfo를 잃는다(naive가 됨).
    # 이 컬럼은 항상 UTC로만 채워지므로 naive면 UTC로 간주.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
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

    # 같은 유저/문제에 이미 active(final_status None) 세션 있으면 새로 만들지 않고 재사용.
    sub = (
        db.query(Submission)
        .filter(
            Submission.user_id == current_user.user_id,
            Submission.problem_id == problem.problem_id,
            Submission.final_status.is_(None),
        )
        .order_by(Submission.started_at.desc())
        .first()
    )

    if sub is None:
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


@users_router.get("", response_model=UserSessionListResponse)
async def list_my_sessions(
    problem_id: Optional[int] = Query(None),
    status_filter: Optional[Literal["active", "solved", "abandoned"]] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Submission).filter(Submission.user_id == current_user.user_id)

    if problem_id is not None:
        query = query.filter(Submission.problem_id == problem_id)

    if status_filter == "active":
        query = query.filter(Submission.final_status.is_(None))
    elif status_filter is not None:
        query = query.filter(Submission.final_status == status_filter)

    total_count = query.count()
    rows = (
        query.order_by(Submission.started_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    problem_ids = {r.problem_id for r in rows}
    problems_by_id = (
        {p.problem_id: p for p in db.query(Problem).filter(Problem.problem_id.in_(problem_ids)).all()}
        if problem_ids
        else {}
    )

    # 세션별 가장 최근 judge_submissions 1건(있으면). 페이지 크기만큼만 조회하면 되므로 파이썬에서 max 계산.
    session_ids = [r.session_id for r in rows]
    latest_by_session: dict[str, JudgeSubmission] = {}
    if session_ids:
        for js in db.query(JudgeSubmission).filter(JudgeSubmission.session_id.in_(session_ids)).all():
            cur = latest_by_session.get(js.session_id)
            if cur is None or js.submitted_at > cur.submitted_at:
                latest_by_session[js.session_id] = js

    items = []
    for r in rows:
        problem = problems_by_id.get(r.problem_id)
        latest = latest_by_session.get(r.session_id)
        items.append(
            UserSessionListItem(
                session_id=r.session_id,
                problem_id=r.problem_id,
                problem_title=problem.title if problem else "",
                difficulty=problem.difficulty if problem else None,
                language=r.language,
                status=r.final_status if r.final_status is not None else "active",
                started_at=_iso(r.started_at),
                ended_at=_iso(r.ended_at),
                latest_verdict=latest.verdict if latest else None,
                latest_submitted_at=_iso(latest.submitted_at) if latest else None,
            )
        )

    return UserSessionListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total_count=total_count,
    )
