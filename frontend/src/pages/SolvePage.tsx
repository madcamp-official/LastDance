import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import type { OnMount } from '@monaco-editor/react'
import { apiClient } from '../lib/api'
import { ApiError } from '../lib/api-client'
import { useAuthStore } from '../store/authStore'
import { useMonacoActivityLogger } from '../hooks/useMonacoActivityLogger'
import { CodeEditor } from '../components/Editor/CodeEditor'
import { FeedbackPanel } from '../components/Feedback/FeedbackPanel'
import { AnalysisPanel } from '../components/Analysis/AnalysisPanel'
import { ComparisonStatsWidget } from '../components/Stats/ComparisonStatsWidget'
import { SUPPORTED_LANGUAGES } from '../lib/languages'
import type { SessionDetail, Submission } from '../types/api'

// 문제별 진행 중인 세션 id를 기억해, 새로고침해도 같은 세션을 이어서 보여준다.
// (api-spec.md에 problem_id로 세션을 조회하는 엔드포인트가 없어 세션 id 자체를 클라이언트에 보관해야 함)
function sessionStorageKey(problemId: string) {
  return `lastdance_session_${problemId}`
}

// Monaco에 입력한 코드는 서버에 저장되는 시점(제출)이 아니면 어디에도 안 남으므로,
// 새로고침 시 유실되지 않도록 문제별로 클라이언트에 보관한다.
function codeStorageKey(problemId: string) {
  return `lastdance_code_${problemId}`
}

const VERDICT_LABEL: Record<string, string> = {
  AC: '정답',
  WA: '오답',
  TLE: '시간 초과',
  RE: '런타임 에러',
  CE: '컴파일 에러',
}

export function SolvePage() {
  const { problemId } = useParams<{ problemId: string }>()
  const numericProblemId = problemId ? Number(problemId) : NaN

  const [language, setLanguage] = useState<string>(SUPPORTED_LANGUAGES[0].value)
  const [session, setSession] = useState<SessionDetail | null>(null)
  const [problemTitle, setProblemTitle] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)

  const [code, setCode] = useState('')
  const [submission, setSubmission] = useState<Submission | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [ending, setEnding] = useState(false)

  const {
    attachEditor,
    markSubmission,
    endSession: endKeystrokeLogging,
  } = useMonacoActivityLogger({
    sessionId: session?.session_id ?? null,
    problemId: Number.isNaN(numericProblemId) ? null : numericProblemId,
    language,
    // 매 (재)연결 시점에 최신 토큰을 읽도록 함수로 전달 — accessToken이 갱신돼도 재연결 불필요.
    getAccessToken: () => useAuthStore.getState().accessToken,
  })

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
        if (restored.language) setLanguage(restored.language)

        apiClient.problems
          .get(Number(problemId))
          .then((p) => {
            if (!cancelled) setProblemTitle(p.title)
          })
          .catch(() => {})

        const savedCode = localStorage.getItem(codeStorageKey(problemId))
        if (savedCode !== null) setCode(savedCode)

        const submissions = await apiClient.submissions.listBySession(restored.session_id)
        if (cancelled) return
        const latest = submissions.items.at(-1)
        if (latest) {
          const detail = await apiClient.submissions.get(latest.submission_id)
          if (!cancelled) setSubmission(detail)
        }
      } catch {
        // 저장된 세션을 더 이상 찾을 수 없으면(만료/삭제 등) 참조를 지우고 시작 화면으로 둔다.
        localStorage.removeItem(sessionStorageKey(problemId))
        localStorage.removeItem(codeStorageKey(problemId))
      }
    })()

    return () => {
      cancelled = true
    }
  }, [problemId])

  async function handleStart() {
    if (!problemId || Number.isNaN(numericProblemId)) return
    setStarting(true)
    setStartError(null)
    try {
      const created = await apiClient.sessions.create({ problem_id: numericProblemId })
      // POST /sessions 응답엔 status/started_at이 없다 — 방금 만든 세션이므로 로컬에서 채운다.
      setSession({
        session_id: created.session_id,
        user_id: created.user_id,
        problem_id: created.problem_id,
        language: null,
        started_at: new Date().toISOString(),
        ended_at: null,
        status: 'active',
      })
      setProblemTitle(created.title)
      setCode('')
      setSubmission(null)
      localStorage.setItem(sessionStorageKey(problemId), created.session_id)
      localStorage.removeItem(codeStorageKey(problemId))
    } catch (err) {
      setStartError(
        err instanceof ApiError ? err.message : '세션을 시작하지 못했습니다.',
      )
    } finally {
      setStarting(false)
    }
  }

  function handleStartOver() {
    if (problemId) {
      localStorage.removeItem(sessionStorageKey(problemId))
      localStorage.removeItem(codeStorageKey(problemId))
    }
    setSession(null)
    setCode('')
    setSubmission(null)
    setSubmitError(null)
  }

  const handleEditorMount: OnMount = (editorInstance) => {
    attachEditor(editorInstance)
  }

  function handleCodeChange(value: string) {
    setCode(value)
    if (problemId) localStorage.setItem(codeStorageKey(problemId), value)
  }

  async function handleSubmit() {
    if (!session || !problemId || Number.isNaN(numericProblemId)) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const created = await apiClient.submissions.create({
        session_id: session.session_id,
        problem_id: numericProblemId,
        code,
        language,
      })
      markSubmission(created.submission_id)

      // 채점이 동기로 끝나므로(백엔드가 Judge0을 기다렸다 응답) 곧바로 verdict를 조회할 수 있다.
      const detail = await apiClient.submissions.get(created.submission_id)
      setSubmission(detail)

      // AC면 백엔드가 세션을 자동으로 종료한다 — 최신 상태를 다시 받아 반영한다.
      const refreshed = await apiClient.sessions.get(session.session_id)
      setSession(refreshed)
      if (session.status === 'active' && refreshed.status !== 'active') {
        endKeystrokeLogging('submitted_ac')
      }
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : '제출하지 못했습니다.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleEndSession(status: 'solved' | 'abandoned') {
    if (!session) return
    setEnding(true)
    try {
      await apiClient.sessions.patch(session.session_id, { status, language })
      setSession({ ...session, status, ended_at: new Date().toISOString() })
      endKeystrokeLogging()
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
            {problemTitle && (
              <span className="font-medium text-gray-700 dark:text-gray-300">
                {problemTitle} ·{' '}
              </span>
            )}
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
          onChange={handleCodeChange}
          onMount={handleEditorMount}
        />

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting || isEnded}
            className="rounded-md bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {submitting ? '채점 중...' : '제출'}
          </button>
          {submission && (
            <span className="text-sm text-gray-500">
              {submission.verdict ? (
                <span
                  className={
                    submission.verdict === 'AC'
                      ? 'font-medium text-emerald-600'
                      : 'font-medium text-red-600'
                  }
                >
                  {VERDICT_LABEL[submission.verdict] ?? submission.verdict}
                </span>
              ) : (
                '채점 중'
              )}
              {submission.runtime_ms != null && ` · ${submission.runtime_ms}ms`}
              {submission.memory_kb != null && ` · ${submission.memory_kb}KB`}
            </span>
          )}
        </div>
        {submitError && <p className="text-sm text-red-600">{submitError}</p>}
      </div>

      <div className="flex flex-col gap-4">
        <FeedbackPanel sessionId={session.session_id} />
        <AnalysisPanel sessionId={session.session_id} enabled={isEnded} />
        <ComparisonStatsWidget />
      </div>
    </div>
  )
}
