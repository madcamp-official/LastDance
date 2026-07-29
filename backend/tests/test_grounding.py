"""Grounding 검증기 단위 검증 (keystroke-analysis-dev-plan.md §7.3).

실행: backend 디렉토리에서  python -m tests.test_grounding
(pytest 없이도 도는 가벼운 스크립트형 테스트 — tests/test_worker.py와 동일 스타일)
"""

import sys

from app.llm.grounding import build_template_feedback, verify_grounding
from app.model.analysis import PauseEventRow

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "OK " if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ---- verify_grounding ----

def test_grounded_number_passes():
    prompt = "[정지] 총 3회, 누적 1200ms"
    ok, ungrounded = verify_grounding("정지가 3회, 1200ms 발생했습니다.", prompt)
    check("입력 값 그대로 인용 -> grounded", ok, str(ungrounded))


def test_unit_conversion_allowed():
    prompt = "[시간 분포 ms] 형성(formation)=65000"
    # 65000ms == 65s == 약 1.08분. 초 단위 변환은 허용 목록.
    ok, ungrounded = verify_grounding("형성 단계에 약 65초가 걸렸습니다.", prompt)
    check("ms->s 단위 변환 허용", ok, str(ungrounded))


def test_hallucinated_number_fails():
    prompt = "[정지] 총 3회, 누적 1200ms"
    ok, ungrounded = verify_grounding("정지가 무려 47회나 발생했습니다.", prompt)
    check("입력에 없는 숫자 -> ungrounded 검출", not ok and 47.0 in ungrounded, str(ungrounded))


def test_no_numbers_in_response_passes():
    prompt = "[정지] 총 3회, 누적 1200ms"
    ok, ungrounded = verify_grounding("정지가 자주 발생하는 편입니다.", prompt)
    check("숫자 없는 응답은 항상 통과", ok, str(ungrounded))


def test_rounding_tolerance():
    prompt = "[시간 분포 ms] 총=12345"
    ok, ungrounded = verify_grounding("총 12344ms가 걸렸습니다.", prompt)
    check("반올림 오차 ±1 허용", ok, str(ungrounded))


# ---- build_template_feedback ----

def _summary_stub(**overrides):
    class _S:
        pass

    s = _S()
    s.setup_ms = 1000
    s.formation_ms = 5000
    s.debug_ms = 2000
    s.refine_ms = 500
    s.pause_count = 2
    s.pause_total_ms = 3000
    s.pivot_count = 1
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_template_feedback_no_summary():
    text = build_template_feedback(None, None, [], [], [], None, None)
    check("summary 없으면 안내 문구만 반환", "충분하지 않아" in text, text)


def test_template_feedback_uses_real_data_only():
    summary = _summary_stub()
    pauses = [
        PauseEventRow(
            id=1, sid="s1", user_id="u1", t_ms=100, duration_ms=900,
            ast_label="LOOP", pattern="이분탐색", phase="formation",
        )
    ]
    text = build_template_feedback(None, summary, pauses, [], [], None, None)
    ok, ungrounded = verify_grounding(text, "형성(formation)=5000 정지=900 이분탐색")
    check("템플릿 폴백 문장은 항상 grounding 통과", ok, f"text={text} ungrounded={ungrounded}")
    check("가장 긴 단계(formation) 언급", "형성(formation)" in text, text)
    check("최다 정지 패턴(이분탐색) 언급", "이분탐색" in text, text)


def run() -> None:
    test_grounded_number_passes()
    test_unit_conversion_allowed()
    test_hallucinated_number_fails()
    test_no_numbers_in_response_passes()
    test_rounding_tolerance()
    test_template_feedback_no_summary()
    test_template_feedback_uses_real_data_only()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
    else:
        print("ALL PASSED")


if __name__ == "__main__":
    run()
    sys.exit(1 if FAILURES else 0)
