"""git 방식 타임라인 산출 스키마 (git-timeline-feedback-spec.md §2.5).

Stage A(app/worker/timeline.py)의 산출물이자 DB 저장(app/model/timeline.py)·
Stage B 프롬프트 렌더링·타임라인 조회 API의 공통 표현.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel

# 세그먼트 라벨 (§2.4). LLM은 이 구분을 뒤집을 수 없다.
SEGMENT_LABELS = ("STALL_SUSPECT", "HIGH_CHURN", "DEBUG_LOOP", "BURST_WRITE", "STEADY")

# 실패 판정 verdict — DEBUG_LOOP 경계 (§2.4)
FAILED_VERDICTS = ("WA", "TLE", "RE", "CE", "MLE", "OLE")

# 사용자 노출 문구. churn/burst를 "어려웠던 구간"으로 부르지 않는 것이 §2.4의 요점.
SEGMENT_LABEL_KO = {
    "STALL_SUSPECT": "생각이 멈춘 구간",
    "HIGH_CHURN": "같은 부분을 반복 수정한 구간",
    "DEBUG_LOOP": "제출-수정 반복 구간",
    "BURST_WRITE": "설계를 빠르게 옮겨적은 구간",
    "STEADY": "평이하게 진행한 구간",
}


class Hunk(BaseModel):
    op: Literal["add", "del", "mod"]
    old_start: int                  # 직전 스냅샷 기준 시작 라인 (1-based), add면 삽입 위치
    new_start: int                  # 현재 스냅샷 기준 시작 라인 (1-based)
    old_lines: List[str] = []       # del/mod: 삭제·수정 전 라인 원문
    new_lines: List[str] = []       # add/mod: 추가·수정 후 라인 원문


class Commit(BaseModel):
    seq: int                        # 세션 내 0부터, SUBMIT 레코드도 같은 시퀀스 공간 사용
    kind: Literal["edit", "submit"]
    t_ms: int                       # 커밋을 닫은 시각
    pause_before_ms: int = 0        # 직전 커밋 마지막 이벤트 ~ 이 커밋 첫 이벤트 간격
    duration_ms: int = 0            # 커밋 내 첫~마지막 이벤트 간격 (타이핑 시간)
    hunks: List[Hunk] = []          # kind="edit"
    verdict: Optional[str] = None   # kind="submit": AC|WA|TLE|RE|CE|PENDING
    lines_added: int = 0
    lines_deleted: int = 0
    lines_modified: int = 0
    net_lines: int = 0
    churn_lines: int = 0
    snapshot_hash: str = ""         # 커밋 직후 전체 코드 해시
    snapshot_text: Optional[str] = None  # 제출 직전·세션 종료 시에만 채움
    segment_label: str = ""         # 소속 세그먼트 라벨 (렌더링/조회 편의, DB는 세그먼트 행이 권위)


class Segment(BaseModel):
    seg_id: str                     # "sg_0" ...
    label: Literal["STALL_SUSPECT", "HIGH_CHURN", "DEBUG_LOOP", "BURST_WRITE", "STEADY"]
    commit_start_seq: int
    commit_end_seq: int
    t_start_ms: int
    t_end_ms: int
    pause_ms: int = 0               # 세그먼트 내 pause 합
    lines_touched: int = 0
    net_lines: int = 0


class TimelineResult(BaseModel):
    timeline_version: int
    analysis_level: str             # "full" | "degraded" (seq gap / 이벤트 없음)
    total_ms: int = 0
    keystroke_count: int = 0
    commits: List[Commit] = []
    segments: List[Segment] = []
    final_code: str = ""
    verdict_seq: List[str] = []

    # ---- session_summaries 재정의 컬럼 (§3.4) ----
    @property
    def stall_ms(self) -> int:
        return sum(s.t_end_ms - s.t_start_ms for s in self.segments if s.label == "STALL_SUSPECT")

    @property
    def debug_ms(self) -> int:
        return sum(
            s.t_end_ms - s.t_start_ms
            for s in self.segments
            if s.label in ("DEBUG_LOOP", "HIGH_CHURN")
        )

    @property
    def steady_ms(self) -> int:
        return sum(
            s.t_end_ms - s.t_start_ms
            for s in self.segments
            if s.label in ("STEADY", "BURST_WRITE")
        )

    @property
    def t_bound_ms(self) -> int:
        """t_range_ms 검증 상한. 커밋 t_ms는 세션 시작 기준 절대 시각이므로, 첫 이벤트가
        t=0이 아니거나 제출이 마지막 편집 뒤에 오면 total_ms(=실질 소요 시간)를 넘는다."""
        if not self.commits:
            return self.total_ms
        return max(self.total_ms, max(c.t_ms for c in self.commits))

    @property
    def edit_commits(self) -> List[Commit]:
        return [c for c in self.commits if c.kind == "edit"]

    @property
    def pause_total_ms(self) -> int:
        return sum(c.pause_before_ms for c in self.edit_commits)

    @property
    def pause_count(self) -> int:
        return sum(1 for c in self.edit_commits if c.pause_before_ms > 0)


# ---- GET /sessions/{session_id}/timeline ----
class TimelineResponse(BaseModel):
    session_id: str
    timeline_version: int
    analysis_level: str
    total_ms: int
    keystroke_count: int
    verdict_seq: List[str] = []
    commits: List[Commit] = []
    segments: List[Segment] = []
