import { z } from 'zod'

// nickname 2~20자는 docs/api-spec.md의 제안값(팀 확정 필요)을 그대로 사용한
// 클라이언트 UX용 가이드일 뿐, 서버가 최종 검증한다.
export const signupSchema = z.object({
  email: z.string().email('올바른 이메일 형식이 아닙니다.'),
  nickname: z
    .string()
    .min(2, '닉네임은 2자 이상이어야 합니다.')
    .max(20, '닉네임은 20자 이하여야 합니다.'),
  password: z.string().min(8, '비밀번호는 8자 이상이어야 합니다.'),
})

export type SignupFormValues = z.infer<typeof signupSchema>

export const loginSchema = z.object({
  email: z.string().email('올바른 이메일 형식이 아닙니다.'),
  password: z.string().min(1, '비밀번호를 입력해주세요.'),
})

export type LoginFormValues = z.infer<typeof loginSchema>
