from typing import List, Literal, Optional

from pydantic import BaseModel

from app.schema.problem import Example


# ---- POST /sessions (세션 시작) ----
class SessionStartRequest(BaseModel):
    problem_id: int


class SessionStartResponse(BaseModel):
    session_id: str
    problem_id: int
    user_id: str
    title: str
    statement: str
    constraints: Optional[str] = None
    examples: List[Example] = []
    source: Optional[str] = None


# ---- PATCH /sessions/{session_id} (세션 종료/제출) ----
class SessionEndRequest(BaseModel):
    status: Literal["solved", "abandoned"]
    language: Optional[str] = None


class SessionEndResponse(BaseModel):
    session_id: str
    status: str


# ---- GET /sessions/{session_id} (세션 조회) ----
class SessionDetailResponse(BaseModel):
    session_id: str
    user_id: str
    problem_id: int
    language: Optional[str] = None
    started_at: str
    ended_at: Optional[str] = None
    status: str  # "active" | "solved" | "abandoned"


# ---- GET /users/me/sessions (유저 기준 세션 목록) ----
class UserSessionListItem(BaseModel):
    session_id: str
    problem_id: int
    problem_title: str
    difficulty: Optional[str] = None
    language: Optional[str] = None
    status: str  # "active" | "solved" | "abandoned"
    started_at: str
    ended_at: Optional[str] = None
    latest_verdict: Optional[str] = None
    latest_submitted_at: Optional[str] = None


class UserSessionListResponse(BaseModel):
    items: List[UserSessionListItem]
    page: int
    page_size: int
    total_count: int
