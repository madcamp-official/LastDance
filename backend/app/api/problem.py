from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.auth import APIError
from app.database import get_db
from app.model.problem import Problem
from app.model.submission import JudgeSubmission
from app.model.user import User
from app.schema.baseline import ProblemBaselineResponse
from app.schema.problem import (
    ProblemDetailResponse,
    ProblemListItem,
    ProblemListResponse,
)
from app.util.cohort import build_cohort_baseline
from app.util.security import get_current_user_optional

router = APIRouter(prefix="/problems", tags=["problems"])

_VALID_DIFFICULTIES = {"A", "B", "C", "D", "E", "F", "G"}


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat().replace("+00:00", "Z")


@router.get("", response_model=ProblemListResponse)
async def list_problems(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort: str = Query("difficulty_asc"),
    difficulty: Optional[str] = Query(None),
    exclude_solved: bool = Query(False),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    query = db.query(Problem)

    if difficulty:
        tiers = {d.strip().upper() for d in difficulty.split(",") if d.strip()} & _VALID_DIFFICULTIES
        if tiers:
            query = query.filter(Problem.difficulty.in_(tiers))

    # 인증된 유저의 문제별 최신 AC 제출 시각 (exclude_solved/solved_at 둘 다에 씀)
    solved_at_by_problem = {}
    if current_user is not None:
        ac_rows = (
            db.query(JudgeSubmission.problem_id, func.max(JudgeSubmission.submitted_at))
            .filter(
                JudgeSubmission.user_id == current_user.user_id,
                JudgeSubmission.verdict == "AC",
            )
            .group_by(JudgeSubmission.problem_id)
            .all()
        )
        solved_at_by_problem = {problem_id: submitted_at for problem_id, submitted_at in ac_rows}

        if exclude_solved and solved_at_by_problem:
            query = query.filter(~Problem.problem_id.in_(solved_at_by_problem.keys()))

    if sort == "difficulty_desc":
        query = query.order_by(Problem.difficulty.desc(), Problem.problem_id)
    elif sort == "problem_id":
        query = query.order_by(Problem.problem_id)
    else:
        query = query.order_by(Problem.difficulty.asc(), Problem.problem_id)

    total_count = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()

    items = [
        ProblemListItem(
            problem_id=p.problem_id,
            title=p.title,
            difficulty=p.difficulty,
            solved_at=_iso(solved_at_by_problem[p.problem_id]) if p.problem_id in solved_at_by_problem else None,
        )
        for p in rows
    ]
    return ProblemListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total_count=total_count,
    )


@router.get("/{problem_id}", response_model=ProblemDetailResponse)
async def get_problem(problem_id: int, db: Session = Depends(get_db)):
    problem = db.query(Problem).filter(Problem.problem_id == problem_id).first()
    if not problem:
        raise APIError(
            status.HTTP_404_NOT_FOUND,
            "PROBLEM_NOT_FOUND",
            "문제를 찾을 수 없습니다.",
        )

    return ProblemDetailResponse(
        problem_id=problem.problem_id,
        title=problem.title,
        statement=problem.statement,
        constraints=problem.constraints,
        examples=problem.examples or [],
        source=problem.source,
        difficulty=problem.difficulty,
        time_limit=problem.time_limit,
        memory_limit=problem.memory_limit,
    )


@router.get("/{problem_id}/stats", response_model=ProblemBaselineResponse)
async def get_problem_stats(problem_id: int, db: Session = Depends(get_db)):
    # 다른 응시자 대비 통계 — 같은 문제를 푼 실제 세션의 인사이트 집계
    # (git-timeline-feedback-spec.md §5.1, §6). 합성 기준선(baseline_cell) 의존 제거.
    # metrics가 비어 있으면 표본 부족(n<5)으로 "비교 불가" 처리.
    problem = db.query(Problem).filter(Problem.problem_id == problem_id).first()
    if not problem:
        raise APIError(
            status.HTTP_404_NOT_FOUND,
            "PROBLEM_NOT_FOUND",
            "문제를 찾을 수 없습니다.",
        )

    return build_cohort_baseline(db, problem_id)
