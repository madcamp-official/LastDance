"""구조 패턴 매처 (dev-plan §4.1 Step 5, matcher_version은 worker/__init__.py).

함수·변수 이름의 '의미'에 의존하지 않고 제어 흐름 + 자료구조 사용 형태로
알고리즘을 식별한다. (동일 변수 추적을 위한 식별자 '동일성' 비교는 허용)
cpp / python 지원. 미지원 언어는 pipeline이 timing_only로 강등한다.
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from app.schema.analysis import PatternMatch
from app.worker.astsupport import collect_by_type, node_text, walk_nodes

PATTERNS = [
    "BFS", "DFS_RECURSIVE", "DFS_ITERATIVE", "BINARY_SEARCH", "DP", "GREEDY", "DSU",
]

_QUEUE_TYPES_RE = re.compile(r"\b(queue|deque|priority_queue)\b")
_STACK_TYPES_RE = re.compile(r"\bstack\b")


def _ranges(*nodes) -> List[Tuple[int, int]]:
    return [(n.start_byte, n.end_byte) for n in nodes if n is not None]


def _ident_of_declarator(decl_node, src: bytes) -> Optional[str]:
    """cpp declaration에서 선언된 변수 이름 후보를 뽑는다."""
    for n in walk_nodes(decl_node):
        if n.type == "identifier":
            return node_text(n, src)
    return None


def _contains(outer, inner) -> bool:
    return outer.start_byte <= inner.start_byte and inner.end_byte <= outer.end_byte


class _Ctx:
    """매칭 1회에 필요한 노드 인덱스 (한 번 걷고 재사용)."""

    def __init__(self, root, src: bytes, lang: str) -> None:
        self.root = root
        self.src = src
        self.lang = lang
        self.all_nodes = list(walk_nodes(root))
        self.funcs = [n for n in self.all_nodes if n.type == "function_definition"]
        self.whiles = [n for n in self.all_nodes if n.type == "while_statement"]
        self.fors = [
            n for n in self.all_nodes
            if n.type in ("for_statement", "for_range_loop")
        ]
        self.calls = [n for n in self.all_nodes if n.type in ("call_expression", "call")]
        self.subscripts = [
            n for n in self.all_nodes if n.type in ("subscript_expression", "subscript")
        ]
        self.assigns = [
            n for n in self.all_nodes
            if n.type in ("assignment_expression", "assignment", "augmented_assignment", "init_declarator")
        ]


# ---- 개별 매처 ----

def _cpp_container_decls(ctx: _Ctx, type_re) -> Dict[str, "object"]:
    """타입 텍스트가 정규식에 맞는 cpp 선언 → {변수명: 선언 노드}."""
    out: Dict[str, object] = {}
    for n in ctx.all_nodes:
        if n.type != "declaration":
            continue
        type_node = n.child_by_field_name("type")
        if type_node is None:
            continue
        if type_re.search(node_text(type_node, ctx.src)):
            name = _ident_of_declarator(n, ctx.src)
            if name:
                out[name] = n
    return out


def _py_container_assigns(ctx: _Ctx, ctor_names: Set[str]) -> Dict[str, "object"]:
    """python `x = deque(...)` 형태 → {변수명: 대입 노드}."""
    out: Dict[str, object] = {}
    for n in ctx.assigns:
        if n.type != "assignment":
            continue
        left = n.child_by_field_name("left")
        right = n.child_by_field_name("right")
        if left is None or right is None or left.type != "identifier":
            continue
        if right.type == "call":
            fn = right.child_by_field_name("function")
            if fn is not None and node_text(fn, ctx.src).split(".")[-1] in ctor_names:
                out[node_text(left, ctx.src)] = n
        elif right.type == "list" and len(right.named_children) == 0:
            out[node_text(left, ctx.src)] = n  # 빈 리스트도 큐/스택 후보
    return out


def _pushes_in(ctx: _Ctx, loop, var: str, methods: Set[str]) -> bool:
    """loop 내부에서 var.push(...) 류 호출이 있는가."""
    for c in ctx.calls:
        if not _contains(loop, c):
            continue
        fn = c.child_by_field_name("function")
        if fn is None:
            continue
        text = node_text(fn, ctx.src)
        if "." in text:
            recv, meth = text.rsplit(".", 1)
        elif "->" in text:
            recv, meth = text.rsplit("->", 1)
        else:
            continue
        if recv.strip() == var and meth.strip() in methods:
            return True
    return False


def _match_bfs(ctx: _Ctx) -> Optional[PatternMatch]:
    # 큐 계열 컨테이너 + while + 루프 내부 push + 배열 인덱싱(방문 체크)
    if ctx.lang == "cpp":
        queues = _cpp_container_decls(ctx, _QUEUE_TYPES_RE)
        push_methods = {"push", "emplace", "push_back", "push_front"}
    else:
        queues = _py_container_assigns(ctx, {"deque", "Queue"})
        push_methods = {"append", "appendleft", "put"}
    for var, decl in queues.items():
        for w in ctx.whiles:
            if not _pushes_in(ctx, w, var, push_methods):
                continue
            if any(_contains(w, s) for s in ctx.subscripts):
                return PatternMatch(pattern="BFS", ranges=_ranges(decl, w))
    return None


def _self_recursive_funcs(ctx: _Ctx) -> List[Tuple[object, str]]:
    out = []
    for f in ctx.funcs:
        if ctx.lang == "cpp":
            decl = f.child_by_field_name("declarator")
            name = _ident_of_declarator(decl, ctx.src) if decl is not None else None
        else:
            name_node = f.child_by_field_name("name")
            name = node_text(name_node, ctx.src) if name_node is not None else None
        if not name:
            continue
        body = f.child_by_field_name("body")
        if body is None:
            continue
        for c in ctx.calls:
            if not _contains(body, c):
                continue
            fn = c.child_by_field_name("function")
            if fn is not None and node_text(fn, ctx.src) == name:
                out.append((f, name))
                break
    return out


def _match_dfs_recursive(ctx: _Ctx) -> Optional[PatternMatch]:
    # 자기 자신 호출 + 함수 내부 배열 원소 대입 (방문 마킹)
    for f, _name in _self_recursive_funcs(ctx):
        for a in ctx.assigns:
            if not _contains(f, a):
                continue
            left = a.child_by_field_name("left")
            if left is not None and left.type in ("subscript_expression", "subscript"):
                return PatternMatch(pattern="DFS_RECURSIVE", ranges=_ranges(f))
    return None


def _match_dfs_iterative(ctx: _Ctx) -> Optional[PatternMatch]:
    # 스택 사용(push/pop 말단) + while + 방문 배열
    if ctx.lang == "cpp":
        stacks = _cpp_container_decls(ctx, _STACK_TYPES_RE)
        push_methods = {"push", "emplace", "push_back"}
    else:
        stacks = _py_container_assigns(ctx, {"list"})
        push_methods = {"append"}
    for var, decl in stacks.items():
        for w in ctx.whiles:
            if not _pushes_in(ctx, w, var, push_methods):
                continue
            if any(_contains(w, s) for s in ctx.subscripts):
                return PatternMatch(pattern="DFS_ITERATIVE", ranges=_ranges(decl, w))
    return None


_MID_RE = re.compile(r"(/\s*2|>>\s*1)")


def _match_binary_search(ctx: _Ctx) -> Optional[PatternMatch]:
    # while (a < b) + 내부 중간값 계산 + a 또는 b 한쪽 갱신
    for w in ctx.whiles:
        cond = w.child_by_field_name("condition")
        if cond is None:
            continue
        cond_text = node_text(cond, ctx.src)
        m = re.search(r"\b(\w+)\s*(?:<=?|-)\s*(\w+)", cond_text)
        if m is None:
            continue
        a, b = m.group(1), m.group(2)
        if not (a.isidentifier() and b.isidentifier()):
            continue
        body = w.child_by_field_name("body") or w
        body_text = node_text(body, ctx.src)
        # 중간값: a, b 둘 다 등장 + /2 또는 >>1
        has_mid = False
        for line in body_text.splitlines():
            if a in line and b in line and _MID_RE.search(line):
                has_mid = True
                break
        if not has_mid:
            continue
        # 한쪽 갱신: a 또는 b에 대한 대입
        updates = re.findall(rf"\b({re.escape(a)}|{re.escape(b)})\s*=[^=]", body_text)
        if updates:
            return PatternMatch(pattern="BINARY_SEARCH", ranges=_ranges(w))
    return None


_INDEX_STRIP_RE = re.compile(r"\s+")


def _subscript_parts(sub, ctx: _Ctx) -> Optional[Tuple[str, str]]:
    """subscript 노드 → (배열 텍스트, 인덱스 텍스트)."""
    if ctx.lang == "cpp":
        arr = sub.child_by_field_name("argument")
        idx = sub.child_by_field_name("index") or sub.child_by_field_name("indices")
    else:
        arr = sub.child_by_field_name("value")
        idx = sub.child_by_field_name("subscript")
    if arr is None or idx is None:
        return None
    idx_text = _INDEX_STRIP_RE.sub("", node_text(idx, ctx.src))
    # cpp 최신 문법은 indices 필드가 "[i]" 브래킷을 포함한다
    idx_text = idx_text.strip("[]")
    return (
        _INDEX_STRIP_RE.sub("", node_text(arr, ctx.src)),
        idx_text,
    )


def _match_dp(ctx: _Ctx) -> Optional[PatternMatch]:
    # X[i] = ... X[j] ... (동일 배열의 다른 인덱스 참조)
    for a in ctx.assigns:
        left = a.child_by_field_name("left")
        right = a.child_by_field_name("right") or a.child_by_field_name("value")
        if left is None or right is None:
            continue
        if left.type not in ("subscript_expression", "subscript"):
            continue
        lhs = _subscript_parts(left, ctx)
        if lhs is None:
            continue
        arr_l, idx_l = lhs
        for sub in ctx.subscripts:
            if not _contains(right, sub):
                continue
            rhs = _subscript_parts(sub, ctx)
            if rhs is None:
                continue
            arr_r, idx_r = rhs
            if arr_r == arr_l and idx_r != idx_l:
                return PatternMatch(pattern="DP", ranges=_ranges(a))
    return None


def _match_greedy(ctx: _Ctx) -> Optional[PatternMatch]:
    # 정렬 호출 직후 단일 패스 루프, 재귀 없음
    if _self_recursive_funcs(ctx):
        return None
    sort_call = None
    for c in ctx.calls:
        fn = c.child_by_field_name("function")
        if fn is None:
            continue
        name = node_text(fn, ctx.src).split("::")[-1].split(".")[-1]
        if name in ("sort", "stable_sort", "sorted"):
            sort_call = c
            break
    if sort_call is None:
        return None
    loops = ctx.fors + ctx.whiles
    after = [l for l in loops if l.start_byte >= sort_call.end_byte]
    if after:
        first = min(after, key=lambda n: n.start_byte)
        return PatternMatch(pattern="GREEDY", ranges=_ranges(sort_call, first))
    return None


def _match_dsu(ctx: _Ctx) -> Optional[PatternMatch]:
    # p[i] = i 자기참조 초기화 + 그 배열을 따라 올라가는 접근 (x = p[x] / p[x] != x)
    init_arrays: Dict[str, object] = {}
    for a in ctx.assigns:
        left = a.child_by_field_name("left")
        right = a.child_by_field_name("right") or a.child_by_field_name("value")
        if left is None or right is None:
            continue
        if left.type not in ("subscript_expression", "subscript"):
            continue
        parts = _subscript_parts(left, ctx)
        if parts is None:
            continue
        arr, idx = parts
        if node_text(right, ctx.src).strip() == idx:
            init_arrays[arr] = a
    if not init_arrays:
        # python: p = list(range(n)) 형태도 자기참조 초기화
        for a in ctx.assigns:
            if a.type != "assignment":
                continue
            left = a.child_by_field_name("left")
            right = a.child_by_field_name("right")
            if left is None or right is None or left.type != "identifier":
                continue
            if re.fullmatch(r"list\(range\([^)]*\)\)", node_text(right, ctx.src).replace(" ", "")):
                init_arrays[node_text(left, ctx.src)] = a
    for arr, init in init_arrays.items():
        pat_up = re.compile(rf"\b(\w+)\s*=\s*{re.escape(arr)}\[\s*\1\s*\]")
        pat_cmp = re.compile(rf"{re.escape(arr)}\[\s*(\w+)\s*\]\s*[!=]=\s*\1\b")
        src_text = ctx.src.decode("utf-8", errors="replace")
        if pat_up.search(src_text) or pat_cmp.search(src_text):
            return PatternMatch(pattern="DSU", ranges=_ranges(init))
    return None


_MATCHERS = [
    _match_bfs,
    _match_dfs_recursive,
    _match_dfs_iterative,
    _match_binary_search,
    _match_dp,
    _match_greedy,
    _match_dsu,
]


def match_patterns(tree, source: bytes, lang: str) -> List[PatternMatch]:
    """트리 1개에 대해 7종 패턴 매칭. lang은 normalize_lang 결과 ("cpp"/"python")."""
    if tree is None or lang not in ("cpp", "python"):
        return []
    ctx = _Ctx(tree.root_node, source, lang)
    out: List[PatternMatch] = []
    for matcher in _MATCHERS:
        m = matcher(ctx)
        if m is not None:
            out.append(m)
    return out


# ---- 기준선 추출용 구조 통계 (dev-plan §5.1의 40차원 피처 일부) ----

def structure_stats(tree, source: bytes, lang: str) -> Dict[str, object]:
    if tree is None:
        return {}
    ctx = _Ctx(tree.root_node, source, lang)
    loops = ctx.fors + ctx.whiles

    def loop_depth(node) -> int:
        depth = 0
        cursor = node.parent
        while cursor is not None:
            if cursor.type in ("for_statement", "for_range_loop", "while_statement"):
                depth += 1
            cursor = cursor.parent
        return depth

    max_depth = max((loop_depth(l) + 1 for l in loops), default=0)
    return {
        "loop_count": len(loops),
        "max_loop_depth": max_depth,
        "has_recursion": bool(_self_recursive_funcs(ctx)),
        "function_count": len(ctx.funcs),
        "subscript_count": len(ctx.subscripts),
    }
