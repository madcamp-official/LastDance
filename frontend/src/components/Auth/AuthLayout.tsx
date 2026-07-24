import type { ReactNode } from 'react'

export function AuthLayout({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-h-[calc(100svh-56px)] items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-lg border border-gray-200 p-6 shadow-sm dark:border-gray-700">
        <h1 className="mb-6 text-xl font-semibold text-gray-900 dark:text-gray-100">{title}</h1>
        {children}
      </div>
    </div>
  )
}
