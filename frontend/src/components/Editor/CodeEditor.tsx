import Editor, { type OnMount } from '@monaco-editor/react'
import { SUPPORTED_LANGUAGES } from '../../lib/languages'

export interface CodeEditorProps {
  language: string
  value: string
  onChange: (value: string) => void
  onMount?: OnMount
}

export function CodeEditor({ language, value, onChange, onMount }: CodeEditorProps) {
  const monacoLanguage =
    SUPPORTED_LANGUAGES.find((l) => l.value === language)?.monacoId ?? 'plaintext'

  return (
    <Editor
      height="60vh"
      language={monacoLanguage}
      value={value}
      onChange={(v) => onChange(v ?? '')}
      onMount={onMount}
      theme="vs-dark"
      options={{ fontSize: 14, minimap: { enabled: false } }}
    />
  )
}
