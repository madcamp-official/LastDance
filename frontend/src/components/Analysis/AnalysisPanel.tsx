import { useEffect, useState } from 'react'
import { apiClient } from '../../lib/api'
import { ApiError } from '../../lib/api-client'
import type { AnalysisResult } from '../../types/api'

// docs/api-spec.md "키스트로크 분석 (Analysis)" 절의 enum 값 → 한국어 라벨.
const PHASE_LABEL: Record<string, string> = {
  SETUP: '설계',
  FORMATION: '형성',
  DEBUG: '디버그',
  REFINE: '다듬기',
}

const AST_LABEL_KO: Record<string, string> = {
  INTERFACE_DESIGN: '인터페이스 설계',
  LOOP_BOUNDARY: '반복문 경계',
  BRANCH_CONDITION: '분기 조건',
  INDEX_REASONING: '인덱스 추론',
  ALGORITHM_ENTRY: '알고리즘 진입',
  SETUP: '초기 설정',
  SYNTAX_STRUGGLE: '문법 씨름',
}

const PIVOT_TYPE_KO: Record<string, string> = {
  APPROACH_SWITCH: '접근법 전환',
  COMPLEXITY_FIX: '복잡도 개선',
  EDGE_CASE_FIX: '엣지 케이스 수정',
  OTHER: '기타',
}

const PHASE_BAR_COLOR: Record<string, string> = {
  SETUP: 'bg-gray-400 dark:bg-gray-600',
  FORMATION: 'bg-indigo-500',
  DEBUG: 'bg-amber-500',
  REFINE: 'bg-emerald-500',
}

const POLL_INTERVAL_MS = 3000
const MAX_POLLS = 40 // 3초 * 40 ≈ 2분. 그 이상 지연되면 폴링을 멈추고 수동 새로고침을 안내.

type PanelState = 'processing' | 'ready' | 'not_found' | 'timeout' | 'error'

function formatMs(ms: number): string {
  const totalSec = Math.round(ms / 1000)
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  return `${min}:${String(sec).padStart(2, '0')}`
}

export function AnalysisPanel({
  sessionId,
  enabled,
}: {
  sessionId: string
  enabled: boolean
}) {
  const [state, setState] = useState<PanelState>('processing')
  const [result, setResult] = useState<AnalysisResult | null>(null)

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    let attempts = 0
    let timer: number | null = null

    async function poll() {
      attempts += 1
      try {
        const res = await apiClient.analysis.get(sessionId)
        if (cancelled) return
        if ('result' in res) {
          setResult(res.result)
          setState('ready')
          if (timer !== null) window.clearInterval(timer)
          return
        }
        if (attempts >= MAX_POLLS) {
          setState('timeout')
          if (timer !== null) window.clearInterval(timer)
        } else {
          setState('processing')
        }
      } catch (err) {
        if (cancelled) return
        setState(err instanceof ApiError && err.status === 404 ? 'not_found' : 'error')
        if (timer !== null) window.clearInterval(timer)
      }
    }

    setState('processing')
    poll()
    timer = window.setInterval(poll, POLL_INTERVAL_MS)

    return () => {
      cancelled = true
      if (timer !== null) window.clearInterval(timer)
    }
  }, [sessionId, enabled])

  if (!enabled) return null

  return (
    <div className="rounded-md border border-gray-200 p-4 dark:border-gray-800">
      <h2 className="mb-3 font-medium">행동 분석</h2>

      {state === 'processing' && (
        <p className="text-sm text-gray-500">편집 기록을 분석하는 중입니다...</p>
      )}
      {state === 'timeout' && (
        <p className="text-sm text-gray-500">
          분석이 예상보다 오래 걸리고 있습니다. 잠시 후 페이지를 새로고침해 다시 확인해 주세요.
        </p>
      )}
      {state === 'not_found' && (
        <p className="text-sm text-gray-500">이 세션에는 분석할 편집 기록이 없습니다.</p>
      )}
      {state === 'error' && (
        <p className="text-sm text-red-600">분석 결과를 불러오지 못했습니다.</p>
      )}

      {state === 'ready' && result && <AnalysisResultView result={result} />}
    </div>
  )
}

function AnalysisResultView({ result }: { result: AnalysisResult }) {
  const phases: Array<{ key: 'setup_ms' | 'formation_ms' | 'debug_ms' | 'refine_ms'; label: string }> = [
    { key: 'setup_ms', label: 'SETUP' },
    { key: 'formation_ms', label: 'FORMATION' },
    { key: 'debug_ms', label: 'DEBUG' },
    { key: 'refine_ms', label: 'REFINE' },
  ]
  const total = result.total_ms || 1

  return (
    <div className="flex flex-col gap-4">
      {result.analysis_level !== 'full' && (
        <p className="rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-300">
          {result.analysis_level === 'timing_only'
            ? '이 언어는 구조 패턴 분석을 지원하지 않아 시간 정보만 제공됩니다.'
            : '이벤트 수가 적어 분석 정확도가 낮을 수 있습니다.'}
        </p>
      )}

      <div>
        <div className="mb-1 flex h-3 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
          {phases.map(({ key, label }) => {
            const ms = result[key]
            if (ms <= 0) return null
            return (
              <div
                key={key}
                title={`${PHASE_LABEL[label]}: ${formatMs(ms)}`}
                className={PHASE_BAR_COLOR[label]}
                style={{ width: `${(ms / total) * 100}%` }}
              />
            )
          })}
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-500">
          {phases.map(({ key, label }) => (
            <span key={key} className="flex items-center gap-1">
              <span className={`inline-block h-2 w-2 rounded-full ${PHASE_BAR_COLOR[label]}`} />
              {PHASE_LABEL[label]} {formatMs(result[key])}
            </span>
          ))}
          <span className="ml-auto font-medium text-gray-700 dark:text-gray-300">
            총 {formatMs(result.total_ms)}
          </span>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-y-1 text-sm sm:grid-cols-4">
        <dt className="text-gray-500">키 입력</dt>
        <dd>{result.keystroke_count}회</dd>
        <dt className="text-gray-500">정지</dt>
        <dd>
          {result.pause_count}회 · {formatMs(result.pause_total_ms)}
        </dd>
        <dt className="text-gray-500">재작성</dt>
        <dd>{result.pivot_count}회</dd>
        <dt className="text-gray-500">코드 크기</dt>
        <dd>{result.code_bytes}B</dd>
      </dl>

      {result.patterns_detected.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {result.patterns_detected.map((pattern) => (
            <span
              key={pattern}
              className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
            >
              {pattern}
            </span>
          ))}
        </div>
      )}

      {result.pauses.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-medium">주요 정지 구간</h3>
          <ul className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-400">
            {[...result.pauses]
              .sort((a, b) => b.duration_ms - a.duration_ms)
              .slice(0, 5)
              .map((pause, i) => (
                <li key={i} className="flex justify-between gap-2">
                  <span>
                    {formatMs(pause.t_ms)} · {formatMs(pause.duration_ms)}
                    {pause.ast_label && ` · ${AST_LABEL_KO[pause.ast_label] ?? pause.ast_label}`}
                  </span>
                  {pause.pattern && <span className="text-gray-400">{pause.pattern}</span>}
                </li>
              ))}
          </ul>
        </div>
      )}

      {result.pivots.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-medium">주요 재작성</h3>
          <ul className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-400">
            {result.pivots.slice(0, 5).map((pivot, i) => (
              <li key={i} className="flex justify-between gap-2">
                <span>
                  {formatMs(pivot.t_ms)} · {pivot.deleted_chars}자 삭제
                  {pivot.pivot_type && ` · ${PIVOT_TYPE_KO[pivot.pivot_type] ?? pivot.pivot_type}`}
                </span>
                {pivot.pattern && <span className="text-gray-400">{pivot.pattern}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
