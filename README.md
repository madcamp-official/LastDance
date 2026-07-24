# LastDance

## 팀원

| 이름 | GitHub | 역할 |
|---|---|---|
| 이재준 | dannyiscard |  |
| 임유빈 | lunar-yoobin |  |

---

## 기획안

> 프로젝트 주제, 목적, 핵심 기능, 예상 사용자, 팀원별 역할 등 정리

- **주제:** 
- **목적:** 사용자들로 하여금 본인의 PS 문제풀이의 피드백을 받을 수 있다.
- **핵심 기능:** 
- **예상 사용자:** 코딩테스트가 얼마 남지 않은 사람들

---

## 기능 명세서

> 구현할 기능을 사용자 관점에서 정리하고, 필수 기능과 선택 기능을 구분

### 필수 기능

- [이메일을 통한 회원가입 및 로그인]
- [PS 문제들에 대한 접근 및 풀이]
- [내가 푼 문제들에 대한 기록]
- [내 풀이에 대한 자세한 사후 피드백]

### 선택 기능


## IA 및 화면 설계서

> 서비스의 전체 페이지 구조와 페이지 간 이동 흐름; 각 페이지의 주요 UI 구성, 입력 요소, 버튼, 사용자 행동 흐름 등을 간단한 와이어프레임 형태로 정리

<!-- Figma 링크 또는 이미지 첨부 -->

---

## DB 스키마

---

## API 문서

> API 주소, 요청 방식, 요청값, 응답값, 에러 상황을 정리

### 인증 (`app/api/auth.py`)

| Method | Endpoint | 설명 | 요청 (Body) | 응답 |
|---|---|---|---|---|
| POST | `/auth/signup` | 회원가입 | {"email": "example@example.com", "nickname": "example001", "password": "test12345"} | 201 {"user_id": "1", "nickname": "example001", "email": "example@example.com", "profile_img": null} |
| POST | `/auth/login` | 로그인, access/refresh 토큰 발급 | {"email": "example@example.com", "password": "test12345"} | 200 {"access_token": "\<jwt>", "refresh_token": "\<jwt>", "token_type": "bearer"} |
| POST | `/auth/refresh` | refresh token으로 access token 재발급 | {"refresh_token": "\<jwt>"} | 200 {"access_token": "\<jwt>", "token_type": "bearer"} |
| POST | `/auth/logout` | 서버에 저장된 refresh token 폐기 | {"refresh_token": "\<jwt>"} | 200 {"message": "로그아웃 하였습니다."} |


---

## 배포 결과물

> 접속 가능한 링크, 실행 방법, 주요 구현 내용

- **실행 방법:** 

---

## 회고 문서

> 개발 과정에서의 어려움, 해결 방법, 역할 분담, 다음에 개선할 점 (KPT 방법론 참고)

### Keep
- **Frontend와 Backend로 역할분담**:

### Problem
- **상대적으로 부실했던 초기 설계**: 

### Try
- **LLM을 활용한 초반 설계**: 

---

