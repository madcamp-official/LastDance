import { useEffect, useRef } from 'react'
import type { OnMount } from '@monaco-editor/react'
import { ActivityLogger } from '../lib/activity-logger'

type StandaloneCodeEditor = Parameters<OnMount>[0]

export function useMonacoActivityLogger(
  sessionId: string | null,
  accessToken: string | null,
) {
  const loggerRef = useRef<ActivityLogger | null>(null)

  useEffect(() => {
    if (!sessionId || !accessToken) return
    const logger = new ActivityLogger({ sessionId, accessToken })
    loggerRef.current = logger
    return () => {
      logger.destroy()
      loggerRef.current = null
    }
  }, [sessionId, accessToken])

  function attachEditor(editorInstance: StandaloneCodeEditor) {
    const disposables = [
      editorInstance.onDidChangeModelContent(() => {
        loggerRef.current?.log('edit_snapshot', {
          char_count: editorInstance.getModel()?.getValueLength() ?? 0,
        })
      }),
      editorInstance.onDidPaste(() => {
        loggerRef.current?.log('paste', {})
      }),
      editorInstance.onDidFocusEditorText(() => {
        loggerRef.current?.log('focus', {})
      }),
      editorInstance.onDidBlurEditorText(() => {
        loggerRef.current?.log('blur', {})
      }),
    ]
    return () => disposables.forEach((d) => d.dispose())
  }

  return { attachEditor }
}
