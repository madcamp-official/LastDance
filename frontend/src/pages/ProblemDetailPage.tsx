import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiClient } from '../lib/api'
import { ApiError } from '../lib/api-client'
import type { ProblemDetail } from '../types/api'
import { MathText } from '../components/ProblemCatalog/MathText'

export function ProblemDetailPage() {
  const { problemId } = useParams<{ problemId: string }>()
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    const numericId = Number(problemId)
    if (!problemId || Number.isNaN(numericId)) {
      setErrorMessage('잘못된 문제 id입니다.')
      setStatus('error')
      return
    }
    let cancelled = false
    setStatus('loading')
    apiClient.problems
      .get(numericId)
      .then((res) => {
        if (cancelled) return
        setProblem(res)
        setStatus('ready')
      })
      .catch((err) => {
        if (cancelled) return
        setErrorMessage(
          err instanceof ApiError ? err.message : '문제를 불러오지 못했습니다.',
        )
        setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [problemId])

  if (status === 'loading') {
    return <p className="mx-auto max-w-3xl px-4 py-8 text-gray-500">불러오는 중...</p>
  }
  if (status === 'error' || !problem) {
    return <p className="mx-auto max-w-3xl px-4 py-8 text-red-600">{errorMessage}</p>
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">{problem.title}</h1>
        <Link
          to={`/problems/${problem.problem_id}/solve`}
          className="rounded-md bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-500"
        >
          풀기
        </Link>
      </div>

      <section className="mb-6">
        <h2 className="mb-2 text-lg font-medium">문제</h2>
        <MathText text={problem.statement} />
      </section>

      {problem.constraints && (
        <section className="mb-6">
          <h2 className="mb-2 text-lg font-medium">제약조건</h2>
          <MathText text={problem.constraints} />
        </section>
      )}

      {problem.examples.length > 0 && (
        <section>
          <h2 className="mb-2 text-lg font-medium">예제</h2>
          <div className="flex flex-col gap-4">
            {problem.examples.map((example, i) => (
              <div key={i} className="grid grid-cols-2 gap-3">
                <div>
                  <p className="mb-1 text-sm text-gray-500">입력 {i + 1}</p>
                  <pre className="overflow-x-auto rounded-md bg-gray-100 p-3 text-sm dark:bg-gray-900">
                    {example.input}
                  </pre>
                </div>
                <div>
                  <p className="mb-1 text-sm text-gray-500">출력 {i + 1}</p>
                  <pre className="overflow-x-auto rounded-md bg-gray-100 p-3 text-sm dark:bg-gray-900">
                    {example.output}
                  </pre>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
