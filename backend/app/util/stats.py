"""결정론적 분위수 계산 (git-timeline-feedback-spec.md §2.4, §5.1 공용).

numpy 없이(워커 프로세스 의존성 최소화) 선형보간 방식 percentile을 구현한다.
Stage A의 pause 상위 10% 임계값과 Stage C의 비교군 p25/p50/p75가 같은 함수를
쓰므로, 같은 표본이면 두 곳이 항상 같은 값을 본다.
"""

from typing import Dict, Iterable, List, Sequence


def percentile(values: Sequence[float], q: float) -> float:
    """q는 0.0~1.0. 선형보간(numpy 기본 'linear'과 동일 눈금)."""
    if not values:
        return 0.0
    data = sorted(values)
    if len(data) == 1:
        return float(data[0])
    q = min(max(q, 0.0), 1.0)
    pos = q * (len(data) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(data) - 1)
    frac = pos - lo
    return float(data[lo]) + (float(data[hi]) - float(data[lo])) * frac


def percentiles(values: Iterable[float], qs: Sequence[float] = (0.1, 0.25, 0.5, 0.75, 0.9)) -> Dict[str, float]:
    """{"p10": ..., "p25": ...} 형태로 한 번에."""
    data: List[float] = [float(v) for v in values]
    return {f"p{int(round(q * 100))}": percentile(data, q) for q in qs}
