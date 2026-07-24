import { InlineMath } from 'react-katex'
import 'katex/dist/katex.min.css'

// 문제 지문에 LaTeX가 포함될 수 있어 $...$ 구간만 수식으로 렌더링한다.
// 구분자 규칙은 백엔드 확정 전까지 잠정: docs/api-spec.md:114 참고.
export function MathText({ text }: { text: string }) {
  const parts = text.split(/(\$[^$]+\$)/g)
  return (
    <p className="whitespace-pre-wrap">
      {parts.map((part, i) =>
        part.length > 1 && part.startsWith('$') && part.endsWith('$') ? (
          <InlineMath key={i} math={part.slice(1, -1)} />
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </p>
  )
}
