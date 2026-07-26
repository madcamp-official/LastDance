import { useState } from 'react'
import { apiClient } from '../../lib/api'
import { ApiError } from '../../lib/api-client'
import type { Feedback, FeedbackRating } from '../../types/api'

export function FeedbackPanel({ sessionId }: { sessionId: string }) {
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [ratedAs, setRatedAs] = useState<FeedbackRating | null>(null)
  const [loading, setLoading] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  async function handleRequestFeedback() {
    setLoading(true)
    setErrorMessage(null)
    try {
      const res = await apiClient.feedback.create(sessionId)
      setFeedback(res)
      setRatedAs(null)
    } catch (err) {
      setErrorMessage(
        err instanceof ApiError ? err.message : '피드백을 불러오지 못했습니다.',
      )
    } finally {
      setLoading(false)
    }
  }

  async function handleRate(rating: FeedbackRating) {
    if (!feedback) return
    try {
      await apiClient.feedback.rate(feedback.feedback_id, rating)
      setRatedAs(rating)
    } catch {
      // 평가 실패는 조용히 무시 — 버튼을 다시 누르면 재시도된다.
    }
  }

  return (
    <div className="rounded-md border border-gray-200 p-4 dark:border-gray-800">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-medium">피드백</h2>
        <button
          type="button"
          onClick={handleRequestFeedback}
          disabled={loading}
          className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {loading ? '불러오는 중...' : '피드백 보기'}
        </button>
      </div>

      {errorMessage && <p className="text-sm text-red-600">{errorMessage}</p>}

      {feedback && (
        <div className="flex flex-col gap-2">
          <p className="whitespace-pre-wrap text-sm">{feedback.text}</p>
          <p className="text-xs text-gray-500">
            {feedback.model_used} · {new Date(feedback.generated_at).toLocaleString()}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => handleRate('up')}
              aria-pressed={ratedAs === 'up'}
              className="rounded-md border border-gray-300 px-2 py-1 text-sm aria-pressed:bg-indigo-50 dark:border-gray-700 dark:aria-pressed:bg-indigo-950"
            >
              👍
            </button>
            <button
              type="button"
              onClick={() => handleRate('down')}
              aria-pressed={ratedAs === 'down'}
              className="rounded-md border border-gray-300 px-2 py-1 text-sm aria-pressed:bg-indigo-50 dark:border-gray-700 dark:aria-pressed:bg-indigo-950"
            >
              👎
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
