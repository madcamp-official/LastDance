from sqlalchemy import Column, Integer, String, Boolean, DateTime
from app.database import Base


class Summary(Base):
    __tablename__ = "summaries"

    # 사용자가 모르는 정수 id
    user_id = Column(String, primary_key=True, unique=True, index=True, nullable=False)
    total_submission = Column(Integer, nullable = False)
    total_correct = Column(Integer, nullable = False)
    total_wrong = Column(Integer, nullable = False)
    
