"""비교군 적재 파이프라인 테이블 매핑 (scripts/01_schema_init.py가 생성, app-db-ingestion-spec.md §1).

원본 정규화(problem/submission)는 AtCoder 원본 TEXT id('pXXXXX') 기준 — 앱의 problems(정수 PK)와는
problems.testcase_dir == baseline_problem.problem_id 로 연결한다.
테이블은 스크립트가 raw SQL로 만들므로 여기 매핑은 조회/수정용이며, create_all은 기존 테이블을 건너뛴다.
"""

from sqlalchemy import Column, Float, Integer, String

from app.database import Base


class BaselineProblem(Base):
    """AtCoder 원본 문제 (tier=A~G, 클러스터링 결과 포함)."""

    __tablename__ = "problem"

    problem_id = Column(String, primary_key=True)          # 예: "p02537"
    tier = Column(String, nullable=True)                   # 'A'~'G'
    cluster_id = Column(Integer, nullable=True)            # NULL이면 tier 단독 fallback
    ac_sample_count = Column(Integer, nullable=False, default=0)


class ArchivedSubmission(Base):
    """AtCoder 원본 제출 메타데이터 (코드 원문은 code_path의 JSON 파일 참조)."""

    __tablename__ = "submission"

    submission_id = Column(String, primary_key=True)
    problem_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    date_raw = Column(String, nullable=False)
    ts_resolved = Column(Integer, nullable=True)           # unix seconds
    ts_resolution = Column(String, nullable=False)         # 'second' | 'ms' | 'day' | 'unknown'
    seq_in_problem = Column(Integer, nullable=True)
    language = Column(String, nullable=False)
    status = Column(String, nullable=False)                # 'AC' | 'WA' | 'TLE' | ...
    cpu_time = Column(Float, nullable=True)                # 채점 실행 시간(ms) — 풀이 시간 아님
    memory = Column(Float, nullable=True)
    code_size = Column(Integer, nullable=True)
    accuracy = Column(Float, nullable=True)
    code_path = Column(String, nullable=True)
    code_blob = Column(String, nullable=True)
    analysis_level = Column(String, nullable=False, default="timing_only")


class AttemptSummary(Base):
    """(user, problem) 풀이 요약. is_synthetic=1이면 합성 비교군 행(user_id=synth_user_id)."""

    __tablename__ = "attempt_summary"

    user_id = Column(String, primary_key=True)
    problem_id = Column(String, primary_key=True)
    tier = Column(String, nullable=True)
    cluster_id = Column(Integer, nullable=True)
    attempt_count = Column(Integer, nullable=False)
    verdict_seq = Column(String, nullable=False)           # JSON 배열 문자열
    final_verdict = Column(String, nullable=False)
    solved = Column(Integer, nullable=False)               # 0/1 (censoring 플래그)
    first_ts = Column(Integer, nullable=True)
    last_ts = Column(Integer, nullable=True)
    ac_ts = Column(Integer, nullable=True)
    duration_unit = Column(String, nullable=False)         # 'seconds' | 'days' | 'ordinal'
    total_duration = Column(Float, nullable=True)
    pivot_count = Column(Integer, nullable=False, default=0)
    is_synthetic = Column(Integer, nullable=False, default=0)


class ArchivedPivotEvent(Base):
    """연속 제출 쌍 AST diff 기반 pivot (키스트로크 기반 pivot_events 테이블과 별개)."""

    __tablename__ = "pivot_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    problem_id = Column(String, index=True, nullable=False)
    from_submission_id = Column(String, nullable=False)
    to_submission_id = Column(String, nullable=False)
    pivot_type = Column(String, nullable=False)            # APPROACH_SWITCH | COMPLEXITY_FIX | EDGE_CASE_FIX | TYPO
    changed_node_ratio = Column(Float, nullable=True)
    is_synthetic = Column(Integer, nullable=False, default=0)


class StructuralPattern(Base):
    """AC 코드에서 검출된 알고리즘 구조 패턴 (BFS, DP 등)."""

    __tablename__ = "structural_pattern"

    id = Column(Integer, primary_key=True, autoincrement=True)
    submission_id = Column(String, index=True, nullable=False)
    pattern = Column(String, nullable=False)
    node_start = Column(Integer, nullable=True)
    node_end = Column(Integer, nullable=True)


class SyntheticUser(Base):
    __tablename__ = "synthetic_user"

    synth_user_id = Column(String, primary_key=True)
    theta = Column(Float, nullable=False)                  # IRT 잠재 능력치
    generated_at = Column(String, nullable=False)
    generator_version = Column(String, nullable=False)


class SyntheticPauseProfile(Base):
    __tablename__ = "synthetic_pause_profile"

    id = Column(Integer, primary_key=True, autoincrement=True)
    synth_user_id = Column(String, index=True, nullable=False)
    problem_id = Column(String, index=True, nullable=False)
    ast_label = Column(String, nullable=False)             # LOOP_BOUNDARY 등 (app/worker/labeler.py 체계)
    pause_ms = Column(Float, nullable=False)
    phase = Column(String, nullable=False)                 # SETUP | FORMATION | DEBUG | REFINE
    weight = Column(Float, nullable=False, default=0.1)


class BaselineCell(Base):
    """(tier, cluster, metric)별 percentile 기준선. cluster_id=-1은 tier 단독 fallback 셀."""

    __tablename__ = "baseline_cell"

    tier = Column(String, primary_key=True)
    cluster_id = Column(Integer, primary_key=True)
    metric = Column(String, primary_key=True)              # total_duration | attempt_count | pivot_count | pause_ms@LABEL
    percentiles_json = Column(String, nullable=False)      # '{"p10":..,"p25":..,"p50":..,"p75":..,"p90":..}'
    n_real = Column(Integer, nullable=False, default=0)
    n_synthetic = Column(Integer, nullable=False, default=0)
    updated_at = Column(String, nullable=False)


class IngestionLog(Base):
    """적재 파이프라인 단계 상태 (00_inspect ~ 09_validate)."""

    __tablename__ = "ingestion_log"

    stage = Column(String, primary_key=True)
    status = Column(String, nullable=False)                # 'pending' | 'running' | 'done' | 'failed'
    row_count = Column(Integer, nullable=True)
    started_at = Column(String, nullable=True)
    finished_at = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
