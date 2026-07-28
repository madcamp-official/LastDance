import { lazy, Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { useAuthStore } from './store/authStore'
import { AppLayout } from './components/Layout/AppLayout'
import { ProtectedRoute } from './routes/ProtectedRoute'
import { ProblemListPage } from './pages/ProblemListPage'

// 아래 4개 페이지가 메인 번들 용량의 대부분을 차지하는 무거운 의존성(zod, katex, Monaco)을
// 끌어오므로 각각 별도 청크로 분리한다: 로그인/회원가입 폼은 zod, 문제 상세는 katex,
// 풀이 화면은 Monaco.
const LoginPage = lazy(() =>
  import('./pages/LoginPage').then((m) => ({ default: m.LoginPage })),
)
const SignupPage = lazy(() =>
  import('./pages/SignupPage').then((m) => ({ default: m.SignupPage })),
)
const ProblemDetailPage = lazy(() =>
  import('./pages/ProblemDetailPage').then((m) => ({
    default: m.ProblemDetailPage,
  })),
)
const SolvePage = lazy(() =>
  import('./pages/SolvePage').then((m) => ({ default: m.SolvePage })),
)
const MyPage = lazy(() =>
  import('./pages/MyPage').then((m) => ({ default: m.MyPage })),
)
const ProblemHistoryPage = lazy(() =>
  import('./pages/ProblemHistoryPage').then((m) => ({
    default: m.ProblemHistoryPage,
  })),
)

function PageFallback() {
  return (
    <div className="px-4 py-16 text-center text-gray-500">불러오는 중...</div>
  )
}

function App() {
  const hydrate = useAuthStore((s) => s.hydrate)

  useEffect(() => {
    hydrate()
  }, [hydrate])

  return (
    <BrowserRouter>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<SignupPage />} />
          <Route element={<AppLayout />}>
            <Route element={<ProtectedRoute />}>
              <Route path="/" element={<Navigate to="/problems" replace />} />
              <Route path="/problems" element={<ProblemListPage />} />
              <Route path="/problems/:problemId" element={<ProblemDetailPage />} />
              <Route path="/problems/:problemId/solve" element={<SolvePage />} />
              <Route path="/me" element={<MyPage />} />
              <Route path="/me/problems/:problemId" element={<ProblemHistoryPage />} />
            </Route>
          </Route>
        </Routes>
      </Suspense>
    </BrowserRouter>
  )
}

export default App
