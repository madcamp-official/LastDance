from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.auth import APIError
from app.database import get_db
from app.model.problem import Problem
from app.schema.problem import (
    ProblemDetailResponse,
    ProblemListItem,
    ProblemListResponse,
)

router = APIRouter(prefix="/problems", tags=["problems"])


@router.get("", response_model=ProblemListResponse)
async def list_problems(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    total_count = db.query(Problem).count()
    rows = (
        db.query(Problem)
        .order_by(Problem.problem_id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [ProblemListItem(problem_id=p.problem_id, title=p.title) for p in rows]
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
    )
