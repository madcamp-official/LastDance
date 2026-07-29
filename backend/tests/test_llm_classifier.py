"""LLM 구조 분류기 파이프라인 단계별 검증 (llm-structural-classifier-addendum.md).

실행: backend 디렉토리에서  python -m tests.test_llm_classifier
(pytest 없이도 도는 가벼운 스크립트형 테스트 — tests/test_worker.py와 동일 양식)

단계별 커버리지:
  1) Structural Diff Extractor — 스냅샷 diff (insert/delete/move, 이름-무관)
  2) UNMATCHED 세그먼트 추출 — 윈도우 커버리지 / 시간 그룹핑 / 최소 이벤트 필터
  3) 파이프라인 통합 — analyze_session이 unmatched_segments를 결정론적으로 산출
  4) 분류기 입력/프롬프트 — 코드·식별자 텍스트 미포함 (addendum §3 원칙)
  5) 출력 파싱 — JSON 스키마 강제, fence 제거
  6) 구조 grounding 검증기 — addendum §6 규칙 1~4
  7) classify_unmatched — 재시도, 최종 실패 시 폐기, confidence 임계
  8) 저장 계층 — source 컬럼 분리, 세그먼트 영속화·상태 전이, 멱등성
  9) 백필 (addendum §7) — pending/구버전 세그먼트 재분류
 10) 검수·메트릭 (addendum §7~§8) — 후보 집계, UNMATCHED 비율, grounding 실패율
"""

import asyncio
import json
import sys

sys.path.insert(0, ".")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.llm.classifier import (
    CANDIDATE_MIN_CONFIDENCE,
    CLASSIFIER_VERSION,
    MAX_ATTEMPTS,
    SegmentEvidence,
    SegmentResult,
    build_classifier_input,
    build_user_prompt,
    classify_unmatched,
    parse_output,
    verify_structural_grounding,
)
from app.llm.client import LLMResult
from app.model.analysis import PatternWindowRow, PivotEventRow, SessionSummary, UnmatchedSegmentRow
from app.util.classifier_review import aggregate_candidates, classifier_metrics
from app.worker.backfill import backfill_classifier, stale_session_ids
from app.schema.analysis import (
    AnalysisResult,
    DiffEvent,
    EditOp,
    PatternWindowResult,
    UnmatchedSegment,
)
from app.worker.astsupport import load_parser
from app.worker.pipeline import analyze_session
from app.worker.store import save_analysis, save_llm_candidates
from app.worker.structdiff import (
    MIN_SEGMENT_EVENTS,
    ShapeSnapshot,
    diff_snapshots,
    extract_unmatched_segments,
    final_subtree_shape,
)

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "OK " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def parse(code: str, lang: str):
    parser = load_parser(lang)
    return parser.parse(code.encode("utf-8"))


# ---- 1) Structural Diff Extractor ----

CPP_V1 = """
int solve(int x) { return x; }
int main() {
    for (int i = 0; i < 10; i++) { }
}
"""

CPP_V2 = """
int solve(int x) { if (x == 0) return 0; return solve(x - 1); }
int main() {
    while (true) { break; }
}
"""


def test_diff_extractor():
    t1 = parse(CPP_V1, "cpp")
    t2 = parse(CPP_V2, "cpp")
    s1 = ShapeSnapshot(t1, CPP_V1.encode())
    s2 = ShapeSnapshot(t2, CPP_V2.encode())

    events = diff_snapshots(s1, s2, t_ms=1000)
    check("diff nonempty", len(events) > 0, f"n={len(events)}")

    inserts = {e.node_type for e in events if e.op == "insert"}
    deletes = {e.node_type for e in events if e.op == "delete"}
    check("diff insert while", "while_statement" in inserts, f"inserts={inserts}")
    check("diff delete for", "for_statement" in deletes, f"deletes={deletes}")
    check("diff self-call boolean", any(e.callee_is_self for e in events if e.op == "insert"))
    check("diff all t_ms stamped", all(e.t_ms == 1000 for e in events))

    # 이름-무관: diff 이벤트 어디에도 식별자 텍스트가 없어야 한다
    dumped = json.dumps([e.model_dump() for e in events])
    check("diff no identifier leak", "solve" not in dumped and "main" not in dumped)

    # 결정론성: 같은 입력 → 같은 출력
    events2 = diff_snapshots(ShapeSnapshot(t1, CPP_V1.encode()), ShapeSnapshot(t2, CPP_V2.encode()), 1000)
    check("diff deterministic", [e.model_dump() for e in events] == [e.model_dump() for e in events2])

    # 빈 이전 스냅샷 → 전부 insert
    events3 = diff_snapshots(None, s1, 0)
    check("diff from empty all inserts", events3 and all(e.op == "insert" for e in events3))

    shape = final_subtree_shape(t2, CPP_V2.encode())
    check("final shape has_self_call", shape is not None and shape.has_self_call)
    # addendum §3 예시처럼 함수가 있으면 function_definition이 shape 루트
    check("final shape root is function", shape.root_type == "function_definition", shape.root_type)
    check("final shape no visited array", not shape.has_visited_array_pattern)


# ---- 1b) move 탐지 + 방문 배열 피처 ----

CPP_MOVE_V1 = """
int main() {
    for (int i = 0; i < 10; i++) { if (a < b) x = 1; }
}
"""

CPP_MOVE_V2 = """
int main() {
    while (a < b) { if (a < b) x = 1; }
}
"""

CPP_VISITED = """
int main() {
    bool vis[100];
    while (n > 0) { vis[n] = true; n--; }
}
"""


def test_move_and_features():
    s1 = ShapeSnapshot(parse(CPP_MOVE_V1, "cpp"), CPP_MOVE_V1.encode())
    s2 = ShapeSnapshot(parse(CPP_MOVE_V2, "cpp"), CPP_MOVE_V2.encode())
    events = diff_snapshots(s1, s2, t_ms=500)

    moves = [e for e in events if e.op == "move"]
    check("move detected", len(moves) >= 1, f"ops={[(e.op, e.node_type) for e in events]}")
    if_moves = [m for m in moves if m.node_type == "if_statement"]
    check(
        "move if for->while",
        any(m.from_parent == "for_statement" and m.to_parent == "while_statement" for m in if_moves),
        f"moves={[(m.node_type, m.from_parent, m.to_parent) for m in moves]}",
    )
    # move로 짝지어진 노드는 insert/delete로 중복 방출되지 않아야 한다
    ins_del_ifs = [e for e in events if e.op != "move" and e.node_type == "if_statement"]
    check("move consumes insert/delete pair", ins_del_ifs == [],
          f"got={[(e.op, e.node_type) for e in ins_del_ifs]}")

    # 구조 조상 눈금: function(1) → while(2) → if(3)
    check("struct depth scale", if_moves and if_moves[0].depth == 3, f"depth={if_moves and if_moves[0].depth}")

    tree_v = parse(CPP_VISITED, "cpp")
    shape_v = final_subtree_shape(tree_v, CPP_VISITED.encode())
    check("visited array pattern true", shape_v is not None and shape_v.has_visited_array_pattern)


# ---- 2) UNMATCHED 세그먼트 추출 ----

def _ev(t_ms: int) -> DiffEvent:
    return DiffEvent(t_ms=t_ms, op="insert", node_type="if_statement")


def test_segment_extraction():
    tree = parse(CPP_V1, "cpp")
    src = CPP_V1.encode()

    # 윈도우 [0, 10000]가 덮는 이벤트는 제외되어야 한다
    windows = [PatternWindowResult(pattern="BFS", t_start_ms=0, t_complete_ms=10_000, formation_ms=10_000)]
    covered = [_ev(t) for t in (1000, 2000, 3000)]
    uncovered = [_ev(t) for t in (20_000, 21_000, 22_000)]
    segs = extract_unmatched_segments(covered + uncovered, windows, tree, src)
    check("segment excludes covered", len(segs) == 1, f"n={len(segs)}")
    check("segment time range", segs and segs[0].t_start_ms == 20_000 and segs[0].t_end_ms == 22_000)
    check("segment id format", segs and segs[0].segment_id == "seg_0")
    check("segment has shape", segs and segs[0].final_subtree_shape is not None)

    # 30초 넘는 간격이면 세그먼트 분리
    far = [_ev(t) for t in (100_000, 101_000, 102_000)]
    segs2 = extract_unmatched_segments(uncovered + far, [], tree, src)
    check("segment gap split", len(segs2) == 2, f"n={len(segs2)}")

    # 최소 이벤트 미만 그룹은 버림
    tiny = [_ev(t) for t in range(0, (MIN_SEGMENT_EVENTS - 1) * 1000, 1000)]
    segs3 = extract_unmatched_segments(tiny, [], tree, src)
    check("segment min-events filter", segs3 == [], f"n={len(segs3)}")

    check("segment empty timeline", extract_unmatched_segments([], [], tree, src) == [])


# ---- 3) 파이프라인 통합 ----

# 7종 규칙 매처 어디에도 걸리지 않는 코드 (이중 루프 + 분기 누적)
CPP_UNKNOWN = """
#include <cstdio>
int main() {
    int n = 100;
    long long s = 0;
    for (int i = 0; i < n; i++) {
        for (int j = i; j < n; j++) {
            if ((i + j) % 3 == 0) { s += i; } else { s -= j; }
        }
    }
    printf("%lld\\n", s);
}
"""


def typed(code: str, dt: int = 80):
    return [EditOp(t=i * dt, op=0, pos=i, txt=ch) for i, ch in enumerate(code)]


def test_pipeline_unmatched():
    events = typed(CPP_UNKNOWN.strip() + "\n")
    result = analyze_session(events, lang="cpp")
    check("pipeline level full", result.analysis_level == "full", result.analysis_level)
    check("pipeline no rule pattern", result.patterns_detected == [], f"got={result.patterns_detected}")
    check(
        "pipeline unmatched segments produced",
        len(result.unmatched_segments) >= 1,
        f"n={len(result.unmatched_segments)}",
    )
    if result.unmatched_segments:
        seg = result.unmatched_segments[0]
        check("pipeline segment has diff events", len(seg.diff_events) >= MIN_SEGMENT_EVENTS)
        dumped = json.dumps([s.model_dump() for s in result.unmatched_segments])
        check("pipeline segment no code leak", "printf" not in dumped and '"s"' not in dumped)

    result2 = analyze_session(events, lang="cpp")
    check("pipeline unmatched deterministic", result.model_dump() == result2.model_dump())

    # timing_only 세션은 세그먼트 없음
    r3 = analyze_session(events, lang="brainfuck")
    check("pipeline timing_only no segments", r3.unmatched_segments == [])


# ---- 4) 분류기 입력 / 프롬프트 ----

def _sample_segments():
    return [
        UnmatchedSegment(
            segment_id="seg_0", t_start_ms=1000, t_end_ms=5000,
            diff_events=[
                DiffEvent(t_ms=1000, op="insert", node_type="while_statement",
                          parent_type="function_definition", depth=2, subtree_hash="a1f9c3", size_nodes=6),
                DiffEvent(t_ms=2000, op="insert", node_type="call_expression",
                          parent_type="while_statement", depth=3, subtree_hash="b7e021",
                          size_nodes=4, callee_is_self=True),
                DiffEvent(t_ms=3000, op="delete", node_type="for_statement",
                          parent_type="function_definition", depth=2, subtree_hash="c3d444", size_nodes=6),
            ],
        )
    ]


def test_classifier_prompt():
    segments = _sample_segments()
    ci = build_classifier_input("sid-1", segments, ["BFS"], lang="cpp", problem_id="p1", total_duration_ms=60_000)
    check("input meta unmatched ids", ci["session_meta"]["unmatched_segments"] == ["seg_0"])
    check("input known patterns", ci["session_meta"]["known_patterns_matched"] == ["BFS"])
    check("input taxonomy has OTHER", "OTHER" in ci["taxonomy"]["pattern_labels"])

    prompt = build_user_prompt("sid-1", ci)
    check("prompt contains taxonomy", "pattern_labels" in prompt)
    check("prompt contains segment", "seg_0" in prompt and "while_statement" in prompt)
    # addendum §3: 구조 정보만 — 프롬프트에 소스 코드 계열 텍스트가 없어야 한다
    check("prompt no source text", "int main" not in prompt and "printf" not in prompt)


# ---- 5) 출력 파싱 ----

_GOOD_OUTPUT = json.dumps({
    "segment_results": [{
        "segment_id": "seg_0",
        "pattern": "OTHER",
        "proposed_label": "recursive_rewrite",
        "pattern_confidence": 0.7,
        "pivot_type": "APPROACH_SWITCH",
        "pivot_confidence": 0.8,
        "evidence": {"diff_event_indices": [0, 1, 2], "reasoning": "for_statement 삭제 후 while_statement + 재귀 호출 신설"},
    }]
})


def test_parse_output():
    out = parse_output(_GOOD_OUTPUT)
    check("parse plain json", len(out.segment_results) == 1)
    out2 = parse_output(f"```json\n{_GOOD_OUTPUT}\n```")
    check("parse fenced json", len(out2.segment_results) == 1)
    for name, bad in (("garbage", "죄송합니다만..."), ("json array", "[1,2]"), ("wrong schema", '{"segment_results": "x"}')):
        try:
            parse_output(bad)
            check(f"parse rejects {name}", False)
        except ValueError:
            check(f"parse rejects {name}", True)


# ---- 6) 구조 grounding 검증기 (addendum §6) ----

def _result(**kw) -> SegmentResult:
    base = dict(
        segment_id="seg_0", pattern="OTHER", proposed_label="two_pointer_scan",
        pattern_confidence=0.7, pivot_type=None, pivot_confidence=0.0,
        evidence=SegmentEvidence(diff_event_indices=[0, 1], reasoning="ok"),
    )
    base.update(kw)
    return SegmentResult(**base)


def test_grounding():
    seg_map = {s.segment_id: s for s in _sample_segments()}

    ok, _ = verify_structural_grounding(_result(), seg_map)
    check("grounding valid passes", ok)

    ok, why = verify_structural_grounding(_result(segment_id="seg_99"), seg_map)
    check("grounding unknown segment fails", not ok, why)

    ok, why = verify_structural_grounding(
        _result(evidence=SegmentEvidence(diff_event_indices=[0, 7], reasoning="x")), seg_map
    )
    check("grounding index out of range fails", not ok, why)

    ok, why = verify_structural_grounding(_result(pattern="DIJKSTRA", proposed_label=None), seg_map)
    check("grounding non-taxonomy pattern fails", not ok, why)

    ok, why = verify_structural_grounding(_result(pattern="BFS"), seg_map)
    check("grounding non-OTHER with proposed_label fails", not ok, why)

    ok, _ = verify_structural_grounding(_result(pattern="BFS", proposed_label=None), seg_map)
    check("grounding taxonomy pattern passes", ok)

    ok, why = verify_structural_grounding(_result(proposed_label="Bad Label!"), seg_map)
    check("grounding non-snake-case proposed fails", not ok, why)

    ok, why = verify_structural_grounding(_result(pivot_type="MADE_UP"), seg_map)
    check("grounding non-taxonomy pivot fails", not ok, why)

    ok, why = verify_structural_grounding(_result(pattern_confidence=1.5), seg_map)
    check("grounding confidence bounds fails", not ok, why)


# ---- 7) classify_unmatched (재시도 / 폐기 / confidence 임계) ----

class FakeLLM:
    """VLLMClient 대역 — 준비된 응답을 순서대로 반환."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.last_kwargs = None

    async def chat(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResult(text=text, model="fake")


def _run(coro):
    return asyncio.run(coro)


def test_classify():
    segments = _sample_segments()

    # 정상 경로: 1회 호출로 통과 + temperature=0/seed/json 강제 확인
    fake = FakeLLM([_GOOD_OUTPUT])
    results = _run(classify_unmatched(fake, "sid-1", segments, []))
    check("classify accepts valid", len(results) == 1 and results[0].proposed_label == "recursive_rewrite")
    check("classify single call", fake.calls == 1, f"calls={fake.calls}")
    check(
        "classify call params (temp0/seed/json)",
        fake.last_kwargs.get("temperature") == 0.0
        and fake.last_kwargs.get("seed") is not None
        and fake.last_kwargs.get("json_mode") is True,
    )

    # 재시도: 처음 2회 실패(비JSON, grounding 위반) 후 3회째 성공
    bad_grounding = json.dumps({"segment_results": [{
        "segment_id": "seg_0", "pattern": "DIJKSTRA", "proposed_label": None,
        "pattern_confidence": 0.9, "pivot_type": None, "pivot_confidence": 0.0,
        "evidence": {"diff_event_indices": [0], "reasoning": "x"},
    }]})
    fake = FakeLLM(["not json", bad_grounding, _GOOD_OUTPUT])
    results = _run(classify_unmatched(fake, "sid-1", segments, []))
    check("classify retry then accept", len(results) == 1)
    check("classify retry count", fake.calls == 3, f"calls={fake.calls}")

    # 최종 실패: MAX_ATTEMPTS 모두 실패 → 폐기(UNMATCHED 유지)
    fake = FakeLLM(["not json"] * MAX_ATTEMPTS)
    results = _run(classify_unmatched(fake, "sid-1", segments, []))
    check("classify all-fail discards", results == [])
    check("classify max attempts", fake.calls == MAX_ATTEMPTS, f"calls={fake.calls}")

    # confidence 임계: 둘 다 임계 미만이면 후보로도 저장하지 않음 (§4 규칙 7)
    low_conf = json.dumps({"segment_results": [{
        "segment_id": "seg_0", "pattern": "OTHER", "proposed_label": "weak_guess",
        "pattern_confidence": CANDIDATE_MIN_CONFIDENCE - 0.2,
        "pivot_type": None, "pivot_confidence": 0.0,
        "evidence": {"diff_event_indices": [0], "reasoning": "x"},
    }]})
    fake = FakeLLM([low_conf])
    results = _run(classify_unmatched(fake, "sid-1", segments, []))
    check("classify low confidence dropped", results == [])

    # 세그먼트 없으면 LLM 호출 자체가 없어야 (addendum §2 호출 시점)
    fake = FakeLLM([])
    results = _run(classify_unmatched(fake, "sid-1", [], []))
    check("classify no segments no call", results == [] and fake.calls == 0)


# ---- 8) 저장 계층 (source 분리 / 세그먼트 영속화 / 멱등성) ----

def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _rule_result(with_segments: bool) -> AnalysisResult:
    return AnalysisResult(
        analysis_level="full", matcher_version=1, total_ms=60_000,
        pattern_windows=[PatternWindowResult(pattern="BFS", t_start_ms=0, t_complete_ms=10_000, formation_ms=10_000)],
        unmatched_segments=_sample_segments() if with_segments else [],
    )


def test_store():
    db = _make_db()

    rule_result = _rule_result(with_segments=True)
    save_analysis(db, sid="s1", user_id="u1", problem_id=1, lang="cpp", result=rule_result)

    # 세그먼트 영속화: 분류 전에는 pending
    seg_rows = db.query(UnmatchedSegmentRow).filter(UnmatchedSegmentRow.sid == "s1").all()
    check("store segments persisted pending",
          len(seg_rows) == 1 and seg_rows[0].status == "pending"
          and json.loads(seg_rows[0].diff_events_json)[0]["node_type"] == "while_statement")

    segments = _sample_segments()
    results = [parse_output(_GOOD_OUTPUT).segment_results[0]]
    save_llm_candidates(
        db, sid="s1", user_id="u1", problem_id=1,
        segments=segments, results=results, classifier_version=CLASSIFIER_VERSION,
    )

    # 채택된 세그먼트 → classified + 버전/라벨 기록
    seg = db.query(UnmatchedSegmentRow).filter(UnmatchedSegmentRow.sid == "s1").first()
    check("store segment classified",
          seg.status == "classified" and seg.proposed_label == "recursive_rewrite"
          and seg.classifier_version == CLASSIFIER_VERSION)

    rule_rows = db.query(PatternWindowRow).filter(PatternWindowRow.sid == "s1", PatternWindowRow.source == "rule").all()
    cand_rows = db.query(PatternWindowRow).filter(PatternWindowRow.sid == "s1", PatternWindowRow.source == "llm_candidate").all()
    check("store rule row source", len(rule_rows) == 1 and rule_rows[0].pattern == "BFS")
    check(
        "store candidate row",
        len(cand_rows) == 1 and cand_rows[0].proposed_label == "recursive_rewrite"
        and cand_rows[0].classifier_version == CLASSIFIER_VERSION
        and cand_rows[0].confidence == 0.7,
        f"rows={[(r.pattern, r.proposed_label) for r in cand_rows]}",
    )

    llm_pivots = db.query(PivotEventRow).filter(PivotEventRow.sid == "s1", PivotEventRow.source == "llm").all()
    check("store llm pivot row", len(llm_pivots) == 1 and llm_pivots[0].pivot_type == "APPROACH_SWITCH")

    # 멱등: 재실행해도 행 수 동일
    save_llm_candidates(
        db, sid="s1", user_id="u1", problem_id=1,
        segments=segments, results=results, classifier_version=CLASSIFIER_VERSION,
    )
    n_cand = db.query(PatternWindowRow).filter(PatternWindowRow.source == "llm_candidate").count()
    n_pivot = db.query(PivotEventRow).filter(PivotEventRow.source == "llm").count()
    check("store candidates idempotent", n_cand == 1 and n_pivot == 1, f"cand={n_cand}, pivot={n_pivot}")

    # 채택 0건이어도 상태는 discarded로 전이 (grounding 실패율 집계용)
    save_llm_candidates(
        db, sid="s1", user_id="u1", problem_id=1,
        segments=segments, results=[], classifier_version=CLASSIFIER_VERSION,
    )
    seg = db.query(UnmatchedSegmentRow).filter(UnmatchedSegmentRow.sid == "s1").first()
    n_cand = db.query(PatternWindowRow).filter(PatternWindowRow.source == "llm_candidate").count()
    check("store zero-accept discards", seg.status == "discarded" and n_cand == 0)

    # save_analysis 재실행(워커 재처리) → 후보·세그먼트 포함 전부 삭제 후 재삽입
    save_analysis(db, sid="s1", user_id="u1", problem_id=1, lang="cpp", result=rule_result)
    n_cand = db.query(PatternWindowRow).filter(PatternWindowRow.source == "llm_candidate").count()
    seg = db.query(UnmatchedSegmentRow).filter(UnmatchedSegmentRow.sid == "s1").first()
    check("store reprocess clears candidates", n_cand == 0 and seg.status == "pending", f"cand={n_cand}")
    db.close()


# ---- 9) 백필 (addendum §7) ----

def test_backfill():
    db = _make_db()
    save_analysis(db, sid="s1", user_id="u1", problem_id=1, lang="cpp", result=_rule_result(True))

    # pending 세그먼트 → stale 대상
    check("backfill stale includes pending", stale_session_ids(db) == ["s1"])

    # 구버전으로 분류된 세그먼트도 stale
    save_llm_candidates(
        db, sid="s1", user_id="u1", problem_id=1,
        segments=_sample_segments(), results=[parse_output(_GOOD_OUTPUT).segment_results[0]],
        classifier_version="p0+old-model",
    )
    check("backfill stale includes outdated version", stale_session_ids(db) == ["s1"])

    fake = FakeLLM([_GOOD_OUTPUT])
    summary = _run(backfill_classifier(db, client=fake))
    check(
        "backfill reclassifies",
        summary["sessions_processed"] == 1 and summary["candidates_accepted"] == 1,
        f"summary={summary}",
    )
    seg = db.query(UnmatchedSegmentRow).filter(UnmatchedSegmentRow.sid == "s1").first()
    check("backfill updates version",
          seg.status == "classified" and seg.classifier_version == CLASSIFIER_VERSION)
    check("backfill nothing stale after", stale_session_ids(db) == [])
    db.close()


# ---- 10) 검수·메트릭 (addendum §7~§8) ----

def test_review_metrics():
    db = _make_db()
    save_analysis(db, sid="s1", user_id="u1", problem_id=1, lang="cpp", result=_rule_result(True))
    save_analysis(db, sid="s2", user_id="u1", problem_id=1, lang="cpp", result=_rule_result(False))
    save_llm_candidates(
        db, sid="s1", user_id="u1", problem_id=1,
        segments=_sample_segments(), results=[parse_output(_GOOD_OUTPUT).segment_results[0]],
        classifier_version=CLASSIFIER_VERSION,
    )

    cands = aggregate_candidates(db)
    check(
        "review aggregate",
        len(cands) == 1 and cands[0]["proposed_label"] == "recursive_rewrite"
        and cands[0]["count"] == 1 and cands[0]["n_sessions"] == 1,
        f"cands={cands}",
    )
    check("review min-count filter", aggregate_candidates(db, min_count=2) == [])

    m = classifier_metrics(db, current_version=CLASSIFIER_VERSION)
    check(
        "metrics unmatched ratio (M3)",
        m["n_full_sessions"] == 2 and m["n_sessions_with_unmatched"] == 1
        and m["unmatched_session_ratio"] == 0.5,
        f"m={m}",
    )
    check(
        "metrics grounding rate (M3.5)",
        m["segments_classified"] == 1 and m["segments_discarded"] == 0
        and m["grounding_discard_rate"] == 0.0 and m["stale_segments"] == 0,
        f"m={m}",
    )
    db.close()


if __name__ == "__main__":
    test_diff_extractor()
    test_move_and_features()
    test_segment_extraction()
    test_pipeline_unmatched()
    test_classifier_prompt()
    test_parse_output()
    test_grounding()
    test_classify()
    test_store()
    test_backfill()
    test_review_metrics()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s): {FAILURES}")
        sys.exit(1)
    print("all checks passed")
