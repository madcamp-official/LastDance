"""AST 트리 변화 이력 파이프라인 + 영속화 검증.

실행: backend 디렉토리에서  python -m tests.test_ast_evolution
(pytest 없이도 도는 가벼운 스크립트형 테스트 — tests/test_worker.py와 동일 스타일)

실제 app.db를 건드리지 않도록 임시 sqlite 파일에 별도 엔진을 붙여 검사한다.
"""

import json
import os
import sys
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.model.ast_tree import AstDiffEventRow, AstSnapshotRow, AstTreeEvolutionRow
from app.schema.analysis import EditOp
from app.worker.pipeline import analyze_session
from app.worker.store import save_analysis

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "OK " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


CPP_BFS = """#include <bits/stdc++.h>
using namespace std;
int main() {
    int n; cin >> n;
    vector<vector<int>> g(n);
    vector<bool> vis(n, false);
    queue<int> q;
    q.push(0); vis[0] = true;
    while (!q.empty()) {
        int u = q.front(); q.pop();
        for (int v : g[u]) {
            if (!vis[v]) { vis[v] = true; q.push(v); }
        }
    }
    return 0;
}
"""


def typed(code: str, start_t: int = 0, dt: int = 100):
    return [EditOp(t=start_t + i * dt, op=0, pos=i, txt=ch) for i, ch in enumerate(code)]


def _session_events():
    """타이핑 후 for 루프 본문을 통째로 지웠다 다시 쓰는 세션 (구조 delete/insert 유발)."""
    events = typed(CPP_BFS)
    t = events[-1].t
    body = "if (!vis[v]) { vis[v] = true; q.push(v); }"
    idx = CPP_BFS.index(body)
    events.append(EditOp(t=t + 5000, op=1, pos=idx, len=len(body)))
    t += 5000
    replacement = "if (vis[v] == false) { vis[v] = true; q.push(v); }"
    for i, ch in enumerate(replacement):
        events.append(EditOp(t=t + 200 + i * 50, op=0, pos=idx + i, txt=ch))
    return events


def test_pipeline_records_evolution():
    result = analyze_session(_session_events(), "cpp")
    check("analysis level full", result.analysis_level == "full", result.analysis_level)

    ev = result.ast_evolution
    check("ast_evolution present", ev is not None)
    if ev is None:
        return None

    check("snapshots recorded", len(ev.snapshots) > 0, f"n={len(ev.snapshots)}")
    check("seq is 0..n-1", [s.seq for s in ev.snapshots] == list(range(len(ev.snapshots))))
    check("t_ms non-decreasing", all(
        ev.snapshots[i].t_ms <= ev.snapshots[i + 1].t_ms for i in range(len(ev.snapshots) - 1)
    ))
    check("first snapshot has only inserts (vs empty tree)",
          ev.snapshots[0].delete_count == 0 and ev.snapshots[0].move_count == 0)
    check("tree grows then holds", ev.peak_node_count >= ev.final_node_count > 0,
          f"peak={ev.peak_node_count} final={ev.final_node_count}")
    check("final tree has while/for structure",
          ev.snapshots[-1].node_type_counts.get("while_statement", 0) >= 1)
    check("diff counts match per-snapshot sums",
          ev.diff_event_count == sum(len(s.diff_events) for s in ev.snapshots)
          and ev.diff_event_count == ev.insert_count + ev.delete_count + ev.move_count,
          f"total={ev.diff_event_count} i/d/m={ev.insert_count}/{ev.delete_count}/{ev.move_count}")
    check("deletion produced delete events", ev.delete_count > 0, f"delete={ev.delete_count}")
    check("final_shape captured",
          ev.final_shape is not None and ev.final_shape.root_type == "function_definition")
    check("sketch_hash set on every snapshot", all(s.sketch_hash for s in ev.snapshots))
    return result


def test_degraded_still_records_evolution():
    """K<50 (degraded)여도 파서가 있으면 세션 종료 시점 트리를 남긴다."""
    short = typed("int main() { return 0; }")
    result = analyze_session(short, "cpp")
    check("degraded level", result.analysis_level == "degraded", result.analysis_level)

    ev = result.ast_evolution
    check("degraded has evolution", ev is not None)
    if ev is None:
        return
    check("degraded single snapshot", len(ev.snapshots) == 1, f"n={len(ev.snapshots)}")
    check("degraded tree non-empty", ev.final_node_count > 0, f"n={ev.final_node_count}")
    check("degraded all inserts", ev.delete_count == 0 and ev.move_count == 0)
    check("degraded shape captured",
          ev.final_shape is not None and ev.final_shape.root_type == "function_definition")


def test_no_parser_no_evolution():
    """파서 없는 언어(timing_only)는 트리 자체가 없으므로 None."""
    result = analyze_session(typed(CPP_BFS), "java")
    check("java is timing_only", result.analysis_level == "timing_only", result.analysis_level)
    check("no parser -> no evolution", result.ast_evolution is None)


def test_deterministic():
    a = analyze_session(_session_events(), "cpp").ast_evolution
    b = analyze_session(_session_events(), "cpp").ast_evolution
    check("evolution deterministic", a is not None and b is not None
          and a.model_dump_json() == b.model_dump_json())


def test_persistence(result):
    """save_analysis가 3테이블에 쓰고, 재실행해도 행 수가 안 늘어나는지(멱등)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)

    ev = result.ast_evolution
    try:
        db = Session()
        try:
            save_analysis(db, sid="sid-1", user_id="u1", problem_id=42, lang="cpp", result=result)
            head = db.query(AstTreeEvolutionRow).filter(AstTreeEvolutionRow.sid == "sid-1").first()
            snaps = db.query(AstSnapshotRow).filter(AstSnapshotRow.sid == "sid-1").all()
            diffs = db.query(AstDiffEventRow).filter(AstDiffEventRow.sid == "sid-1").all()

            check("evolution row written", head is not None)
            check("snapshot rows == snapshots", len(snaps) == len(ev.snapshots),
                  f"{len(snaps)} vs {len(ev.snapshots)}")
            check("diff rows == diff events", len(diffs) == ev.diff_event_count,
                  f"{len(diffs)} vs {ev.diff_event_count}")
            check("not truncated at this size", head is not None and not head.truncated)
            check("summary counts persisted",
                  head is not None
                  and head.insert_count == ev.insert_count
                  and head.delete_count == ev.delete_count
                  and head.move_count == ev.move_count
                  and head.snapshot_count == len(ev.snapshots))
            check("final_shape_json roundtrips",
                  head is not None and head.final_shape_json is not None
                  and json.loads(head.final_shape_json)["root_type"] == "function_definition")
            check("node_type_counts_json roundtrips",
                  json.loads(sorted(snaps, key=lambda s: s.seq)[-1].node_type_counts_json)
                  == ev.snapshots[-1].node_type_counts)
            check("diff rows carry snapshot_seq",
                  {d.snapshot_seq for d in diffs} <= {s.seq for s in ev.snapshots})
        finally:
            db.close()

        # 멱등: 같은 sid 재저장 시 중복 누적 없음
        db = Session()
        try:
            save_analysis(db, sid="sid-1", user_id="u1", problem_id=42, lang="cpp", result=result)
            check("idempotent snapshots",
                  db.query(AstSnapshotRow).filter(AstSnapshotRow.sid == "sid-1").count()
                  == len(ev.snapshots))
            check("idempotent diffs",
                  db.query(AstDiffEventRow).filter(AstDiffEventRow.sid == "sid-1").count()
                  == ev.diff_event_count)
            check("idempotent head row",
                  db.query(AstTreeEvolutionRow).filter(AstTreeEvolutionRow.sid == "sid-1").count() == 1)
        finally:
            db.close()

        # timing_only 세션(ast_evolution=None)은 기존 행을 지우고 아무것도 안 남긴다
        db = Session()
        try:
            result.ast_evolution = None
            save_analysis(db, sid="sid-1", user_id="u1", problem_id=42, lang="cpp", result=result)
            check("None evolution clears rows",
                  db.query(AstTreeEvolutionRow).filter(AstTreeEvolutionRow.sid == "sid-1").count() == 0
                  and db.query(AstSnapshotRow).filter(AstSnapshotRow.sid == "sid-1").count() == 0
                  and db.query(AstDiffEventRow).filter(AstDiffEventRow.sid == "sid-1").count() == 0)
        finally:
            db.close()
    finally:
        engine.dispose()
        os.unlink(path)


def main() -> int:
    result = test_pipeline_records_evolution()
    test_degraded_still_records_evolution()
    test_no_parser_no_evolution()
    test_deterministic()
    if result is not None:
        test_persistence(result)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
