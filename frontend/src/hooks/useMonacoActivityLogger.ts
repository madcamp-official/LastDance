import { useEffect, useRef } from 'react'
import type { OnMount } from '@monaco-editor/react'
import { KeystrokeLogger } from '../lib/activity-logger'
import type { SessionEndReason } from '../types/api'

type StandaloneCodeEditor = Parameters<OnMount>[0]

export interface UseMonacoActivityLoggerParams {
  sessionId: string | null
  problemId: number | null
  language: string
  getAccessToken: () => string | null
  initialCode?: string
}

/**
 * Monaco 편집 이벤트를 EditOp(dev-plan §2.1)로 변환해 KeystrokeLogger로 흘려보낸다.
 * IME(한글/일본어) 조합 중간 상태는 기록하지 않고, compositionend 확정 시점에 조합 시작 전/후
 * 텍스트를 diff해 하나의 delete+insert로 합쳐 기록한다(api-spec.md 필수 준수 사항).
 *
 * 알려진 한계: Monaco 공개 API로는 편집이 자동완성/자동들여쓰기에 의한 것인지 구분할 신뢰
 * 가능한 신호가 없어(내부적으로 커스텀 source 태깅 없이는 불가), 모든 실사용자 편집을
 * EditOp 기본값인 `src: "user"`로만 기록한다.
 */
export function useMonacoActivityLogger({
  sessionId,
  problemId,
  language,
  getAccessToken,
  initialCode,
}: UseMonacoActivityLoggerParams) {
  const loggerRef = useRef<KeystrokeLogger | null>(null)

  useEffect(() => {
    if (!sessionId || problemId === null) return
    const logger = new KeystrokeLogger({
      sessionId,
      problemId,
      language,
      getAccessToken,
      initialCode,
      editorVersion: '@monaco-editor/react@4',
    })
    loggerRef.current = logger
    return () => {
      logger.destroy()
      loggerRef.current = null
    }
    // language/getAccessToken/initialCode는 세션 시작 시점 값으로 고정한다 — 세션 도중
    // 언어를 바꾸는 흐름은 없으므로 재연결 트리거로 넣지 않는다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, problemId])

  function attachEditor(editorInstance: StandaloneCodeEditor) {
    const domNode = editorInstance.getDomNode()
    let composing = false
    let preCompositionText = ''

    function decomposeChange(rangeOffset: number, rangeLength: number, text: string) {
      if (rangeLength > 0) {
        loggerRef.current?.pushOp({ op: 1, pos: rangeOffset, len: rangeLength })
      }
      if (text.length > 0) {
        loggerRef.current?.pushOp({ op: 0, pos: rangeOffset, txt: text })
      }
    }

    // 조합 시작/종료 시점의 전체 텍스트를 비교해 공통 접두/접미를 잘라내는 최소 diff.
    // 파일이 크지 않은 코딩테스트 코드 특성상 전체 diff 비용은 무시할 만하다.
    function diffAndEmit(preText: string, postText: string) {
      const maxPrefix = Math.min(preText.length, postText.length)
      let prefix = 0
      while (prefix < maxPrefix && preText[prefix] === postText[prefix]) prefix++

      const maxSuffix = maxPrefix - prefix
      let suffix = 0
      while (
        suffix < maxSuffix &&
        preText[preText.length - 1 - suffix] === postText[postText.length - 1 - suffix]
      ) {
        suffix++
      }

      const deleteLen = preText.length - prefix - suffix
      const insertText = postText.slice(prefix, postText.length - suffix)
      decomposeChange(prefix, deleteLen, insertText)
    }

    const handleCompositionStart = () => {
      composing = true
      preCompositionText = editorInstance.getModel()?.getValue() ?? ''
    }
    const handleCompositionEnd = () => {
      if (!composing) return
      composing = false
      const postText = editorInstance.getModel()?.getValue() ?? ''
      diffAndEmit(preCompositionText, postText)
    }
    domNode?.addEventListener('compositionstart', handleCompositionStart)
    domNode?.addEventListener('compositionend', handleCompositionEnd)

    const disposables = [
      editorInstance.onDidChangeModelContent((event) => {
        if (composing) return // 조합 중엔 개별 변경을 무시하고 compositionend에서 통째로 처리
        for (const change of event.changes) {
          decomposeChange(change.rangeOffset, change.rangeLength, change.text)
        }
      }),
      editorInstance.onDidChangeCursorPosition((event) => {
        const model = editorInstance.getModel()
        if (!model) return
        loggerRef.current?.updateCursor(model.getOffsetAt(event.position))
      }),
      // paste도 별도 이벤트 없이 onDidChangeModelContent에서 큰 insert EditOp로 이미 잡힌다.
    ]

    return () => {
      disposables.forEach((d) => d.dispose())
      domNode?.removeEventListener('compositionstart', handleCompositionStart)
      domNode?.removeEventListener('compositionend', handleCompositionEnd)
    }
  }

  function markSubmission(submissionId: string) {
    loggerRef.current?.markSubmission(submissionId)
  }

  /** reason을 주면 명시적 session.end(예: AC 제출)를, 안 주면 그냥 연결만 끊는다(서버가 "closed"로 처리). */
  function endSession(reason?: SessionEndReason) {
    if (reason) {
      loggerRef.current?.end(reason)
    } else {
      loggerRef.current?.destroy()
    }
  }

  return { attachEditor, markSubmission, endSession }
}
