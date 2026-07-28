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
    difficulty: Optional[str] = None
    solved_at: Optional[str] = None


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
    difficulty: Optional[str] = None


# GET /problems/{problem_id}/stats 응답은 app/schema/baseline.py의 ProblemBaselineResponse 사용.
