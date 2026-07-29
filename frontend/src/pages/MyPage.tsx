import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../lib/api'
import { useAuthStore } from '../store/authStore'
import { DifficultyBadge } from '../components/ProblemCatalog/DifficultyBadge'
import type { UserSessionListItem } from '../types/api'

interface ProblemHistoryRow {
  problem_id: number
  problem_title: string
  difficulty: UserSessionListItem['difficulty']
  lastSubmittedAt: string | null
  solved: boolean
}

export function MyPage() {
  const user = useAuthStore((s) => s.user)
  const [sessions, setSessions] = useState<UserSessionListItem[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')

  useEffect(() => {
    let cancelled = false
    apiClient.sessions
      .listMine({ page_size: 100 })
      .then((res) => {
        if (!cancelled) {
          setSessions(res.items)
          setStatus('ready')
        }
      })
      .catch(() => {
        if (!cancelled) setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 세션을 problem_id로 그룹핑 — 활동 요약과 풀이 기록 로그(문제당 1행)가 같은 그룹을 공유한다.
  // 제출 하나 없는 세션(시작만 해둔 시도)은 풀이 기록에서 노출하지 않는다.
  const byProblem = useMemo(() => {
    const map = new Map<number, UserSessionListItem[]>()
    for (const s of sessions) {
      if (s.latest_submitted_at === null) continue
      const list = map.get(s.problem_id) ?? []
      list.push(s)
      map.set(s.problem_id, list)
    }
    return map
  }, [sessions])

  const solvedCount = useMemo(
    () => [...byProblem.values()].filter((list) => list.some((s) => s.status === 'solved')).length,
    [byProblem],
  )
  const attemptedCount = byProblem.size
  // 문제 하나 푸는 데 평균 몇 번 세션을 쓰는지 — 결과가 아니라 과정을 보는 이 서비스 취지에 맞는 지표.
  const avgSessionsPerProblem = attemptedCount > 0 ? sessions.length / attemptedCount : 0

  const rows: ProblemHistoryRow[] = useMemo(
    () =>
      [...byProblem.entries()]
        .map(([problem_id, list]) => ({
          problem_id,
          problem_title: list[0].problem_title,
          difficulty: list[0].difficulty,
          lastSubmittedAt:
            list
              .map((s) => s.latest_submitted_at)
              .filter((t): t is string => t !== null)
              .sort()
              .at(-1) ?? null,
          solved: list.some((s) => s.status === 'solved'),
        }))
        .sort((a, b) => {
          if (!a.lastSubmittedAt) return 1
          if (!b.lastSubmittedAt) return -1
          return a.lastSubmittedAt < b.lastSubmittedAt ? 1 : -1
        }),
    [byProblem],
  )

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <div className="mb-2">
        <Link to="/problems" className="text-sm text-indigo-600 hover:underline">
          ← 문제 목록
        </Link>
      </div>
      <h1 className="mb-6 text-2xl font-semibold">마이페이지</h1>

      <section className="mb-8 flex flex-col gap-4 rounded-md border border-gray-200 p-4 dark:border-gray-800">
        <div className="flex items-center gap-3">
          {user?.profile_img ? (
            <img
              src={user.profile_img}
              alt=""
              className="h-12 w-12 rounded-full object-cover"
            />
          ) : (
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-indigo-100 text-lg font-semibold text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
              {(user?.nickname ?? '?').slice(0, 1).toUpperCase()}
            </div>
          )}
          <div>
            <p className="text-lg font-semibold">{user?.nickname ?? '내 계정'}</p>
            <p className="text-sm text-gray-500">{user?.email}</p>
          </div>
        </div>
        <dl className="grid grid-cols-2 gap-4 border-t border-gray-200 pt-4 text-sm dark:border-gray-800">
          <div>
            <dt className="text-gray-500">푼 문제 수</dt>
            <dd className="text-xl font-semibold">{solvedCount}</dd>
          </div>
          <div>
            <dt className="text-gray-500">평균 제출 세션 수</dt>
            <dd className="text-xl font-semibold">
              {attemptedCount > 0 ? avgSessionsPerProblem.toFixed(1) : '-'}
            </dd>
          </div>
        </dl>
      </section>

      <section>
        <h2 className="mb-3 text-lg font-medium">풀이 기록</h2>
        {status === 'loading' && <p className="text-gray-500">불러오는 중...</p>}
        {status === 'error' && <p className="text-red-600">풀이 기록을 불러오지 못했습니다.</p>}
        {status === 'ready' && rows.length === 0 && (
          <p className="text-gray-500">아직 풀이 기록이 없습니다.</p>
        )}
        {status === 'ready' && rows.length > 0 && (
          <ul className="flex flex-col divide-y divide-gray-200 dark:divide-gray-800">
            {rows.map((row) => (
              <li key={row.problem_id} className="flex items-center gap-3 py-3">
                <DifficultyBadge difficulty={row.difficulty} />
                <Link
                  to={`/me/problems/${row.problem_id}`}
                  className="flex-1 font-medium text-indigo-600 hover:underline"
                >
                  {row.problem_title}
                </Link>
                {row.solved && (
                  <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                    Solved
                  </span>
                )}
                <span className="text-xs text-gray-500">
                  {row.lastSubmittedAt
                    ? new Date(row.lastSubmittedAt).toLocaleString()
                    : '제출 기록 없음'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
