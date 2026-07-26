import { AuthLayout } from '../components/Auth/AuthLayout'
import { SignupForm } from '../components/Auth/SignupForm'

export function SignupPage() {
  return (
    <AuthLayout title="회원가입">
      <SignupForm />
    </AuthLayout>
  )
}
