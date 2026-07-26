from typing import Optional

# dev-plan §4.1 Step 3: pause 시점의 커서를 포함하는 최소 AST 노드에서
# 조상 체인을 따라 올라가며 라벨을 부여한다.

LABEL_INTERFACE_DESIGN = "INTERFACE_DESIGN"
LABEL_LOOP_BOUNDARY = "LOOP_BOUNDARY"
LABEL_BRANCH_CONDITION = "BRANCH_CONDITION"
LABEL_INDEX_REASONING = "INDEX_REASONING"
LABEL_ALGORITHM_ENTRY = "ALGORITHM_ENTRY"
LABEL_SETUP = "SETUP"
LABEL_SYNTAX_STRUGGLE = "SYNTAX_STRUGGLE"
LABEL_OTHER = "OTHER"   # 계획서 표에 없는 함수 본문 내부 일반 지점

_SUBSCRIPT_TYPES = {"subscript_expression", "subscript"}          # cpp / python
_LOOP_TYPES = {"for_statement", "while_statement", "for_range_loop", "do_statement"}
_FUNC_TYPES = {"function_definition", "function_declaration", "method_definition"}
_BODY_TYPES = {"compound_statement", "block"}                     # cpp / python
_PARAM_TYPES = {"function_declarator", "parameter_list", "parameters", "parameter_declaration"}
_TOP_TYPES = {"translation_unit", "module", "program", "source_file"}


def _in_field(child, parent, field_name: str) -> bool:
    target = parent.child_by_field_name(field_name)
    if target is None:
        return False
    return target.start_byte <= child.start_byte and child.end_byte <= target.end_byte


def label_context(tree, byte_offset: int) -> str:
    """byte_offset 위치의 AST 컨텍스트 라벨. tree가 None이면 빈 문자열."""
    if tree is None:
        return ""
    root = tree.root_node
    byte_offset = max(0, min(byte_offset, root.end_byte))
    node = root.descendant_for_byte_range(byte_offset, byte_offset)
    if node is None:
        return LABEL_SETUP

    # 1) ERROR 조상 우선 — 구문이 깨진 상태에서의 정체
    cursor = node
    while cursor is not None:
        if cursor.type == "ERROR" or cursor.is_missing:
            return LABEL_SYNTAX_STRUGGLE
        cursor = cursor.parent

    # 2) 안쪽에서 바깥으로 첫 매칭 라벨
    child = node
    parent = node.parent
    while parent is not None:
        if child.type in _SUBSCRIPT_TYPES or parent.type in _SUBSCRIPT_TYPES:
            return LABEL_INDEX_REASONING

        if parent.type == "if_statement" and _in_field(child, parent, "condition"):
            return LABEL_BRANCH_CONDITION

        if parent.type in _LOOP_TYPES:
            # cpp: condition 필드 / python for: left·right 헤더 전체를 경계로 본다
            if _in_field(child, parent, "condition") or (
                parent.type == "for_statement"
                and (_in_field(child, parent, "left") or _in_field(child, parent, "right"))
            ):
                return LABEL_LOOP_BOUNDARY

        if parent.type in _FUNC_TYPES and child.type in _PARAM_TYPES:
            return LABEL_INTERFACE_DESIGN
        if child.type in _PARAM_TYPES or parent.type in _PARAM_TYPES:
            return LABEL_INTERFACE_DESIGN

        if parent.type in _FUNC_TYPES and child.type in _BODY_TYPES:
            # 함수 본문 시작 직후(첫 문장 이전)인가
            first_stmt = next(iter(child.named_children), None)
            if first_stmt is None or byte_offset <= first_stmt.start_byte:
                return LABEL_ALGORITHM_ENTRY
            return LABEL_OTHER

        child, parent = parent, parent.parent

    # 3) 최상위(선언부/헤더)
    return LABEL_SETUP
