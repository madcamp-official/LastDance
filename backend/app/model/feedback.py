from sqlalchemy import Column, DateTime, String
from app.database import Base


class Feedback(Base):
    __tablename__ = "feedbacks"

    feedback_id = Column(String, primary_key=True, unique=True, index=True, nullable=False)
    session_id = Column(String, unique=True, index=True, nullable=False)
    text = Column(String, nullable=False)
    model_used = Column(String, nullable=False)
    generated_at = Column(DateTime(timezone=True), nullable=False)
    rating = Column(String, nullable=True)  # "up" | "down" | None
