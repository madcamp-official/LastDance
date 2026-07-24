import { useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { useAuthStore } from './store/authStore'
import { AppLayout } from './components/Layout/AppLayout'
import { ProtectedRoute } from './routes/ProtectedRoute'
import { LoginPage } from './pages/LoginPage'
import { SignupPage } from './pages/SignupPage'
import { ProblemListPage } from './pages/ProblemListPage'
import { ProblemDetailPage } from './pages/ProblemDetailPage'
import { SolvePage } from './pages/SolvePage'

function App() {
  const hydrate = useAuthStore((s) => s.hydrate)

  useEffect(() => {
    hydrate()
  }, [hydrate])

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route element={<AppLayout />}>
          <Route element={<ProtectedRoute />}>
            <Route path="/" element={<Navigate to="/problems" replace />} />
            <Route path="/problems" element={<ProblemListPage />} />
            <Route path="/problems/:problemId" element={<ProblemDetailPage />} />
            <Route path="/problems/:problemId/solve" element={<SolvePage />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
