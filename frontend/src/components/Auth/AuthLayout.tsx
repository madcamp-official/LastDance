import type { ReactNode } from 'react'

export function AuthLayout({
  title,
  description,
  children,
}: {
  title: string
  description?: string
  children: ReactNode
}) {
  return (
    <div className="flex min-h-[calc(100svh-56px)] items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-lg border border-gray-200 p-6 shadow-sm dark:border-gray-700">
        <p className="mb-1 text-sm font-semibold text-indigo-600">LastDance</p>
        <h1
          className={`text-xl font-semibold text-gray-900 dark:text-gray-100 ${description ? 'mb-2' : 'mb-6'}`}
        >
          {title}
        </h1>
        {description && (
          <p className="mb-6 text-sm text-gray-500 dark:text-gray-400">{description}</p>
        )}
        {children}
      </div>
    </div>
  )
}
