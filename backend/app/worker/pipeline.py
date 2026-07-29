"""Replay Worker 파이프라인 (dev-plan §4).

같은 입력에 항상 같은 출력을 내는 결정론적 배치. 두 번의 패스로 처리한다.
  1차 패스: AST 없이 타임스탬프 통계만 — pause 임계값, 삭제 버스트 경계
  2차 패스: 이벤트 재생 + 증분 AST — pause 라벨링, 버스트 스냅샷,
            디바운스된 패턴 매칭, 함수 본문 최초 진입 시각
(pause 임계값이 전체 간격 분포를 필요로 하므로 단일 패스로는 불가)
"""

from typing import Dict, List, Optional, Tuple

from app.schema.analysis import (
    AnalysisResult,
    AstSnapshot,
    DeleteBurst,
    DiffEvent,
    EditOp,
    PatternMatch,
    PatternWindowResult,
    PausePoint,
)
from app.worker import MATCHER_VERSION
from app.worker.astsupport import load_parser, normalize_lang
from app.worker.buffer import byte_and_point, byte_to_char_map
from app.worker.labeler import label_context
from app.worker.patterns import match_patterns
from app.worker.pause import detect_pauses
from app.worker.pivot import StructSnapshot, classify_pivot, count_local_rewrites, detect_bursts
from app.worker.replay import ReplayEngine
from app.worker.structdiff import (
    ShapeSnapshot,
    build_ast_snapshot,
    build_tree_evolution,
    diff_snapshots,
    extract_unmatched_segments,
)

MIN_EVENTS = 50                     # K < 50 → 분석 제외 (§8: 문제 열람만 하고 이탈)
DEBOUNCE_CHARS = 200                # 직전 매칭 이후 변경 누적 임계값 (§4.1 Step 5)
LONG_SESSION_MS = 6 * 3600 * 1000   # 이보다 길면 자리 비움 의심 (§8)
IDLE_GAP_MS = 30 * 60 * 1000        # 30분 이상 무편집 구간은 총 시간에서 제외 (§8)

# 패턴 매칭을 지원하는 언어 (그 외 파서 보유 언어는 라벨링까지만도 가능하지만,
# 계획서 §4.1 Step 1에 따라 미지원은 일괄 timing_only로 강등)
_FULL_LANGS = {"cpp", "python"}


def _edit_focus_char(ev: EditOp) -> int:
    """이벤트 적용 직후의 '마지막 편집 위치' (라벨링 기준점)."""
    if ev.op == 0:
        return ev.pos + len(ev.txt)
    return ev.pos


def _effective_total_ms(events: List[EditOp]) -> int:
    total = events[-1].t - events[0].t if events else 0
    if total <= LONG_SESSION_MS:
        return total
    idle = sum(
        gap
        for i in range(1, len(events))
        if (gap := events[i].t - events[i - 1].t) >= IDLE_GAP_MS
    )
    return total - idle


def analyze_session(
    events: List[EditOp],
    lang: str,
    submission_ts_ms: Optional[List[int]] = None,
    global_baseline: Optional[Tuple[float, float]] = None,
) -> AnalysisResult:
    submission_ts_ms = sorted(submission_ts_ms or [])
    events = sorted(events, key=lambda e: e.t)
    K = len(events)

    # ---- 극단값: 이벤트가 너무 적으면 분석 제외 (재생만 수행, M1 검증용) ----
    if K < MIN_EVENTS:
        engine = ReplayEngine(parser=None)
        engine.replay_all(events)
        code = engine.buffer.text()
        return AnalysisResult(
            analysis_level="degraded",
            matcher_version=MATCHER_VERSION,
            total_ms=_effective_total_ms(events),
            keystroke_count=K,
            code_bytes=len(code.encode("utf-8")),
            final_code=code,
        )

    norm_lang = normalize_lang(lang)
    parser = load_parser(lang) if norm_lang in _FULL_LANGS else None
    full = parser is not None
    level = "full" if full else "timing_only"

    # ---- 1차 패스: 타임스탬프 통계 ----
    pauses, _threshold = detect_pauses(events, global_baseline)
    bursts = detect_bursts(events)

    # 2차 패스에서 참조할 인덱스 테이블
    label_points: Dict[int, List[PausePoint]] = {}      # 적용 완료 인덱스 → pause들
    for p in pauses:
        label_points.setdefault(p.event_index - 1, []).append(p)
    burst_before: Dict[int, List[DeleteBurst]] = {}     # 적용 직전 인덱스 → 버스트
    burst_after: Dict[int, List[DeleteBurst]] = {}      # 적용 완료 인덱스 → 버스트
    for b in bursts:
        burst_before.setdefault(b.start_index, []).append(b)
        burst_after.setdefault(b.rewrite_horizon, []).append(b)

    # ---- 2차 패스: 재생 + 증분 AST ----
    engine = ReplayEngine(parser=parser)
    snapshots_before: Dict[int, StructSnapshot] = {}    # key: burst.start_index
    snapshots_after: Dict[int, StructSnapshot] = {}
    first_complete_ms: Dict[str, int] = {}              # 패턴 → 최초 매칭 성공 시각
    final_matches: List[PatternMatch] = []
    body_entry_ms: Optional[int] = None                 # 첫 함수 본문 진입 시각
    chars_since_match = 0
    prev_shape: Optional[ShapeSnapshot] = None          # 직전 매처 실행 시점 구조 스냅샷
    diff_timeline: List[DiffEvent] = []                 # 매처 실행 시점 간 구조 diff 누적
    ast_snapshots: List[AstSnapshot] = []               # 트리 변화 이력 (세션 종료 후 DB 영속화)

    def run_matcher(t_ms: int, at_end: bool = False) -> None:
        nonlocal chars_since_match, final_matches, prev_shape
        matches = match_patterns(engine.tree, engine.source_bytes(), norm_lang)
        for m in matches:
            first_complete_ms.setdefault(m.pattern, t_ms)
        if at_end:
            final_matches = matches
        # 구조 diff 추출 (addendum §2): 매처와 같은 디바운스 시점에만 스냅샷을 떠서
        # 세션당 diff 이벤트 수가 매처 실행 횟수 P와 같은 규모로 수렴한다.
        shape = ShapeSnapshot(engine.tree, engine.source_bytes())
        step_events = diff_snapshots(prev_shape, shape, t_ms)
        diff_timeline.extend(step_events)
        # UNMATCHED 여부와 무관하게 전 구간 기록 — 세션 종료 후 트리 변화 재구성용
        ast_snapshots.append(build_ast_snapshot(len(ast_snapshots), t_ms, shape, step_events))
        prev_shape = shape
        chars_since_match = 0

    for i, ev in enumerate(events):
        if full and i in burst_before:
            snap = StructSnapshot(engine.tree, engine.source_bytes(), norm_lang)
            for b in burst_before[i]:
                snapshots_before[b.start_index] = snap

        engine.apply(ev, i)
        chars_since_match += len(ev.txt) if ev.op == 0 else ev.len

        if not full:
            continue

        src_bytes = engine.source_bytes()

        if i in label_points or chars_since_match >= DEBOUNCE_CHARS:
            # 디바운스 실행 시점: pause 감지 시점 + 변경 누적 초과 시점 (§4.1 Step 5)
            run_matcher(ev.t)

        if i in label_points:
            focus_b, _ = byte_and_point(engine.buffer.text(), _edit_focus_char(ev))
            label = label_context(engine.tree, focus_b)
            for p in label_points[i]:
                p.ast_label = label
            if body_entry_ms is None and label not in ("", "SETUP", "SYNTAX_STRUGGLE"):
                body_entry_ms = ev.t

        if body_entry_ms is None:
            # 첫 함수 본문 진입: 편집 위치가 함수 본문 내부로 들어온 최초 시각
            focus_b, _ = byte_and_point(engine.buffer.text(), _edit_focus_char(ev))
            if label_context(engine.tree, focus_b) in (
                "ALGORITHM_ENTRY", "OTHER", "LOOP_BOUNDARY", "BRANCH_CONDITION", "INDEX_REASONING",
            ):
                body_entry_ms = ev.t

        if i in burst_after:
            snap = StructSnapshot(engine.tree, src_bytes, norm_lang)
            for b in burst_after[i]:
                snapshots_after[b.start_index] = snap

    if full:
        run_matcher(events[-1].t, at_end=True)  # 세션 종료 시점 확정 매칭 (§4.1 Step 5)

    final_code = engine.buffer.text()

    # ---- pivot 분류 (버스트 직전 vs 재작성 완료 스냅샷) ----
    for b in bursts:
        if full and b.start_index in snapshots_before and b.start_index in snapshots_after:
            b.pivot_type = classify_pivot(
                snapshots_before[b.start_index], snapshots_after[b.start_index]
            )
    # TYPO는 지표에서 제외 (§4.1 Step 4) — 단, 국소 반복 수정 신호는 별도로 센다
    typo_bursts = [b for b in bursts if b.pivot_type == "TYPO"]
    pivots = [b for b in bursts if b.pivot_type != "TYPO"]
    local_rewrite_count = count_local_rewrites(typo_bursts, events) if full else 0

    # ---- 패턴 형성 구간 역산 (§4.1 Step 6) ----
    windows: List[PatternWindowResult] = []
    if full and final_matches:
        b2c = byte_to_char_map(final_code)
        origins = engine.buffer.origins()
        for m in final_matches:
            origin_events = set()
            for start_b, end_b in m.ranges:
                start_c = b2c[min(start_b, len(b2c) - 1)]
                end_c = b2c[min(end_b, len(b2c) - 1)]
                origin_events.update(origins[start_c:end_c])
            if not origin_events:
                continue
            t_start = min(events[idx].t for idx in origin_events)
            t_complete = first_complete_ms.get(m.pattern, events[-1].t)
            t_complete = max(t_complete, t_start)
            win = PatternWindowResult(
                pattern=m.pattern,
                t_start_ms=t_start,
                t_complete_ms=t_complete,
                formation_ms=t_complete - t_start,
            )
            # 구간 내 pause·pivot을 패턴에 귀속
            for p in pauses:
                if t_start <= p.t_ms <= t_complete and not p.pattern:
                    p.pattern = m.pattern
                    win.pause_ms_in_window += p.duration_ms
            for b in pivots:
                if t_start <= b.t_ms <= t_complete and not b.pattern:
                    b.pattern = m.pattern
                    win.pivot_count_in_window += 1
            windows.append(win)

    # ---- UNMATCHED 세그먼트 추출 (addendum §2~3, 결정론적) ----
    unmatched = (
        extract_unmatched_segments(diff_timeline, windows, engine.tree, engine.source_bytes())
        if full
        else []
    )

    # ---- 세션 전체 AST 트리 변화 이력 (app/model/ast_tree.py) ----
    ast_evolution = (
        build_tree_evolution(ast_snapshots, engine.tree, engine.source_bytes()) if full else None
    )

    # ---- Phase 세그멘테이션 (§4.1 Step 7) ----
    t_end = events[-1].t
    setup_end = body_entry_ms if body_entry_ms is not None else t_end
    formation_end = max((w.t_complete_ms for w in windows), default=setup_end)
    first_sub = submission_ts_ms[0] if submission_ts_ms else None
    last_sub = submission_ts_ms[-1] if submission_ts_ms else None

    def phase_of(t: int) -> str:
        if last_sub is not None and t >= last_sub:
            return "REFINE"
        if first_sub is not None and t >= first_sub:
            return "DEBUG"
        if t < setup_end:
            return "SETUP"
        return "FORMATION"

    for p in pauses:
        p.phase = phase_of(p.t_ms)

    setup_ms = min(setup_end, t_end)
    debug_ms = (last_sub - first_sub) if (first_sub is not None and last_sub is not None) else 0
    refine_ms = (t_end - last_sub) if (last_sub is not None and t_end > last_sub) else 0
    formation_ms = max(min(formation_end, t_end) - setup_ms, 0)

    return AnalysisResult(
        analysis_level=level,
        matcher_version=MATCHER_VERSION,
        total_ms=_effective_total_ms(events),
        setup_ms=setup_ms,
        formation_ms=formation_ms,
        debug_ms=debug_ms,
        refine_ms=refine_ms,
        keystroke_count=K,
        pause_total_ms=sum(p.duration_ms for p in pauses),
        pause_count=len(pauses),
        pivot_count=len(pivots),
        local_rewrite_count=local_rewrite_count,
        code_bytes=len(final_code.encode("utf-8")),
        final_code=final_code,
        pauses=pauses,
        pivots=pivots,
        pattern_windows=windows,
        patterns_detected=sorted({m.pattern for m in final_matches}),
        unmatched_segments=unmatched,
        ast_evolution=ast_evolution,
    )
