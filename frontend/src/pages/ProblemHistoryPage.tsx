import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiClient } from '../lib/api'
import { ApiError } from '../lib/api-client'
import { MathText } from '../components/ProblemCatalog/MathText'
import { DifficultyBadge } from '../components/ProblemCatalog/DifficultyBadge'
import { FeedbackPanel } from '../components/Feedback/FeedbackPanel'
import { AnalysisPanel } from '../components/Analysis/AnalysisPanel'
import type { ProblemDetail, Submission, UserSessionListItem } from '../types/api'

const VERDICT_LABEL: Record<string, string> = {
  AC: '정답',
  WA: '오답',
  TLE: '시간 초과',
  MLE: '메모리 초과',
  RE: '런타임 에러',
  CE: '컴파일 에러',
}

interface SessionEntry {
  session: UserSessionListItem
  submissions: Submission[]
}

export function ProblemHistoryPage() {
  const { problemId } = useParams<{ problemId: string }>()
  const numericProblemId = problemId ? Number(problemId) : NaN

  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [entries, setEntries] = useState<SessionEntry[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    if (!problemId || Number.isNaN(numericProblemId)) return
    let cancelled = false
    setStatus('loading')
    ;(async () => {
      try {
        const [problemDetail, sessionList] = await Promise.all([
          apiClient.problems.get(numericProblemId),
          apiClient.sessions.listMine({ problem_id: numericProblemId, page_size: 100 }),
        ])
        if (cancelled) return
        setProblem(problemDetail)

        const withSubs = await Promise.all(
          sessionList.items.map(async (session) => {
            const list = await apiClient.submissions.listBySession(session.session_id)
            const submissions = await Promise.all(
              list.items.map((item) => apiClient.submissions.get(item.submission_id)),
            )
            return { session, submissions }
          }),
        )
        if (cancelled) return
        // 제출 하나 없는 세션(예: 시작만 해두고 손대지 않은 시도)은 풀이 기록에서 굳이 보여주지 않는다.
        setEntries(
          withSubs
            .filter((entry) => entry.submissions.length > 0)
            .sort((a, b) => (a.session.started_at < b.session.started_at ? 1 : -1)),
        )
        setStatus('ready')
      } catch (err) {
        if (cancelled) return
        setErrorMessage(
          err instanceof ApiError ? err.message : '풀이 기록을 불러오지 못했습니다.',
        )
        setStatus('error')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [problemId, numericProblemId])

  if (status === 'loading') {
    return <p className="mx-auto max-w-3xl px-4 py-8 text-gray-500">불러오는 중...</p>
  }
  if (status === 'error' || !problem) {
    return <p className="mx-auto max-w-3xl px-4 py-8 text-red-600">{errorMessage}</p>
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <div className="mb-2">
        <Link to="/me" className="text-sm text-indigo-600 hover:underline">
          ← 마이페이지
        </Link>
      </div>
      <div className="mb-6 flex items-center gap-3">
        <DifficultyBadge difficulty={problem.difficulty} />
        <h1 className="text-2xl font-semibold">{problem.title}</h1>
      </div>

      <section className="mb-8">
        <h2 className="mb-2 text-sm font-medium text-gray-500">문제</h2>
        <MathText text={problem.statement} />
      </section>

      <h2 className="mb-3 text-lg font-medium">풀이 기록</h2>
      {entries.length === 0 && (
        <p className="text-gray-500">이 문제에 대한 풀이 기록이 없습니다.</p>
      )}
      <div className="flex flex-col gap-6">
        {entries.map(({ session, submissions }) => (
          <div
            key={session.session_id}
            className="rounded-md border border-gray-200 p-4 dark:border-gray-800"
          >
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="text-gray-500">
                {new Date(session.started_at).toLocaleString()}
                {session.ended_at && ` ~ ${new Date(session.ended_at).toLocaleString()}`}
              </span>
              <span
                className={
                  session.status === 'solved'
                    ? 'font-medium text-emerald-600'
                    : session.status === 'abandoned'
                      ? 'text-gray-500'
                      : 'font-medium text-indigo-600'
                }
              >
                {session.status === 'solved'
                  ? '해결'
                  : session.status === 'abandoned'
                    ? '포기'
                    : '진행 중'}
              </span>
            </div>

            {submissions.length > 0 ? (
              <div className="mb-4 flex flex-col gap-3">
                {submissions.map((sub) => (
                  <div
                    key={sub.submission_id}
                    className="rounded-md bg-gray-50 p-3 dark:bg-gray-900"
                  >
                    <div className="mb-1.5 flex items-center justify-between text-xs text-gray-500">
                      <span>{new Date(sub.submitted_at).toLocaleString()}</span>
                      <span
                        className={
                          sub.verdict === 'AC'
                            ? 'font-medium text-emerald-600'
                            : 'font-medium text-red-600'
                        }
                      >
                        {sub.verdict ? (VERDICT_LABEL[sub.verdict] ?? sub.verdict) : '채점 중'}
                      </span>
                    </div>
                    <pre className="overflow-x-auto whitespace-pre-wrap break-all text-xs">
                      {sub.code || '(코드 원문은 아직 조회할 수 없습니다)'}
                    </pre>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mb-4 text-sm text-gray-500">제출 없음</p>
            )}

            <FeedbackPanel sessionId={session.session_id} mode="review" />
            {session.status !== 'active' && (
              <div className="mt-4">
                <AnalysisPanel sessionId={session.session_id} enabled />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
