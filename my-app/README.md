# my-app 1차 전환본

이번 단계에서 반영한 방향은 아래와 같습니다.

- 기존 Django UI는 제거
- 로그인 후 바로 ChatGPT 스타일의 메인 채팅 화면 표시
- 좌측 사이드바에는 아래만 유지
  - 새 채팅
  - 이전 채팅 이력
  - 인폼노트 DB 탭

## 실행

```bash
cd my-app
npm install
npm run dev
```

## 환경 변수

`.env.local` 파일 예시

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```
