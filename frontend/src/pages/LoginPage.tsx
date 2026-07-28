import { AuthLayout } from '../components/Auth/AuthLayout'
import { LoginForm } from '../components/Auth/LoginForm'

export function LoginPage() {
  return (
    <AuthLayout
      title="로그인"
      description="정답 여부만 알려주는 채점이 아니라, 어떻게 풀었는지 그 과정을 분석해 피드백을 드립니다. 코딩테스트를 앞둔 당신을 위한 코드 테스트 플랫폼."
    >
      <LoginForm />
    </AuthLayout>
  )
}
