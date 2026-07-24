// docs/api-spec.md:141 — 지원 언어 목록은 채점 엔진 착수 시 확정. 예시로 제시된 3개만 우선 노출.
export const SUPPORTED_LANGUAGES = [
  { value: 'python3', label: 'Python 3', monacoId: 'python' },
  { value: 'cpp17', label: 'C++17', monacoId: 'cpp' },
  { value: 'java17', label: 'Java 17', monacoId: 'java' },
] as const

export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number]['value']
