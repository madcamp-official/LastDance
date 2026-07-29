import { AuthLayout } from '../components/Auth/AuthLayout'
import { SignupForm } from '../components/Auth/SignupForm'

export function SignupPage() {
  return (
    <AuthLayout title="회원가입" description="풀이 '과정'까지 분석해 피드백을 주는 코드 테스트 플랫폼, Codeback을 시작해보세요.">
      <SignupForm />
    </AuthLayout>
  )
}
