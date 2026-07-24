import { AuthLayout } from '../components/Auth/AuthLayout'
import { LoginForm } from '../components/Auth/LoginForm'

export function LoginPage() {
  return (
    <AuthLayout title="로그인">
      <LoginForm />
    </AuthLayout>
  )
}
