from sqlalchemy import Column, Integer, String, JSON
from app.database import Base


class Problem(Base):
    __tablename__ = "problems"

    # 사용자가 모르는 정수 id (api-spec.md: problem_id는 integer)
    problem_id = Column(Integer, primary_key=True, index=True, nullable=False)
    title = Column(String, index=True, nullable=False)
    statement = Column(String, nullable=False)
    constraints = Column(String, nullable=True)
    examples = Column(JSON, nullable=True)  # [{"input": "...", "output": "..."}]
    source = Column(String, nullable=True)
