from typing import List, Literal, Optional

from pydantic import BaseModel

from app.schema.analysis import EditOp

# dev-plan §2.3 / api-spec.md "실시간 이벤트 수집 (Ingest Gateway)" WS 프로토콜.
# 배치당 크기 상한(§3.1): 이벤트 500개, 64 KB.
MAX_BATCH_EVENTS = 500
MAX_BATCH_BYTES = 64 * 1024


# ---- Client -> Server ----
class SessionStartMessage(BaseModel):
    type: Literal["session.start"]
    sid: str
    problem_id: int
    lang: str
    client_ts: int
    editor: str
    initial_code: str = ""


class EditBatchMessage(BaseModel):
    type: Literal["edit.batch"]
    sid: str
    seq: int
    base_t: int
    ops: List[EditOp]


class HeartbeatMessage(BaseModel):
    type: Literal["session.heartbeat"]
    sid: str
    t: int
    cursor: int


class SubmissionMarkMessage(BaseModel):
    type: Literal["submission.mark"]
    sid: str
    t: int
    submission_id: str


class SessionEndMessage(BaseModel):
    type: Literal["session.end"]
    sid: str
    t: int
    reason: Literal["submitted_ac", "closed", "timeout"]


# ---- Server -> Client ----
class AckMessage(BaseModel):
    type: Literal["ack"] = "ack"
    sid: str
    seq: int


class ResumeMessage(BaseModel):
    type: Literal["resume"] = "resume"
    sid: str
    last_seq: int


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    sid: str
    code: str
    seq: Optional[int] = None


# ---- POST /events/beacon (WS 불가/탭 종료 시 폴백. edit.batch와 동일 payload) ----
class BeaconRequest(BaseModel):
    seq: int
    base_t: int
    ops: List[EditOp]
