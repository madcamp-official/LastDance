from typing import Literal

from pydantic import BaseModel


# ---- POST /feedback ----
class FeedbackRequest(BaseModel):
    session_id: str


class FeedbackResponse(BaseModel):
    feedback_id: str
    text: str
    model_used: str
    generated_at: str


# ---- PATCH /feedback/{feedback_id}/rating ----
class FeedbackRatingRequest(BaseModel):
    rating: Literal["up", "down"]


class FeedbackRatingResponse(BaseModel):
    feedback_id: str
    rating: str
