import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiClient } from '../lib/api'
import { ApiError } from '../lib/api-client'
import { DifficultyBadge } from '../components/ProblemCatalog/DifficultyBadge'
import type { DifficultyTier, ProblemListItem, UserSessionListItem } from '../types/api'

const PAGE_SIZE = 20
const ALL_TIERS: DifficultyTier[] = ['A', 'B', 'C', 'D', 'E', 'F', 'G']

export function ProblemListPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const page = Number(searchParams.get('page') ?? '1')

  const [showSolved, setShowSolved] = useState(false)
  const [selectedDifficulties, setSelectedDifficulties] = useState<DifficultyTier[]>([])

  const [items, setItems] = useState<ProblemListItem[]>([])
  const [total, setTotal] = useState(0)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const [inProgress, setInProgress] = useState<UserSessionListItem[]>([])

  useEffect(() => {
    let cancelled = false
    setStatus('loading')
    apiClient.problems
      .list({
        page,
        page_size: PAGE_SIZE,
        sort: 'difficulty_asc',
        difficulty: selectedDifficulties.length > 0 ? selectedDifficulties : undefined,
        exclude_solved: !showSolved,
      })
      .then((res) => {
        if (cancelled) return
        setItems(res.items)
        setTotal(res.total_count)
        setStatus('ready')
      })
      .catch((err) => {
        if (cancelled) return
        setErrorMessage(err instanceof ApiError ? err.message : '문제 목록을 불러오지 못했습니다.')
        setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [page, showSolved, selectedDifficulties])

  useEffect(() => {
    let cancelled = false
    apiClient.sessions
      .listMine({ status: 'active', page_size: 50 })
      .then((res) => {
        if (!cancelled) setInProgress(res.items)
      })
      .catch(() => {
        // "풀이 중인 문제" 섹션은 부가 정보 — 조회 실패해도 조용히 숨긴다.
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 과거에 중복 생성된 active 세션이 남아있을 수 있어 문제당 가장 최근 세션 하나만 보여준다.
  const dedupedInProgress = useMemo(() => {
    const byProblem = new Map<number, UserSessionListItem>()
    for (const s of inProgress) {
      const cur = byProblem.get(s.problem_id)
      if (!cur || s.started_at > cur.started_at) byProblem.set(s.problem_id, s)
    }
    return [...byProblem.values()]
  }, [inProgress])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  function toggleDifficulty(tier: DifficultyTier) {
    setSelectedDifficulties((prev) =>
      prev.includes(tier) ? prev.filter((t) => t !== tier) : [...prev, tier],
    )
    setSearchParams({ page: '1' })
  }

  function handleShowSolvedChange(checked: boolean) {
    setShowSolved(checked)
    setSearchParams({ page: '1' })
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      {dedupedInProgress.length > 0 && (
        <section className="mb-8 border-b border-gray-200 pb-8 dark:border-gray-800">
          <h1 className="mb-3 text-2xl font-semibold">풀이 중인 문제</h1>
          <ul className="flex flex-col divide-y divide-gray-200 rounded-md border border-gray-200 dark:divide-gray-800 dark:border-gray-800">
            {dedupedInProgress.map((s) => (
              <li key={s.session_id} className="flex items-center gap-3 px-3 py-2.5">
                <DifficultyBadge difficulty={s.difficulty} />
                <Link
                  to={`/problems/${s.problem_id}/solve`}
                  className="font-medium text-indigo-600 hover:underline"
                >
                  {s.problem_title}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <h1 className="mb-6 text-2xl font-semibold">문제 목록</h1>

      <div className="mb-6 flex flex-wrap items-center gap-4 rounded-md border border-gray-200 p-3 text-sm dark:border-gray-800">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={showSolved}
            onChange={(e) => handleShowSolvedChange(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300"
          />
          Show solved problems
        </label>
        <div className="flex flex-wrap items-center gap-1.5">
          {ALL_TIERS.map((tier) => (
            <button
              key={tier}
              type="button"
              onClick={() => toggleDifficulty(tier)}
              aria-pressed={selectedDifficulties.includes(tier)}
              className="rounded-full border border-gray-300 px-2 py-0.5 text-xs font-medium aria-pressed:border-indigo-600 aria-pressed:bg-indigo-50 aria-pressed:text-indigo-700 dark:border-gray-700 dark:aria-pressed:bg-indigo-950 dark:aria-pressed:text-indigo-300"
            >
              {tier}
            </button>
          ))}
        </div>
      </div>

      {status === 'loading' && <p className="text-gray-500">불러오는 중...</p>}
      {status === 'error' && <p className="text-red-600">{errorMessage}</p>}
      {status === 'ready' && items.length === 0 && (
        <p className="text-gray-500">조건에 맞는 문제가 없습니다.</p>
      )}

      {status === 'ready' && items.length > 0 && (
        <ul className="flex flex-col divide-y divide-gray-200 dark:divide-gray-800">
          {items.map((problem) => (
            <li key={problem.problem_id} className="flex items-center gap-3 py-3">
              <DifficultyBadge difficulty={problem.difficulty} />
              <Link
                to={`/problems/${problem.problem_id}`}
                className="flex-1 font-medium text-indigo-600 hover:underline"
              >
                {problem.title}
              </Link>
              {problem.solved_at && (
                <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                  Solved: {new Date(problem.solved_at).toLocaleDateString()}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {status === 'ready' && totalPages > 1 && (
        <div className="mt-6 flex items-center justify-center gap-4 text-sm">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setSearchParams({ page: String(page - 1) })}
            className="rounded-md border border-gray-300 px-3 py-1 disabled:opacity-40 dark:border-gray-700"
          >
            이전
          </button>
          <span className="text-gray-500">
            {page} / {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => setSearchParams({ page: String(page + 1) })}
            className="rounded-md border border-gray-300 px-3 py-1 disabled:opacity-40 dark:border-gray-700"
          >
            다음
          </button>
        </div>
      )}
    </div>
  )
}
