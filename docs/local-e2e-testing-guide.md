# 로컬 E2E 수동 테스트 가이드 (frontend-main 기준)

목적: mock API가 아니라 **실제 백엔드(Postgres 대신 로컬 SQLite, Redis, Kafka, Judge0)**에
프론트엔드를 붙여서 회원가입부터 풀이 → 제출 → 채점 → 피드백 → 행동 분석 결과 조회까지
전체 흐름을 브라우저로 직접 확인하기 위한 가이드입니다.

## 0. 먼저 알아야 할 것 — 알려진 이슈/제약

| 항목 | 상태 | 설명 |
|---|---|---|
| [#5](https://github.com/madcamp-official/LastDance/issues/5) WebSocket 라이브러리 누락 | **해결됨** | `requirements.txt`에 `websockets` 추가됨(이 브랜치에 포함) |
| [#6](https://github.com/madcamp-official/LastDance/issues/6) Judge0 4xx/5xx 응답이 전부 "연결 불가"로 표시됨 | **미해결** | 채점이 실패하면 원인(요청 문제/Judge0 다운/샌드박스 내부 오류)과 무관하게 항상 같은 에러 메시지가 뜸. 아래 3단계에서 이 상태로 테스트해도 됨 — 다만 에러 메시지만 보고 "연결 문제"로 단정하지 말 것 |
| [#7](https://github.com/madcamp-official/LastDance/issues/7) `/auth/me`의 `created_at` 타임존 누락 | **미해결** | 사소함, 테스트 진행에 지장 없음 |
| Judge0 실행 샌드박스(cgroup) 문제 | **macOS(특히 Apple Silicon)에서 확인됨, Windows는 미확인** | `isolate`가 cgroups v1 전용인데 macOS Docker Desktop은 v2라 실제 코드 실행이 항상 실패했음. Windows(WSL2 커널)에서는 다를 수 있으니 **미리 안 될 거라 가정하지 말고 3-6단계에서 직접 확인** — 안 되면 배포 서버(`api.codeback.madcamp-kaist.org`)로 채점만 별도 확인 |
| LLM 피드백이 fallback 문구만 나옴 | **버그 아님, 의도된 동작** | vLLM 서버가 원격 클라우드 호스트라 로컬에서 접근 불가. `200 OK` + "피드백 생성 서버에 연결할 수 없어..." 문구가 뜨는 게 정상 |
| 문제 목록에 1개만 보임 | **정상** | 실제 AtCoder_100 100문제 데이터는 git에 없음(아래 시딩 스크립트로 데모 문제 1개만 생성). 실제 100문제는 `api.codeback.madcamp-kaist.org`에만 있음 |

## 1. 브랜치 준비

```bash
git checkout frontend-main
git pull
```

## 2. 인프라 기동 (Redis + Kafka + Judge0)

```bash
cd backend
docker compose up -d redis kafka judge0-db judge0-redis judge0-server judge0-workers
docker compose ps
```

전부 `Up`이어야 합니다. 만약 6379/9092/2358 포트가 이미 다른 프로그램에서 쓰이고 있다면
`docker-compose.yml`에서 해당 서비스의 왼쪽 포트만 바꾸고(예: `"16379:6379"`), 4단계의
`REDIS_URL` 등 환경변수도 그 포트에 맞춰주면 됩니다.

## 3. 백엔드 파이썬 환경

```bash
cd backend
python -m venv .venv
```

가상환경 활성화:
- macOS/Linux: `source .venv/bin/activate`
- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- Windows cmd: `.venv\Scripts\activate.bat`

```bash
pip install -r requirements.txt
```

## 4. 데모 문제 시딩

문제 1개(`problem_id=1`, "세 정수의 합")와 채점용 테스트케이스를 만들어줍니다.
`backend/` 디렉토리, venv 활성화된 상태에서:

```bash
python scripts/seed_demo_problem.py
```

이미 있으면 조용히 건너뛰므로 여러 번 실행해도 안전합니다. 실행하면:
- `backend/app.db`(SQLite)에 문제 1개가 들어감
- 레포 루트에 `AtCoder_100/sum3/io/testcases.csv`가 생김(채점용, git에는 안 올라감)

## 5. 백엔드 기동

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

기동 로그에 `No supported WebSocket library detected` 경고가 **없어야** 정상입니다
(있으면 3단계에서 requirements.txt가 제대로 안 깔린 것).

## 6. 프론트엔드 — 실제 백엔드 모드로 실행

```bash
cd frontend
npm install
```

`.env.local` 파일을 만들어서(git에는 안 올라감):
```
VITE_USE_MOCK_API=false
VITE_API_BASE_URL=http://localhost:8001
```

```bash
npm run dev -- --port 3000
```

**반드시 3000번 포트여야 합니다** — 백엔드 CORS 허용 목록(`backend/app/main.py`)이
`localhost:3000`으로 고정돼 있어서, 다른 포트로 띄우면 브라우저에서 CORS 에러가 납니다.

## 7. 브라우저 체크리스트 (http://localhost:3000)

1. 회원가입 → 로그인
2. 문제 목록 → "세 정수의 합" 클릭 → 문제 상세 확인 → "풀기"
3. 언어 선택(Python 3 권장) → "시작하기"
4. 에디터에 코드 입력 — 개발자도구 Network 탭에서 `/ws/events` WebSocket 연결이 붙는지,
   Console에 에러가 없는지 확인
5. 예시 코드(파이썬):
   ```python
   a, b, c = map(int, input().split())
   print(a + b + c)
   ```
6. "제출" 클릭
   - 동기 채점이라 새로고침 없이 verdict가 바로 떠야 함
   - 여기서 실제 채점(AC/WA)이 되면 → cgroup 문제 없는 것, 그대로 진행
   - `JUDGE_INTERNAL_ERROR`/`JUDGE_UNAVAILABLE`이 뜨면 → macOS에서 겪은 cgroup 문제일
     가능성이 높음(0단계 표 참고). 백엔드 터미널 로그와 `docker logs <judge0-server
     컨테이너명>`으로 실제 원인 확인 권장
   - 일부러 오답 코드로도 한번 제출해서, 세션이 끝나지 않고 코드도 안 지워져서
     **이어서 수정 가능한지** 확인
7. 오른쪽 패널 "피드백 보기" 클릭 → fallback 문구라도 `200 OK`로 정상 표시되는지 확인
8. "포기" 또는 "해결로 종료" 클릭 → 세션 상태 변경 확인 → 오른쪽에 **"행동 분석"**
   패널이 몇 초 폴링하다가 phase별 시간/정지·재작성 목록/패턴을 보여주는지 확인
   (이번에 새로 만든 것 중 가장 검증하고 싶은 부분)

## 8. 정리

```bash
# 백엔드(uvicorn)/프론트(vite) 터미널 각각 Ctrl+C
cd backend
docker compose down   # 컨테이너 정리. 데이터까지 지우려면 `down -v`
```

## 문제가 생기면

- 백엔드 로그(`uvicorn` 실행 중인 터미널)와 `docker compose logs <서비스명>`을 같이 확인
- WebSocket 관련 문제는 브라우저 개발자도구 Network 탭에서 `ws://localhost:8001/ws/events`
  요청의 상태를 확인
- 위 표에 없는 새로운 문제를 발견하면 GitHub 이슈로 남겨주세요(#6, #7과 같은 패턴 참고)
