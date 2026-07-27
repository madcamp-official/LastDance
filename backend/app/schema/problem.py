from typing import List, Optional

from pydantic import BaseModel


# ---- 공통 ----
class Example(BaseModel):
    input: str
    output: str


# ---- GET /problems (목록) ----
class ProblemListItem(BaseModel):
    problem_id: int
    title: str


class ProblemListResponse(BaseModel):
    items: List[ProblemListItem]
    page: int
    page_size: int
    total_count: int


# ---- GET /problems/{problem_id} (상세) ----
class ProblemDetailResponse(BaseModel):
    problem_id: int
    title: str
    statement: str
    constraints: Optional[str] = None
    examples: List[Example] = []
    source: Optional[str] = None


# ---- GET /problems/{problem_id}/stats (비교 통계, 미구현 — 자리표시자) ----
class ProblemStatsResponse(BaseModel):
    problem_id: int
    message: str
