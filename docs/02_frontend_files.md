# my-app 파일 설명서

이 문서는 `my-app` 폴더의 현재 파일 역할을 파일 단위로 정리한 문서입니다.

## 루트 설정 파일

### `my-app/package.json`
Next.js 프로젝트의 기본 설정 파일입니다.

역할:
- 프로젝트 이름/버전 정의
- 실행 스크립트 정의 (`dev`, `build`, `start`, `lint`)
- 의존성 버전 관리

### `my-app/package-lock.json`
설치된 npm 패키지 버전을 고정하는 자동 생성 파일입니다.

원칙:
- 보통 수정하지 않고 npm이 자동 관리합니다.
- 팀원이 같은 패키지 버전을 설치하게 만드는 데 중요합니다.

### `my-app/tsconfig.json`
TypeScript 컴파일 옵션 파일입니다.

특징:
- `strict: true`
- `@/*` 경로 별칭 사용
- Next.js 플러그인 설정 포함

### `my-app/next.config.ts`
Next.js 런타임 설정 파일입니다.

현재는 `reactCompiler: true`만 켜 둔 상태입니다.

### `my-app/eslint.config.mjs`
ESLint 규칙 설정 파일입니다.

역할:
- Next.js 권장 규칙 적용
- `.next`, `build` 등 불필요 폴더 무시

### `my-app/postcss.config.mjs`
PostCSS 설정 파일입니다.

현재는 Tailwind용 PostCSS 플러그인만 등록되어 있습니다.

### `my-app/.gitignore`
Git에 올리지 않을 파일 목록입니다.

대표 예:
- `node_modules`
- `.next`
- `.env*`
- 로그 파일

### `my-app/README.md`
현재 1차 전환본 프런트 목적과 실행 방법을 설명합니다.

### `my-app/AGENTS.md`
Next.js 버전 변화에 주의하라는 내부 메모성 문서입니다.

### `my-app/CLAUDE.md`
`AGENTS.md`를 참조하는 매우 짧은 연결 파일입니다.

### `my-app/middleware.ts`
로그인 여부를 페이지 진입 전에 검사하는 Next.js 미들웨어입니다.

역할:
- `/login` 접근 시 이미 토큰이 있으면 `/`로 돌려보냄
- `/`, `/inform` 접근 시 토큰이 없으면 `/login`으로 보냄

현재 검사 쿠키 이름:
- `chat_test_token`

---

## public 폴더

### `my-app/public/file.svg`
### `my-app/public/globe.svg`
### `my-app/public/next.svg`
### `my-app/public/vercel.svg`
### `my-app/public/window.svg`
기본 Next.js 생성 정적 아이콘 파일입니다.

현재 화면 핵심 기능에는 직접 사용되지 않습니다.

---

## app 폴더

### `my-app/src/app/layout.tsx`
전체 페이지 공통 레이아웃 파일입니다.

역할:
- 전역 CSS 등록
- `<html lang="ko">` 설정
- 앱 전체 메타데이터 제목/설명 설정

### `my-app/src/app/globals.css`
현재 프런트의 전역 스타일 핵심 파일입니다.

역할:
- 전체 색상 변수
- 사이드바 레이아웃
- 채팅 화면 스타일
- 로그인 카드 스타일
- 인폼노트 테이블 스타일
- 반응형 규칙

**색상 변경의 핵심 파일도 바로 이 파일입니다.**

### `my-app/src/app/page.tsx`
메인 `/` 페이지 엔트리 파일입니다.

역할:
- 실제 UI는 `ChatScreen` 컴포넌트에 위임

### `my-app/src/app/login/page.tsx`
로그인 페이지 엔트리 파일입니다.

역할:
- 실제 UI는 `LoginScreen`에 위임

### `my-app/src/app/inform/page.tsx`
인폼노트 DB 페이지 엔트리 파일입니다.

역할:
- 실제 UI는 `InformScreen`에 위임

### `my-app/src/app/favicon.ico`
브라우저 탭 아이콘입니다.

---

## components 폴더

### `my-app/src/components/app-sidebar.tsx`
좌측 사이드바 컴포넌트입니다.

역할:
- 새 채팅 버튼
- 채팅 / 인폼노트 DB 탭 이동
- 이전 채팅 목록 표시
- 로그아웃 버튼 처리

핵심 로직:
- `/api/chat/sessions` 호출로 이전 대화 목록 조회
- 현재 URL의 `chat` 파라미터를 읽어 active 상태 표시
- 로그아웃 시 토큰 삭제 후 `/login` 이동

### `my-app/src/components/chat-screen.tsx`
메인 채팅 화면 핵심 컴포넌트입니다.

역할:
- 현재 채팅 세션 생성/조회
- 메시지 목록 렌더링
- 질문 입력 및 전송
- 답변 수신 후 세션 상태 갱신

핵심 흐름:
1. `chat` 쿼리 파라미터 확인
2. 없으면 새 세션 생성
3. 있으면 해당 세션 조회
4. 메시지 전송 시 `/api/chat/sessions/{id}/messages` 호출

### `my-app/src/components/inform-screen.tsx`
인폼노트 DB 화면 컴포넌트입니다.

역할:
- 공정 / 라인 / 설비명 필터 UI
- 인폼노트 조회 버튼
- 결과 테이블 렌더링

핵심 흐름:
- 상태값으로 필터를 관리
- URLSearchParams로 쿼리 문자열 생성
- `/api/inform/records` 호출 후 표에 반영

### `my-app/src/components/login-screen.tsx`
로그인 화면 핵심 컴포넌트입니다.

역할:
- 아이디/비밀번호 입력
- 로그인 API 호출
- 성공 시 토큰 저장
- 메인 페이지로 이동

현재 기본값:
- `admin`
- `admin1234`

---

## lib 폴더

### `my-app/src/lib/api.ts`
프런트 API 통신 공통 유틸입니다.

역할:
- API 기본 URL 관리
- 쿠키에서 토큰 읽기
- 공통 fetch 래퍼 제공
- Authorization 헤더 자동 부착
- 401 발생 시 `/login`으로 이동
- 토큰 저장/삭제 함수 제공

핵심 함수:
- `apiFetch()`
- `setAuthToken()`
- `clearAuthToken()`

---

## 앞으로 손보기 좋은 순서

1. `globals.css`에서 색상 체계 정리
2. `chat-screen.tsx`에서 로딩/오류 UI 개선
3. `app-sidebar.tsx`에서 채팅 제목 편집/삭제 추가
4. `inform-screen.tsx`에 날짜 범위 필터 추가
5. `api.ts`를 server/client 분리형으로 개선
