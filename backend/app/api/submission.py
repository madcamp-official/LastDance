import uuid
from datetime import UTC, datetime
from typing import List

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.auth import APIError
from app.database import get_db
from app.judge.client import Judge0Client, Judge0Unavailable
from app.judge.engine import Judge0InternalError, judge_submission
from app.judge.languages import UnsupportedLanguage
from app.judge.testcases import TestcasesNotFound
from app.model.problem import Problem
from app.model.session import Submission as SessionRow
from app.model.submission import JudgeSubmission
from app.model.user import User
from app.schema.submission import (
    CreateSubmissionRequest,
    CreateSubmissionResponse,
    SubmissionDetailResponse,
    SubmissionListItem,
    SubmissionListResponse,
)
from app.util.security import get_current_user

router = APIRouter(prefix="/submissions", tags=["submissions"])


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat().replace("+00:00", "Z")


def get_judge_client() -> Judge0Client:
    return Judge0Client()


def _load_owned_session(session_id: str, user_id: str, db: Session) -> SessionRow:
    sub = db.query(SessionRow).filter(SessionRow.session_id == session_id).first()
    if not sub:
        raise APIError(status.HTTP_404_NOT_FOUND, "SESSION_NOT_FOUND", "세션을 찾을 수 없습니다.")
    if sub.user_id != user_id:
        raise APIError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "접근 권한이 없습니다.")
    return sub


@router.post("", response_model=CreateSubmissionResponse, status_code=status.HTTP_200_OK)
async def create_submission(
    body: CreateSubmissionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    judge_client: Judge0Client = Depends(get_judge_client),
):
    session_row = _load_owned_session(body.session_id, current_user.user_id, db)
    if session_row.final_status is not None:
        raise APIError(
            status.HTTP_409_CONFLICT, "SESSION_ALREADY_ENDED", "이미 종료된 세션입니다."
        )
    if body.problem_id != session_row.problem_id:
        raise APIError(
            status.HTTP_400_BAD_REQUEST, "PROBLEM_ID_MISMATCH", "세션의 문제와 일치하지 않습니다."
        )

    problem = db.query(Problem).filter(Problem.problem_id == body.problem_id).first()
    if not problem:
        raise APIError(status.HTTP_404_NOT_FOUND, "PROBLEM_NOT_FOUND", "문제를 찾을 수 없습니다.")
    if not problem.testcase_dir:
        raise APIError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "PROBLEM_TESTCASES_NOT_CONFIGURED",
            "문제에 채점용 테스트케이스 경로가 설정되어 있지 않습니다.",
        )

    try:
        outcome = judge_submission(
            code=body.code,
            language=body.language,
            problem_source=problem.testcase_dir,
            client=judge_client,
        )
    except UnsupportedLanguage:
        raise APIError(
            status.HTTP_400_BAD_REQUEST, "LANGUAGE_NOT_SUPPORTED", "지원하지 않는 언어입니다."
        )
    except TestcasesNotFound:
        raise APIError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "TESTCASES_NOT_FOUND",
            "문제의 테스트케이스를 찾을 수 없습니다.",
        )
    except Judge0Unavailable:
        raise APIError(
            status.HTTP_502_BAD_GATEWAY, "JUDGE_UNAVAILABLE", "채점 서버에 연결할 수 없습니다."
        )
    except Judge0InternalError:
        raise APIError(
            status.HTTP_502_BAD_GATEWAY, "JUDGE_INTERNAL_ERROR", "채점 중 내부 오류가 발생했습니다."
        )

    now = datetime.now(tz=UTC)
    record = JudgeSubmission(
        submission_id=str(uuid.uuid4()),
        session_id=session_row.session_id,
        problem_id=body.problem_id,
        user_id=current_user.user_id,
        language=body.language,
        code=body.code,
        status="judged",
        verdict=outcome.verdict,
        runtime_ms=outcome.runtime_ms,
        memory_kb=outcome.memory_kb,
        submitted_at=now,
    )
    db.add(record)

    # 맞으면 세션을 그대로 종료(해결), 틀리면 세션은 계속 진행 — 기록만 남긴다.
    if outcome.verdict == "AC":
        session_row.final_status = "solved"
        session_row.language = body.language
        session_row.ended_at = now

    db.commit()

    return CreateSubmissionResponse(
        submission_id=record.submission_id,
        status=record.status,
        submitted_at=_iso(record.submitted_at),
    )


@router.get("/{submission_id}", response_model=SubmissionDetailResponse)
async def get_submission(
    submission_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = (
        db.query(JudgeSubmission).filter(JudgeSubmission.submission_id == submission_id).first()
    )
    if not record:
        raise APIError(status.HTTP_404_NOT_FOUND, "SUBMISSION_NOT_FOUND", "제출을 찾을 수 없습니다.")
    if record.user_id != current_user.user_id:
        raise APIError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "접근 권한이 없습니다.")

    return SubmissionDetailResponse(
        submission_id=record.submission_id,
        status=record.status,
        verdict=record.verdict,
        runtime_ms=record.runtime_ms,
        memory_kb=record.memory_kb,
        submitted_at=_iso(record.submitted_at),
        code=record.code,
        lang=record.language,
    )


@router.get("", response_model=SubmissionListResponse)
async def list_submissions(
    session_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _load_owned_session(session_id, current_user.user_id, db)

    records: List[JudgeSubmission] = (
        db.query(JudgeSubmission)
        .filter(JudgeSubmission.session_id == session_id)
        .order_by(JudgeSubmission.submitted_at.asc())
        .all()
    )
    return SubmissionListResponse(
        items=[
            SubmissionListItem(
                submission_id=r.submission_id,
                status=r.status,
                verdict=r.verdict,
                submitted_at=_iso(r.submitted_at),
            )
            for r in records
        ]
    )
