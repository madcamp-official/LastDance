import type { DifficultyTier } from '../../types/api'

// A(가장 쉬움) -> G(가장 어려움) 신호등 그라데이션.
const DIFFICULTY_COLOR: Record<DifficultyTier, string> = {
  A: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300',
  B: 'bg-lime-100 text-lime-700 dark:bg-lime-950 dark:text-lime-300',
  C: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-950 dark:text-yellow-300',
  D: 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300',
  E: 'bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300',
  F: 'bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300',
  G: 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300',
}

export function DifficultyBadge({ difficulty }: { difficulty: DifficultyTier | null }) {
  if (!difficulty) {
    return (
      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-500 dark:bg-gray-800 dark:text-gray-400">
        미정
      </span>
    )
  }
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-medium ${DIFFICULTY_COLOR[difficulty]}`}
    >
      {difficulty}
    </span>
  )
}
