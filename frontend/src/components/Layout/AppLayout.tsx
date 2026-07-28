import { Link, Outlet, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../../store/authStore'

export function AppLayout() {
  const navigate = useNavigate()
  const user = useAuthStore((s) => s.user)
  const status = useAuthStore((s) => s.status)
  const logout = useAuthStore((s) => s.logout)

  async function handleLogout() {
    await logout()
    navigate('/login')
  }

  return (
    <div className="min-h-svh bg-white text-gray-900 dark:bg-gray-950 dark:text-gray-100">
      <header className="flex h-14 items-center justify-between border-b border-gray-200 px-4 dark:border-gray-800">
        <Link to="/problems" className="font-semibold">
          LastDance
        </Link>
        {status === 'authenticated' && (
          <div className="flex items-center gap-3 text-sm">
            <Link to="/me" className="text-gray-500 hover:text-gray-700 hover:underline dark:hover:text-gray-300">
              {user?.nickname ?? '내 계정'}
            </Link>
            <button
              type="button"
              onClick={handleLogout}
              className="rounded-md border border-gray-300 px-3 py-1 hover:bg-gray-100 dark:border-gray-700 dark:hover:bg-gray-800"
            >
              로그아웃
            </button>
          </div>
        )}
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  )
}
