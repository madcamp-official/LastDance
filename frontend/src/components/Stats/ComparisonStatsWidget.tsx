// docs/api-spec.md "비교 통계 (Stats)" 절 — GET /problems/{id}/stats는 백엔드에 자리표시자
// 메시지만 반환하도록 구현되어 있다(실제 통계 산출 로직은 없음). 실제 연동 전까지
// 정적 mock 데이터로 레이아웃만 구현한다.
const MOCK_STATS = {
  percentile: 62,
  medianTimeMinutes: 9,
  yourTimeMinutes: 8,
}

export function ComparisonStatsWidget() {
  return (
    <div className="rounded-md border border-dashed border-gray-300 p-4 text-sm dark:border-gray-700">
      <h2 className="mb-1 font-medium">다른 응시자 대비 (mock)</h2>
      <p className="mb-3 text-xs text-gray-500">
        실제 통계 연동 전까지의 레이아웃 placeholder입니다.
      </p>
      <dl className="grid grid-cols-2 gap-y-2">
        <dt className="text-gray-500">상위 백분위</dt>
        <dd>{MOCK_STATS.percentile}%</dd>
        <dt className="text-gray-500">중앙값 소요 시간</dt>
        <dd>{MOCK_STATS.medianTimeMinutes}분</dd>
        <dt className="text-gray-500">내 소요 시간</dt>
        <dd>{MOCK_STATS.yourTimeMinutes}분</dd>
      </dl>
    </div>
  )
}
