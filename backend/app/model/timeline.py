"""git 방식 타임라인 영속화 (git-timeline-feedback-spec.md §3.1~§3.2).

멱등성: 기존 규약대로 워커 재실행 시 sid 기준 삭제 후 재삽입
(app/worker/store.py save_timeline).
"""

from sqlalchemy import Column, Integer, String, UniqueConstraint

from app.database import Base


class CodeCommitRow(Base):
    """세션별 git 방식 코드 작성 기록 (§3.1). 커밋 1개 = 1행, 제출도 같은 시퀀스 공간."""

    __tablename__ = "code_commits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sid = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    problem_id = Column(Integer, index=True, nullable=False)
    seq = Column(Integer, nullable=False)              # sid 내 순서, (sid, seq) 유니크
    kind = Column(String, nullable=False)              # "edit" | "submit"
    t_ms = Column(Integer, nullable=False)
    pause_before_ms = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=False, default=0)
    hunks_json = Column(String, nullable=False, default="[]")
    verdict = Column(String, nullable=True)            # kind="submit"
    lines_added = Column(Integer, nullable=False, default=0)
    lines_deleted = Column(Integer, nullable=False, default=0)
    lines_modified = Column(Integer, nullable=False, default=0)
    net_lines = Column(Integer, nullable=False, default=0)
    churn_lines = Column(Integer, nullable=False, default=0)
    snapshot_hash = Column(String, nullable=False, default="")
    snapshot_text = Column(String, nullable=True)      # 제출·종료 시점만
    timeline_version = Column(Integer, nullable=False)

    __table_args__ = (UniqueConstraint("sid", "seq", name="uq_code_commits_sid_seq"),)


class SessionSegmentRow(Base):
    """결정론적 라벨 구간 (§3.2). LLM은 이 라벨 구분을 뒤집을 수 없다."""

    __tablename__ = "session_segments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sid = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    problem_id = Column(Integer, index=True, nullable=False)
    seg_id = Column(String, nullable=False)
    # STALL_SUSPECT | HIGH_CHURN | DEBUG_LOOP | BURST_WRITE | STEADY
    label = Column(String, nullable=False)
    commit_start_seq = Column(Integer, nullable=False)
    commit_end_seq = Column(Integer, nullable=False)
    t_start_ms = Column(Integer, nullable=False)
    t_end_ms = Column(Integer, nullable=False)
    pause_ms = Column(Integer, nullable=False, default=0)
    lines_touched = Column(Integer, nullable=False, default=0)
    net_lines = Column(Integer, nullable=False, default=0)
    timeline_version = Column(Integer, nullable=False)
