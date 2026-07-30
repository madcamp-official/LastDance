"""Stage A — git 방식 타임라인 빌더 (git-timeline-feedback-spec.md §2).

EditOp 스트림을 재생해 5초 이상 pause를 커밋 경계로 하는 라인 단위 커밋 로그를 만들고,
커밋별 diff(hunk)와 결정론적 세그먼트 라벨을 붙인다. LLM 없이 완결되며 같은 입력이면
항상 같은 출력을 낸다 (§7 재현성).

기존 app/worker/pipeline.py(AST 패턴 매처)를 대체하는 진입점. tree-sitter를 쓰지 않으므로
언어에 무관하게 동작한다 — lang은 산출물 메타로만 쓰인다.

핵심 설계 의도: "코드가 많이 바뀐 구간 = 어려웠던 구간"이라는 오진을 차단하기 위해
STALL_SUSPECT(손은 멈췄고 코드는 안 늘어남)와 HIGH_CHURN/BURST_WRITE(코드는 많이
바뀜)를 서로 다른 라벨로 분리해서 하류(LLM)에 넘긴다 (§2.4).
"""

import difflib
import hashlib
from typing import Dict, List, Optional, Sequence, Set, Tuple

from app.schema.analysis import EditOp
from app.schema.timeline import FAILED_VERDICTS, Commit, Hunk, Segment, TimelineResult
from app.util.stats import percentile
from app.worker import TIMELINE_VERSION
from app.worker.buffer import OriginBuffer

# ---- 커밋 경계 규칙 (§2.1) ----
PAUSE_COMMIT_MS = 5000          # 연속 이벤트 간격 ≥ 5초 → 커밋 닫기

# ---- 세그먼트 라벨 임계값 (§2.4). 변경 시 TIMELINE_VERSION을 올린다 (§7) ----
STALL_PAUSE_MS = 30000          # 이 이상 pause면 무조건 STALL 후보
STALL_TOP_PCT = 0.10            # 또는 세션 내 pause 상위 10%
STALL_MAX_NET_LINES = 5         # 직후 커밋의 순증가가 이 이하일 때만 STALL 확정
CHURN_WINDOW = 3                # 연속 N커밋 윈도
CHURN_RATIO = 3.0               # Σ만진라인 / max(1, Σ순증가) ≥ 3
CHURN_MIN_TOUCHED = 10          # 그리고 Σ만진라인 ≥ 10
CHURN_REPEAT_COMMITS = 3        # 동일 lid를 N커밋 이상 반복 수정
BURST_MIN_NET_LINES = 15        # 순증가 ≥ 15인 커밋
BURST_MAX_PAUSE_MS = 10000      # 이 이하 간격으로 연속되면 BURST_WRITE

# ---- 총 소요 시간 보정 (기존 pipeline.py §8 규약 계승) ----
LONG_SESSION_MS = 6 * 3600 * 1000
IDLE_GAP_MS = 30 * 60 * 1000

# 라벨 우선순위 (§2.4 판정 규칙은 우선순위 순으로 적용)
_LABEL_PRIORITY = ("DEBUG_LOOP", "STALL_SUSPECT", "HIGH_CHURN", "BURST_WRITE", "STEADY")


def _lines(text: str) -> List[str]:
    return text.splitlines()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _effective_total_ms(events: Sequence[EditOp]) -> int:
    """자리 비움(30분 이상 무편집) 구간을 제외한 실질 소요 시간."""
    if not events:
        return 0
    total = events[-1].t - events[0].t
    if total <= LONG_SESSION_MS:
        return total
    idle = sum(
        gap
        for i in range(1, len(events))
        if (gap := events[i].t - events[i - 1].t) >= IDLE_GAP_MS
    )
    return total - idle


class _LineDoc:
    """라인 문서 + stable line id (§2.3).

    물리 라인 번호는 위쪽 삽입/삭제로 계속 밀리므로, 생성 시점에 부여한 lid로
    "같은 라인을 몇 번 고쳤는가"를 라인 번호와 무관하게 센다.
    """

    __slots__ = ("lines", "lids", "_next_lid", "edit_count", "lid_commits")

    def __init__(self) -> None:
        self.lines: List[str] = []
        self.lids: List[int] = []
        self._next_lid = 0
        self.edit_count: Dict[int, int] = {}        # lid → 다시 만져진 커밋 수
        self.lid_commits: Dict[int, Set[int]] = {}  # lid → 만진 커밋 seq 집합

    def _new_lid(self) -> int:
        lid = self._next_lid
        self._next_lid += 1
        return lid

    def apply(self, new_lines: List[str], commit_seq: int) -> Tuple[List[Hunk], Dict[str, int]]:
        """직전 스냅샷 → 현재 스냅샷 라인 diff. hunk + 라인 통계 반환."""
        old = self.lines
        sm = difflib.SequenceMatcher(a=old, b=new_lines, autojunk=False)
        hunks: List[Hunk] = []
        new_lids: List[int] = []
        added = deleted = modified = 0
        touched: List[int] = []

        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                new_lids.extend(self.lids[i1:i2])
                continue
            if tag == "insert":
                hunks.append(
                    Hunk(op="add", old_start=i1 + 1, new_start=j1 + 1, new_lines=new_lines[j1:j2])
                )
                added += j2 - j1
                new_lids.extend(self._new_lid() for _ in range(j2 - j1))
            elif tag == "delete":
                hunks.append(
                    Hunk(op="del", old_start=i1 + 1, new_start=j1 + 1, old_lines=old[i1:i2])
                )
                deleted += i2 - i1
                touched.extend(self.lids[i1:i2])
            else:  # replace = 수정
                n_old, n_new = i2 - i1, j2 - j1
                n_mod = min(n_old, n_new)
                hunks.append(
                    Hunk(
                        op="mod", old_start=i1 + 1, new_start=j1 + 1,
                        old_lines=old[i1:i2], new_lines=new_lines[j1:j2],
                    )
                )
                modified += n_mod
                added += max(0, n_new - n_old)
                deleted += max(0, n_old - n_new)
                touched.extend(self.lids[i1:i2])
                # 겹치는 위치는 같은 논리 라인의 수정으로 간주해 lid를 유지한다
                new_lids.extend(self.lids[i1 : i1 + n_mod])
                new_lids.extend(self._new_lid() for _ in range(max(0, n_new - n_old)))

        # 같은 커밋 안에서 한 라인을 여러 hunk가 건드려도 "그 커밋에서 1회 만짐"으로 센다
        churn_lines = 0
        for lid in sorted(set(touched)):
            self.edit_count[lid] = self.edit_count.get(lid, 0) + 1
            self.lid_commits.setdefault(lid, set()).add(commit_seq)
            if self.edit_count[lid] >= 2:
                churn_lines += 1

        self.lines = list(new_lines)
        self.lids = new_lids
        return hunks, {
            "lines_added": added,
            "lines_deleted": deleted,
            "lines_modified": modified,
            "net_lines": added - deleted,
            "churn_lines": churn_lines,
        }


def _stall_threshold(pause_values: List[int]) -> int:
    """§2.4: pause ≥ 30s **또는** 세션 내 상위 10% — 둘 중 낮은 쪽이 실질 임계값."""
    if not pause_values:
        return STALL_PAUSE_MS
    top = percentile(pause_values, 1.0 - STALL_TOP_PCT)
    return int(min(float(STALL_PAUSE_MS), top))


def _debug_loop_seqs(commits: List[Commit]) -> Set[int]:
    """실패 제출과 다음 제출(없으면 세션 끝) 사이의 모든 편집 커밋."""
    marked: Set[int] = set()
    in_loop = False
    for c in commits:
        if c.kind == "submit":
            in_loop = (c.verdict or "").upper() in FAILED_VERDICTS
            continue
        if in_loop:
            marked.add(c.seq)
    return marked


def _high_churn_seqs(edits: List[Commit], lid_commits: Dict[int, Set[int]]) -> Set[int]:
    marked: Set[int] = set()
    for i in range(len(edits) - CHURN_WINDOW + 1):
        win = edits[i : i + CHURN_WINDOW]
        touched = sum(c.lines_added + c.lines_deleted + c.lines_modified for c in win)
        net = sum(c.net_lines for c in win)
        if touched >= CHURN_MIN_TOUCHED and touched / max(1, net) >= CHURN_RATIO:
            marked.update(c.seq for c in win)
    # 동일 라인(lid)을 3커밋 이상 반복 수정 — 윈도 규칙이 못 잡는 산발적 반복 수정
    edit_seqs = {c.seq for c in edits}
    for seqs in lid_commits.values():
        if len(seqs) >= CHURN_REPEAT_COMMITS:
            marked.update(seqs & edit_seqs)
    return marked


def _burst_write_seqs(edits: List[Commit]) -> Set[int]:
    """순증가 ≥ 15인 커밋들이 pause ≤ 10s 간격으로 연속된 구간.

    단독 커밋도 직전 pause가 10s 이하라면(= 생각 없이 곧바로 쏟아낸 타이핑)
    BURST_WRITE로 본다 — §2.4의 "머릿속 설계를 쏟아내는 구간" 판정 의도.
    """
    marked: Set[int] = set()
    run: List[Commit] = []

    def flush() -> None:
        if not run:
            return
        if len(run) >= 2 or run[0].pause_before_ms <= BURST_MAX_PAUSE_MS:
            marked.update(c.seq for c in run)
        run.clear()

    for c in edits:
        if c.net_lines < BURST_MIN_NET_LINES:
            flush()
            continue
        if run and c.pause_before_ms > BURST_MAX_PAUSE_MS:
            flush()
        run.append(c)
    flush()
    return marked


def _label_commits(commits: List[Commit], lid_commits: Dict[int, Set[int]]) -> Dict[int, str]:
    edits = [c for c in commits if c.kind == "edit"]
    debug = _debug_loop_seqs(commits)
    thr = _stall_threshold([c.pause_before_ms for c in edits])
    stall = {
        c.seq
        for c in edits
        if c.pause_before_ms >= thr and c.net_lines <= STALL_MAX_NET_LINES
    }
    churn = _high_churn_seqs(edits, lid_commits)
    burst = _burst_write_seqs(edits)
    buckets = {
        "DEBUG_LOOP": debug,
        "STALL_SUSPECT": stall,
        "HIGH_CHURN": churn,
        "BURST_WRITE": burst,
    }
    labels: Dict[int, str] = {}
    for c in edits:
        labels[c.seq] = next(
            (lb for lb in _LABEL_PRIORITY if c.seq in buckets.get(lb, ())), "STEADY"
        )
    return labels


def _build_segments(commits: List[Commit], labels: Dict[int, str]) -> List[Segment]:
    """같은 라벨의 연속 편집 커밋을 하나의 세그먼트로 묶는다. 제출은 경계."""
    segments: List[Segment] = []
    run: List[Commit] = []

    def flush() -> None:
        if not run:
            return
        first, last = run[0], run[-1]
        # 세그먼트 시작 = 첫 커밋의 pause가 시작된 시점. pause를 빼면 STALL_SUSPECT
        # 세그먼트의 지속 시간이 "타이핑한 2초"로 찍혀 §2.4의 요점(손은 멈췄고 코드는
        # 안 늘어남 = 사고 시간)이 사라진다. 프롬프트의 세그먼트 요약(§4.3)과
        # session_summaries.formation_ms(§3.4)가 모두 이 값을 쓴다.
        segments.append(
            Segment(
                seg_id=f"sg_{len(segments)}",
                label=labels[first.seq],
                commit_start_seq=first.seq,
                commit_end_seq=last.seq,
                t_start_ms=max(0, first.t_ms - first.duration_ms - first.pause_before_ms),
                t_end_ms=last.t_ms,
                pause_ms=sum(c.pause_before_ms for c in run),
                lines_touched=sum(
                    c.lines_added + c.lines_deleted + c.lines_modified for c in run
                ),
                net_lines=sum(c.net_lines for c in run),
            )
        )
        run.clear()

    for c in commits:
        if c.kind == "submit":
            flush()
            continue
        if run and labels[run[-1].seq] != labels[c.seq]:
            flush()
        run.append(c)
    flush()

    for seg in segments:
        for c in commits:
            if c.kind == "edit" and seg.commit_start_seq <= c.seq <= seg.commit_end_seq:
                c.segment_label = seg.label
    return segments


def build_timeline(
    events: List[EditOp],
    lang: str,
    submission_ts_ms: Optional[List[int]] = None,
    verdicts: Optional[List[str]] = None,
) -> TimelineResult:
    """EditOp 스트림 → 커밋 로그 + 세그먼트 라벨 (§2 전 구간 결정론적).

    submission_ts_ms와 verdicts는 인덱스로 대응한다. verdicts가 짧으면 "PENDING"으로
    채운다 (채점 결과가 아직 안 붙은 제출).
    """
    events = sorted(events, key=lambda e: e.t)
    subs = sorted(submission_ts_ms or [])
    vs = list(verdicts or [])[: len(subs)]
    vs += ["PENDING"] * (len(subs) - len(vs))

    result = TimelineResult(
        timeline_version=TIMELINE_VERSION,
        analysis_level="full" if events else "degraded",
        total_ms=_effective_total_ms(events),
        keystroke_count=sum(1 for e in events if e.src == "user"),
        verdict_seq=list(vs),
    )
    if not events:
        return result

    buf = OriginBuffer()
    doc = _LineDoc()
    commits: List[Commit] = []
    group: List[EditOp] = []
    prev_event_t = 0        # 세션 시작(t=0) 기준 — 첫 커밋의 pause는 시작부터의 정지
    seq = 0

    def close_group() -> None:
        nonlocal group, seq, prev_event_t
        if not group:
            return
        text = buf.text()
        hunks, stats = doc.apply(_lines(text), seq)
        commits.append(
            Commit(
                seq=seq,
                kind="edit",
                t_ms=group[-1].t,
                pause_before_ms=max(0, group[0].t - prev_event_t),
                duration_ms=max(0, group[-1].t - group[0].t),
                hunks=hunks,
                snapshot_hash=_hash(text),
                **stats,
            )
        )
        prev_event_t = group[-1].t
        seq += 1
        group = []

    def emit_submit(t_ms: int, verdict: str) -> None:
        nonlocal seq
        text = buf.text()
        commits.append(
            Commit(
                seq=seq, kind="submit", t_ms=t_ms, verdict=verdict,
                snapshot_hash=_hash(text), snapshot_text=text,
            )
        )
        seq += 1

    sub_idx = 0
    for ev in events:
        # 제출 시각은 무조건 커밋을 닫고 SUBMIT 레코드를 끼운다 (§2.1)
        while sub_idx < len(subs) and subs[sub_idx] <= ev.t:
            close_group()
            emit_submit(subs[sub_idx], vs[sub_idx])
            sub_idx += 1
        if group and ev.t - group[-1].t >= PAUSE_COMMIT_MS:
            close_group()
        # src != "user"(autoindent/autocomplete)도 재생에는 포함 — keystroke 통계에서만 제외
        if ev.op == 0:
            buf.insert(ev.pos, ev.txt, len(commits))
        else:
            buf.delete(ev.pos, ev.len)
        group.append(ev)

    close_group()                                   # 세션 종료: 잔여 편집을 최종 커밋으로
    while sub_idx < len(subs):
        emit_submit(subs[sub_idx], vs[sub_idx])
        sub_idx += 1

    final_code = buf.text()
    if commits:
        commits[-1].snapshot_text = final_code      # 세션 종료 시점 전체 코드 (§2.5)

    labels = _label_commits(commits, doc.lid_commits)
    result.commits = commits
    result.segments = _build_segments(commits, labels)
    result.final_code = final_code
    return result
