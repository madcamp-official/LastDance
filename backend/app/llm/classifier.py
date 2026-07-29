"""LLM Structural Classifier (llm-structural-classifier-addendum.md §3~§6).

규칙 매처(Step 5)가 UNMATCHED로 남긴 구간의 구조 diff 이벤트만 LLM에 넘겨
분류시킨다. 입력은 결정론적으로 추출된 구조 이벤트뿐 — 원본 코드/변수명 없음.
출력은 고정 JSON 스키마이며 구조 grounding 검증(§6)을 통과한 세그먼트 결과만
후보(llm_candidate)로 하류에 반영된다. 실패 세그먼트는 UNMATCHED로 유지.
"""

import json
import logging
import re
from typing import List, Optional, Tuple

from pydantic import BaseModel, ValidationError

from app.llm.client import LLM_MODEL, VLLMClient
from app.schema.analysis import UnmatchedSegment

logger = logging.getLogger("app.llm.classifier")

# addendum §7: 프롬프트 버전 + 모델 버전 조합. 바뀌면 과거 llm_candidate 행과
# 비교 불가 → 재처리 백필 대상 (기존 matcher_version과 동일한 사상).
PROMPT_VERSION = 1
CLASSIFIER_VERSION = f"p{PROMPT_VERSION}+{LLM_MODEL}"

# addendum §3 taxonomy — 규칙 매처(app.worker.patterns.PATTERNS)의 7종 + OTHER,
# pivot 유형(app.worker.pivot)의 4종 + OTHER.
PATTERN_LABELS = [
    "BFS", "DFS_RECURSIVE", "DFS_ITERATIVE", "BINARY_SEARCH", "DP", "GREEDY", "DSU", "OTHER",
]
PIVOT_LABELS = ["APPROACH_SWITCH", "COMPLEXITY_FIX", "EDGE_CASE_FIX", "TYPO", "OTHER"]

MAX_ATTEMPTS = 3               # §6.5: 3회 시도 후 실패 세그먼트는 UNMATCHED 유지
CANDIDATE_MIN_CONFIDENCE = 0.6  # §4 규칙 7: 이 미만이면 후보로도 저장하지 않음
_SEED = 20260729
_PROPOSED_LABEL_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class SegmentEvidence(BaseModel):
    diff_event_indices: List[int] = []
    reasoning: str = ""


class SegmentResult(BaseModel):
    segment_id: str
    pattern: str
    proposed_label: Optional[str] = None
    pattern_confidence: float = 0.0
    pivot_type: Optional[str] = None
    pivot_confidence: float = 0.0
    evidence: SegmentEvidence = SegmentEvidence()


class ClassifierOutput(BaseModel):
    segment_results: List[SegmentResult] = []


_SYSTEM_PROMPT = """당신은 프로그래밍 학습 분석 시스템의 구조 분류기입니다.
당신에게는 소스 코드 원문이 아니라, 결정론적으로 계산된 AST 구조 변화 이벤트만 주어집니다.

[절대 규칙]
1. 입력에 없는 노드 타입, 해시, 시각을 지어내지 마십시오.
2. 판단의 근거가 된 diff_events의 인덱스를 반드시 evidence 필드에 명시하십시오.
   evidence에 없는 이벤트를 근거로 주장하지 마십시오.
3. taxonomy.pattern_labels / pivot_labels에 없는 새 이름을 만들고 싶다면,
   pattern 필드는 "OTHER"로 두고 proposed_label에만 새 이름(스네이크케이스, 영문)을 적으십시오.
   OTHER가 아닌 라벨을 자의로 신설하지 마십시오.
4. 변수/함수 이름에 대한 정보가 없으므로, 이름 기반 추론(예: "함수명이 dfs라서 DFS")을 하지 마십시오.
   오직 제어 흐름 구조(재귀 호출 존재 여부, 반복문 종류, 스택/큐 형태 컨테이너 사용,
   조건식 갱신 패턴 등)로만 판단하십시오.
5. 하나의 segment는 최대 1개의 pattern과 0~1개의 pivot_type만 가집니다.
   애매하면 낮은 confidence와 함께 OTHER를 선택하십시오. 무리하게 기존 7종에 끼워 맞추지 마십시오.
6. 출력은 아래 JSON 스키마를 엄격히 따르십시오. 스키마 외 텍스트(설명, 인사말, 마크다운 fence)를
   출력하지 마십시오.
7. confidence는 0.0~1.0. 0.6 미만이면 하류 파이프라인이 이 결과를 후보로만 취급하고
   기준선 통계에 반영하지 않습니다. 과신하지 말고 근거가 약하면 낮게 매기십시오.

[출력 JSON 스키마]
{
  "segment_results": [
    {
      "segment_id": "string, 입력의 segment_id와 동일",
      "pattern": "OTHER 또는 taxonomy.pattern_labels 중 하나",
      "proposed_label": "pattern이 OTHER일 때만, 아니면 null",
      "pattern_confidence": 0.0,
      "pivot_type": "OTHER 또는 taxonomy.pivot_labels 중 하나, 없으면 null",
      "pivot_confidence": 0.0,
      "evidence": {
        "diff_event_indices": [0, 1, 2],
        "reasoning": "20단어 이내, 구조적 근거만 서술 (자연어 설명이 아니라 판정 사유)"
      }
    }
  ]
}"""


def build_classifier_input(
    session_id: str,
    segments: List[UnmatchedSegment],
    known_patterns: List[str],
    lang: Optional[str] = None,
    problem_id: Optional[str] = None,
    total_duration_ms: int = 0,
) -> dict:
    """addendum §3 입력 JSON. 전 필드 결정론적 — 코드/식별자 텍스트 없음."""
    return {
        "session_meta": {
            "problem_id": problem_id or "",
            "lang": lang or "",
            "total_duration_ms": total_duration_ms,
            "known_patterns_matched": sorted(known_patterns),
            "unmatched_segments": [s.segment_id for s in segments],
        },
        "taxonomy": {
            "pattern_labels": PATTERN_LABELS,
            "pivot_labels": PIVOT_LABELS,
        },
        "segments": [s.model_dump() for s in segments],
    }


def build_user_prompt(session_id: str, classifier_input: dict) -> str:
    """addendum §5 유저 프롬프트 템플릿."""
    taxonomy = json.dumps(classifier_input["taxonomy"], ensure_ascii=False)
    segments = json.dumps(classifier_input["segments"], ensure_ascii=False)
    return (
        f"다음은 세션 {session_id}의 미매칭 구조 변화 구간입니다.\n"
        f"taxonomy: {taxonomy}\n"
        f"segments: {segments}\n\n"
        "위 세그먼트 각각을 시스템 프롬프트의 규칙에 따라 분류하고,\n"
        "지정된 JSON 스키마로만 응답하십시오."
    )


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_output(text: str) -> ClassifierOutput:
    """스키마 위반이면 ValueError — 호출부가 재시도한다."""
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    try:
        return ClassifierOutput(**json.loads(cleaned))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ValueError(f"classifier output parse failed: {exc}") from exc


def verify_structural_grounding(
    result: SegmentResult,
    segments_by_id: dict,
) -> Tuple[bool, str]:
    """addendum §6 구조 grounding 검증 (세그먼트 단위). (통과 여부, 실패 사유)."""
    seg = segments_by_id.get(result.segment_id)
    if seg is None:
        return False, f"unknown segment_id {result.segment_id!r}"

    # §6.1: evidence 인덱스가 diff_events 배열 범위 안인가
    n = len(seg.diff_events)
    bad = [i for i in result.evidence.diff_event_indices if i < 0 or i >= n]
    if bad:
        return False, f"evidence indices out of range: {bad} (n={n})"

    # §6.2: taxonomy에 없는 라벨
    if result.pattern not in PATTERN_LABELS:
        return False, f"pattern {result.pattern!r} not in taxonomy"
    if result.pivot_type is not None and result.pivot_type not in PIVOT_LABELS:
        return False, f"pivot_type {result.pivot_type!r} not in taxonomy"

    # §6.3: OTHER가 아닌데 proposed_label이 있으면 스키마 위반
    if result.pattern != "OTHER" and result.proposed_label is not None:
        return False, "proposed_label set but pattern != OTHER"
    if result.proposed_label is not None and not _PROPOSED_LABEL_RE.fullmatch(result.proposed_label):
        return False, f"proposed_label {result.proposed_label!r} not snake_case"

    if not (0.0 <= result.pattern_confidence <= 1.0 and 0.0 <= result.pivot_confidence <= 1.0):
        return False, "confidence out of [0, 1]"

    # §6.4: reasoning이 언급한 노드 타입이 evidence 이벤트와 겹치는지 — 경고만
    evidence_types = {seg.diff_events[i].node_type for i in result.evidence.diff_event_indices}
    mentioned = {t for t in {e.node_type for e in seg.diff_events} if t in result.evidence.reasoning}
    if mentioned and not (mentioned & evidence_types):
        logger.warning(
            "classifier reasoning mentions node types %s not in evidence (segment=%s)",
            mentioned, result.segment_id,
        )
    return True, ""


async def classify_unmatched(
    client: VLLMClient,
    session_id: str,
    segments: List[UnmatchedSegment],
    known_patterns: List[str],
    lang: Optional[str] = None,
    problem_id: Optional[str] = None,
    total_duration_ms: int = 0,
) -> List[SegmentResult]:
    """UNMATCHED 세그먼트 분류. grounding 통과 + confidence 임계 이상 결과만 반환.

    §6.5: MAX_ATTEMPTS회 안에 통과하지 못한 세그먼트는 UNMATCHED로 유지(결과에서 제외).
    LLMUnavailable은 그대로 전파 — 호출부(consumer)가 로그만 남기고 넘어간다.
    """
    if not segments:
        return []

    classifier_input = build_classifier_input(
        session_id, segments, known_patterns, lang, problem_id, total_duration_ms
    )
    user_prompt = build_user_prompt(session_id, classifier_input)
    segments_by_id = {s.segment_id: s for s in segments}

    accepted: dict = {}
    for attempt in range(MAX_ATTEMPTS):
        result = await client.chat(
            system=_SYSTEM_PROMPT, user=user_prompt,
            temperature=0.0, seed=_SEED, json_mode=True,
        )
        try:
            output = parse_output(result.text)
        except ValueError as exc:
            logger.warning(
                "classifier parse failed (session=%s, attempt=%d/%d): %s",
                session_id, attempt + 1, MAX_ATTEMPTS, exc,
            )
            continue

        all_ok = True
        for seg_result in output.segment_results:
            if seg_result.segment_id in accepted:
                continue
            ok, reason = verify_structural_grounding(seg_result, segments_by_id)
            if ok:
                accepted[seg_result.segment_id] = seg_result
            else:
                all_ok = False
                logger.warning(
                    "classifier grounding failed (session=%s, segment=%s, attempt=%d/%d): %s",
                    session_id, seg_result.segment_id, attempt + 1, MAX_ATTEMPTS, reason,
                )
        if all_ok and len(accepted) == len(segments):
            break

    # §4 규칙 7: confidence 0.6 미만은 후보로도 저장하지 않는다
    return [
        r for r in accepted.values()
        if r.pattern_confidence >= CANDIDATE_MIN_CONFIDENCE
        or (r.pivot_type is not None and r.pivot_confidence >= CANDIDATE_MIN_CONFIDENCE)
    ]
