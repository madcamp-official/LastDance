# Replay Worker 패키지 (keystroke-analysis-dev-plan.md §4)
# 세션 종료 후 이벤트 로그를 재생해 파생 피처를 뽑는 비동기 배치 계층.
# 규칙이 바뀌면 과거 세션과 비교 불가 → 버전을 파생 행에 기록한다 (§11.5)
MATCHER_VERSION = 1
