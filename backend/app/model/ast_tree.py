"""세션 전체의 AST 트리 변화 이력 (dev-plan §4.1 Step 5 디바운스 시점 기준).

기존 analysis 모델(`app/model/analysis.py`)은 "규칙 매처가 못 덮은 구간"만
UnmatchedSegmentRow로 남긴다. 즉 세션 중 AST가 실제로 어떻게 자라고 갈아엎혔는지는
UNMATCHED가 아닌 구간에서 통째로 버려진다.

여기서는 매처 실행(디바운스) 시점마다 뜬 구조 스냅샷과 그 사이의 구조 diff를
전 구간 보존한다. 재생(replay) 없이도 세션 종료 후 다음이 가능해진다:
  - 시간축 트리 성장/축소 곡선 (struct_node_count, max_depth)
  - 어느 시점에 어떤 노드 타입이 삽입/삭제/이동됐는지
  - 구조 해시 비교로 "되돌아간" 구간(같은 sketch_hash 재등장) 탐지

멱등성(§8): 워커 재실행 시 sid 기준으로 기존 행을 전부 지우고 다시 쓴다.
원본 코드/식별자 텍스트는 저장하지 않는다 — 노드 타입·해시·크기만 (addendum §3와 동일 원칙).
"""

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, UniqueConstraint

from app.database import Base


class AstTreeEvolutionRow(Base):
    """세션당 1행 — 트리 변화 전체 요약."""

    __tablename__ = "ast_tree_evolutions"

    sid = Column(String, primary_key=True, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    problem_id = Column(Integer, index=True, nullable=False)
    lang = Column(String, nullable=True)
    matcher_version = Column(Integer, nullable=False, default=0)

    snapshot_count = Column(Integer, nullable=False, default=0)
    diff_event_count = Column(Integer, nullable=False, default=0)   # 계산된 총 diff 이벤트 수
    stored_diff_count = Column(Integer, nullable=False, default=0)  # 상한 적용 후 실제 저장 행 수
    truncated = Column(Boolean, nullable=False, default=False)      # 상한에 걸려 일부만 저장됨

    insert_count = Column(Integer, nullable=False, default=0)
    delete_count = Column(Integer, nullable=False, default=0)
    move_count = Column(Integer, nullable=False, default=0)

    first_t_ms = Column(Integer, nullable=False, default=0)
    last_t_ms = Column(Integer, nullable=False, default=0)

    initial_node_count = Column(Integer, nullable=False, default=0)  # 첫 스냅샷 구조 노드 수
    final_node_count = Column(Integer, nullable=False, default=0)
    peak_node_count = Column(Integer, nullable=False, default=0)     # 최대치 (축소 여부 판단용)
    final_max_depth = Column(Integer, nullable=False, default=0)
    final_sketch_hash = Column(String, nullable=False, default="")
    final_shape_json = Column(String, nullable=True)                 # SubtreeShape JSON

    created_at = Column(DateTime(timezone=True), nullable=True)


class AstSnapshotRow(Base):
    """매처 실행(디바운스) 시점 1회당 1행 — 그 시점 트리의 이름-무관 요약."""

    __tablename__ = "ast_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sid = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    problem_id = Column(Integer, index=True, nullable=False)
    seq = Column(Integer, nullable=False)            # 0부터 세션 내 매처 실행 순서
    t_ms = Column(Integer, nullable=False)

    struct_node_count = Column(Integer, nullable=False, default=0)
    max_depth = Column(Integer, nullable=False, default=0)
    sketch_hash = Column(String, nullable=False, default="")       # 트리 전체 구조 해시
    node_type_counts_json = Column(String, nullable=False, default="{}")

    # 직전 스냅샷 대비 변화량
    insert_count = Column(Integer, nullable=False, default=0)
    delete_count = Column(Integer, nullable=False, default=0)
    move_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("sid", "seq", name="uq_ast_snapshot_sid_seq"),)


class AstDiffEventRow(Base):
    """스냅샷 사이의 구조 diff 이벤트 1건당 1행 (insert | delete | move)."""

    __tablename__ = "ast_diff_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sid = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    snapshot_seq = Column(Integer, nullable=False)   # 이 diff를 만들어낸 스냅샷의 seq
    t_ms = Column(Integer, nullable=False)

    op = Column(String, nullable=False)               # insert | delete | move
    node_type = Column(String, nullable=False, default="")
    parent_type = Column(String, nullable=False, default="")
    depth = Column(Integer, nullable=False, default=0)
    subtree_hash = Column(String, nullable=False, default="")
    size_nodes = Column(Integer, nullable=False, default=0)
    callee_is_self = Column(Boolean, nullable=False, default=False)
    from_parent = Column(String, nullable=False, default="")   # move 전용
    to_parent = Column(String, nullable=False, default="")     # move 전용

    __table_args__ = (Index("ix_ast_diff_events_sid_seq", "sid", "snapshot_seq"),)
