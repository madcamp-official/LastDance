"""Replay Worker 단위 검증.

실행: backend 디렉토리에서  python -m tests.test_worker
(pytest 없이도 도는 가벼운 스크립트형 테스트)
"""

import sys

from app.schema.analysis import EditOp
from app.worker.astsupport import load_parser
from app.worker.patterns import match_patterns
from app.worker.pipeline import analyze_session
from app.worker.replay import ReplayEngine

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "OK " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ---- 1) 재생 정확성 (M1: 바이트 단위 일치) ----
def typed(code: str, start_t: int = 0, dt: int = 100):
    """코드를 한 글자씩 타이핑한 이벤트 로그 생성."""
    return [
        EditOp(t=start_t + i * dt, op=0, pos=i, txt=ch)
        for i, ch in enumerate(code)
    ]


def test_replay_exact():
    code = 'int main() {\n    int a = 0;\n    return a;\n}\n'
    events = typed(code)
    # 중간 삭제 + 재삽입 섞기: "a = 0" 의 0을 지우고 42 삽입
    idx = code.index("0")
    t0 = events[-1].t
    events.append(EditOp(t=t0 + 200, op=1, pos=idx, len=1))
    events.append(EditOp(t=t0 + 400, op=0, pos=idx, txt="42"))
    expected = code[:idx] + "42" + code[idx + 1 :]

    engine = ReplayEngine(parser=load_parser("cpp"))
    engine.replay_all(events)
    check("replay byte-exact", engine.buffer.text() == expected)


# ---- 2) 패턴 매처 스팟체크 (알려진 알고리즘 코드) ----
CPP_BFS = """
#include <bits/stdc++.h>
using namespace std;
int main() {
    int n; cin >> n;
    vector<vector<int>> g(n);
    vector<bool> vis(n, false);
    queue<int> q;
    q.push(0); vis[0] = true;
    while (!q.empty()) {
        int u = q.front(); q.pop();
        for (int v : g[u]) if (!vis[v]) { vis[v] = true; q.push(v); }
    }
}
"""

CPP_BINSEARCH = """
int main() {
    int lo = 0, hi = 100;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (ok(mid)) hi = mid; else lo = mid + 1;
    }
}
"""

CPP_DP = """
int main() {
    int dp[100];
    dp[0] = 1;
    for (int i = 1; i < 100; i++) dp[i] = dp[i-1] + dp[i-2];
}
"""

PY_DFS = """
import sys
sys.setrecursionlimit(10**6)
vis = [False] * 100
def dfs(u):
    vis[u] = True
    for v in g[u]:
        if not vis[v]:
            dfs(v)
dfs(0)
"""

CPP_DSU = """
int p[100];
int find(int x) {
    while (p[x] != x) x = p[x];
    return x;
}
int main() {
    for (int i = 0; i < 100; i++) p[i] = i;
}
"""

PY_GREEDY = """
a = sorted(map(int, input().split()))
total = 0
for x in a:
    total += x
print(total)
"""


def run_matcher(code: str, lang: str):
    parser = load_parser(lang)
    tree = parser.parse(code.encode("utf-8"))
    return {m.pattern for m in match_patterns(tree, code.encode("utf-8"), lang)}


def test_patterns():
    cases = [
        ("BFS cpp", CPP_BFS, "cpp", "BFS"),
        ("BINARY_SEARCH cpp", CPP_BINSEARCH, "cpp", "BINARY_SEARCH"),
        ("DP cpp", CPP_DP, "cpp", "DP"),
        ("DFS_RECURSIVE py", PY_DFS, "python", "DFS_RECURSIVE"),
        ("DSU cpp", CPP_DSU, "cpp", "DSU"),
        ("GREEDY py", PY_GREEDY, "python", "GREEDY"),
    ]
    for name, code, lang, expected in cases:
        got = run_matcher(code, lang)
        check(f"pattern {name}", expected in got, f"got={got}")
    # 오탐 체크: hello world에서 아무 패턴도 안 나와야
    got = run_matcher('#include <cstdio>\nint main() { printf("hi"); }\n', "cpp")
    check("pattern negative (hello world)", got == set(), f"got={got}")


# ---- 3) 파이프라인 전 구간 (합성 세션) ----
def test_pipeline():
    # BFS 코드를 타이핑하되, 중간에 큰 pause와 삭제 버스트 삽입
    events = typed(CPP_BFS.strip() + "\n", dt=80)
    t_last = events[-1].t
    # 5초 pause 후 마지막에 주석 추가 (pause 1개 이상 보장)
    tail = "// done\n"
    base = len(CPP_BFS.strip() + "\n")
    for i, ch in enumerate(tail):
        events.append(EditOp(t=t_last + 5000 + i * 80, op=0, pos=base + i, txt=ch))

    result = analyze_session(events, lang="cpp", submission_ts_ms=[t_last + 6000])
    check("pipeline level full", result.analysis_level == "full", result.analysis_level)
    check("pipeline replay exact", result.final_code == CPP_BFS.strip() + "\n" + tail)
    check("pipeline pause detected", result.pause_count >= 1, f"count={result.pause_count}")
    check("pipeline BFS detected", "BFS" in result.patterns_detected, f"got={result.patterns_detected}")
    check(
        "pipeline formation window",
        any(w.pattern == "BFS" and w.formation_ms > 0 for w in result.pattern_windows),
        f"windows={[(w.pattern, w.formation_ms) for w in result.pattern_windows]}",
    )
    check("pipeline phases sum sane", result.setup_ms >= 0 and result.total_ms > 0)

    # 멱등성: 같은 입력 → 같은 출력
    result2 = analyze_session(events, lang="cpp", submission_ts_ms=[t_last + 6000])
    check("pipeline deterministic", result.model_dump() == result2.model_dump())

    # 미지원 언어 → timing_only
    r3 = analyze_session(events, lang="brainfuck")
    check("pipeline timing_only fallback", r3.analysis_level == "timing_only", r3.analysis_level)

    # 이벤트 극소 세션 → degraded
    r4 = analyze_session(events[:10], lang="cpp")
    check("pipeline degraded (K<50)", r4.analysis_level == "degraded", r4.analysis_level)


if __name__ == "__main__":
    test_replay_exact()
    test_patterns()
    test_pipeline()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s): {FAILURES}")
        sys.exit(1)
    print("all checks passed")
