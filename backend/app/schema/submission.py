from typing import List, Literal, Optional

from pydantic import BaseModel

SubmissionStatus = Literal["pending", "judged"]
SubmissionVerdict = Literal["AC", "WA", "TLE", "RE", "CE"]


# ---- POST /submissions ----
class CreateSubmissionRequest(BaseModel):
    session_id: str
    problem_id: int
    code: str
    language: str


class CreateSubmissionResponse(BaseModel):
    submission_id: str
    status: SubmissionStatus
    submitted_at: str


# ---- GET /submissions/{submission_id} ----
class SubmissionDetailResponse(BaseModel):
    submission_id: str
    status: SubmissionStatus
    verdict: Optional[SubmissionVerdict] = None
    runtime_ms: Optional[int] = None
    memory_kb: Optional[int] = None
    submitted_at: str


# ---- GET /submissions?session_id={id} ----
class SubmissionListItem(BaseModel):
    submission_id: str
    status: SubmissionStatus
    verdict: Optional[SubmissionVerdict] = None
    submitted_at: str


class SubmissionListResponse(BaseModel):
    items: List[SubmissionListItem]
