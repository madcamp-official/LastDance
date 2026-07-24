import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiClient } from '../lib/api'
import { ApiError } from '../lib/api-client'
import type { ProblemListItem } from '../types/api'

const PAGE_SIZE = 20

export function ProblemListPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const page = Number(searchParams.get('page') ?? '1')

  const [items, setItems] = useState<ProblemListItem[]>([])
  const [total, setTotal] = useState(0)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setStatus('loading')
    apiClient.problems
      .list({ page, page_size: PAGE_SIZE })
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
  }, [page])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <h1 className="mb-6 text-2xl font-semibold">문제 목록</h1>

      {status === 'loading' && <p className="text-gray-500">불러오는 중...</p>}
      {status === 'error' && <p className="text-red-600">{errorMessage}</p>}
      {status === 'ready' && items.length === 0 && (
        <p className="text-gray-500">등록된 문제가 없습니다.</p>
      )}

      {status === 'ready' && items.length > 0 && (
        <ul className="flex flex-col divide-y divide-gray-200 dark:divide-gray-800">
          {items.map((problem) => (
            <li key={problem.problem_id} className="py-3">
              <Link
                to={`/problems/${problem.problem_id}`}
                className="font-medium text-indigo-600 hover:underline"
              >
                {problem.title}
              </Link>
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
