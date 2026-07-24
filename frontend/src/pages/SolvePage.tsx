import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import type { OnMount } from '@monaco-editor/react'
import { apiClient } from '../lib/api'
import { ApiError } from '../lib/api-client'
import { useAuthStore } from '../store/authStore'
import { useMonacoActivityLogger } from '../hooks/useMonacoActivityLogger'
import { CodeEditor } from '../components/Editor/CodeEditor'
import { FeedbackPanel } from '../components/Feedback/FeedbackPanel'
import { ComparisonStatsWidget } from '../components/Stats/ComparisonStatsWidget'
import { SUPPORTED_LANGUAGES } from '../lib/languages'
import type { Session, SubmissionStatus } from '../types/api'

// 문제별 진행 중인 세션 id를 기억해, 새로고침해도 같은 세션을 이어서 보여준다.
// (api-spec.md에 problem_id로 세션을 조회하는 엔드포인트가 없어 세션 id 자체를 클라이언트에 보관해야 함)
function sessionStorageKey(problemId: string) {
  return `lastdance_session_${problemId}`
}

export function SolvePage() {
  const { problemId } = useParams<{ problemId: string }>()
  const accessToken = useAuthStore((s) => s.accessToken)

  const [language, setLanguage] = useState<string>(SUPPORTED_LANGUAGES[0].value)
  const [session, setSession] = useState<Session | null>(null)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)

  const [code, setCode] = useState('')
  const [submissionId, setSubmissionId] = useState<string | null>(null)
  const [submissionStatus, setSubmissionStatus] = useState<SubmissionStatus | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [ending, setEnding] = useState(false)

  const { attachEditor } = useMonacoActivityLogger(
    session?.session_id ?? null,
    accessToken,
  )

  // 새로고침 시 이 문제에 대해 진행 중이던(또는 마지막) 세션이 있으면 이어서 복원한다.
  useEffect(() => {
    if (!problemId) return
    const storedSessionId = localStorage.getItem(sessionStorageKey(problemId))
    if (!storedSessionId) return

    let cancelled = false
    ;(async () => {
      try {
        const restored = await apiClient.sessions.get(storedSessionId)
        if (cancelled) return
        setSession(restored)
        setLanguage(restored.language)

        const submissions = await apiClient.submissions.listBySession(
          restored.session_id,
        )
        if (cancelled) return
        const latest = submissions.items.at(-1)
        if (latest) {
          setSubmissionId(latest.submission_id)
          setSubmissionStatus(latest.status)
        }
      } catch {
        // 저장된 세션을 더 이상 찾을 수 없으면(만료/삭제 등) 참조를 지우고 시작 화면으로 둔다.
        localStorage.removeItem(sessionStorageKey(problemId))
      }
    })()

    return () => {
      cancelled = true
    }
  }, [problemId])

  async function handleStart() {
    if (!problemId) return
    setStarting(true)
    setStartError(null)
    try {
      const created = await apiClient.sessions.create({
        problem_id: problemId,
        language,
      })
      setSession(created)
      setCode('')
      localStorage.setItem(sessionStorageKey(problemId), created.session_id)
    } catch (err) {
      setStartError(
        err instanceof ApiError ? err.message : '세션을 시작하지 못했습니다.',
      )
    } finally {
      setStarting(false)
    }
  }

  function handleStartOver() {
    if (problemId) localStorage.removeItem(sessionStorageKey(problemId))
    setSession(null)
    setCode('')
    setSubmissionId(null)
    setSubmissionStatus(null)
    setSubmitError(null)
  }

  const handleEditorMount: OnMount = (editorInstance) => {
    attachEditor(editorInstance)
  }

  async function handleSubmit() {
    if (!session || !problemId) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const res = await apiClient.submissions.create({
        session_id: session.session_id,
        problem_id: problemId,
        code,
        language,
      })
      setSubmissionId(res.submission_id)
      setSubmissionStatus(res.status)
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : '제출하지 못했습니다.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRefreshSubmission() {
    if (!submissionId) return
    try {
      const res = await apiClient.submissions.get(submissionId)
      setSubmissionStatus(res.status)
    } catch {
      // 조회 실패 시 조용히 무시 — 새로고침 버튼으로 재시도 가능
    }
  }

  async function handleEndSession(status: 'solved' | 'unsolved' | 'abandoned') {
    if (!session) return
    setEnding(true)
    try {
      const updated = await apiClient.sessions.patch(session.session_id, { status })
      setSession(updated)
    } catch {
      // 종료 실패 — 버튼을 다시 눌러 재시도 가능하도록 세션 상태를 그대로 유지
    } finally {
      setEnding(false)
    }
  }

  if (!session) {
    return (
      <div className="mx-auto max-w-md px-4 py-16">
        <h1 className="mb-4 text-xl font-semibold">풀이 시작</h1>
        <label
          htmlFor="language"
          className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300"
        >
          언어 선택
        </label>
        <select
          id="language"
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
          className="mb-4 w-full rounded-md border border-gray-300 px-3 py-2 dark:border-gray-600 dark:bg-gray-800"
        >
          {SUPPORTED_LANGUAGES.map((lang) => (
            <option key={lang.value} value={lang.value}>
              {lang.label}
            </option>
          ))}
        </select>
        {startError && <p className="mb-3 text-sm text-red-600">{startError}</p>}
        <button
          type="button"
          onClick={handleStart}
          disabled={starting}
          className="w-full rounded-md bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {starting ? '시작하는 중...' : '시작하기'}
        </button>
      </div>
    )
  }

  const isEnded = session.status !== 'active'
  const languageLabel = SUPPORTED_LANGUAGES.find((l) => l.value === language)?.label

  return (
    <div className="mx-auto grid max-w-6xl grid-cols-1 gap-6 px-4 py-6 lg:grid-cols-[2fr_1fr]">
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <span className="text-sm text-gray-500">
            언어: {languageLabel} · 세션 상태: {session.status}
          </span>
          {!isEnded && (
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => handleEndSession('abandoned')}
                disabled={ending}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-700"
              >
                포기
              </button>
              <button
                type="button"
                onClick={() => handleEndSession('solved')}
                disabled={ending}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-700"
              >
                해결로 종료
              </button>
            </div>
          )}
          {isEnded && (
            <button
              type="button"
              onClick={handleStartOver}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-700"
            >
              새 시도 시작
            </button>
          )}
        </div>

        <CodeEditor
          language={language}
          value={code}
          onChange={setCode}
          onMount={handleEditorMount}
        />

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting || isEnded}
            className="rounded-md bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {submitting ? '제출 중...' : '제출'}
          </button>
          {submissionId && (
            <>
              <span className="text-sm text-gray-500">
                제출 상태: {submissionStatus} ({submissionId})
              </span>
              <button
                type="button"
                onClick={handleRefreshSubmission}
                className="text-sm text-indigo-600 hover:underline"
              >
                새로고침
              </button>
            </>
          )}
        </div>
        {submitError && <p className="text-sm text-red-600">{submitError}</p>}
      </div>

      <div className="flex flex-col gap-4">
        <FeedbackPanel sessionId={session.session_id} />
        <ComparisonStatsWidget />
      </div>
    </div>
  )
}
