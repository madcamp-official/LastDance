# Replay Worker 패키지 (keystroke-analysis-dev-plan.md §4)
# 세션 종료 후 이벤트 로그를 재생해 파생 피처를 뽑는 비동기 배치 계층.
# 규칙이 바뀌면 과거 세션과 비교 불가 → 버전을 파생 행에 기록한다 (§11.5)
MATCHER_VERSION = 1

# git-timeline-feedback-spec.md §7 재현성: Stage A(app/worker/timeline.py)의 커밋 경계·
# 세그먼트 라벨 규칙이나 임계값이 바뀌면 증가시킨다. raw blob이 남아 있으므로 버전
# 증가 시 재생으로 code_commits를 재계산할 수 있다.
TIMELINE_VERSION = 1
