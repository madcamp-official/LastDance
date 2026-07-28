from statistics import median
from typing import List, Optional, Tuple

from app.schema.analysis import EditOp, PausePoint

# dev-plan §4.1 Step 2 상수
MIN_THRESHOLD_MS = 1500     # 하한 클램프 — 타이핑 리듬 흔들림을 pause로 잡지 않음
MAD_K = 5                   # threshold = med + 5 * mad
SMALL_SESSION_K = 100       # 표본이 이보다 작으면 유저 전역 기준선으로 fallback


def detect_pauses(
    events: List[EditOp],
    global_baseline: Optional[Tuple[float, float]] = None,
) -> Tuple[List[PausePoint], float]:
    """개인·세션별 적응형 pause 탐지. 반환: (pauses, threshold_ms).

    global_baseline: 유저 전역 (med, mad). 세션 표본이 작을 때 fallback (§4.1 Step 2).
    """
    # src != "user" 이벤트는 pause 계산에서 제외 (§2.1 주의사항 3)
    user_idx = [i for i, ev in enumerate(events) if ev.src == "user"]
    if len(user_idx) < 2:
        return [], float(MIN_THRESHOLD_MS)

    intervals = [
        events[user_idx[j]].t - events[user_idx[j - 1]].t
        for j in range(1, len(user_idx))
    ]

    if len(intervals) < SMALL_SESSION_K and global_baseline is not None:
        med, mad = global_baseline
    else:
        med = median(intervals)
        mad = median(abs(x - med) for x in intervals)

    threshold = max(med + MAD_K * mad, MIN_THRESHOLD_MS)

    pauses: List[PausePoint] = []
    for j in range(1, len(user_idx)):
        gap = events[user_idx[j]].t - events[user_idx[j - 1]].t
        if gap > threshold:
            pauses.append(
                PausePoint(
                    event_index=user_idx[j],
                    t_ms=events[user_idx[j - 1]].t,
                    duration_ms=gap,
                )
            )
    return pauses, threshold
