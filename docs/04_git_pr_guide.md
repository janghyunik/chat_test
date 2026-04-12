# 현재 Git / PR 상태 설명

이 문서는 현재 캡처 기준 Git 상태가 왜 PR로 이어지지 않는지 설명하는 문서입니다.

## 현재 상태 요약

캡처를 보면 현재 로컬 브랜치는 `main`이고, 아래 커밋이 로컬에만 있습니다.

- `version 0.1.0 frontend, backend 기초 구성 완료`

반면 원격 `origin/main`은 그 아래 커밋까지만 올라가 있습니다.

즉 현재 상태는 아래와 같습니다.

```text
로컬 main      = origin/main 보다 1커밋 앞섬
원격 origin/main = 아직 새 커밋이 없음
```

## 왜 PR이 바로 안 생기는가

PR은 보통 아래처럼 **서로 다른 두 브랜치**를 비교할 때 만듭니다.

- base: `main`
- compare: `feature/무언가`

그런데 지금은 새 작업 커밋이 `main`에 직접 올라가 있습니다.

즉 비교 대상이 이런 상태가 아닙니다.

```text
main <- feature/phase1-ui
```

대신 이런 상태입니다.

```text
main(로컬만 앞섬)
origin/main(원격은 뒤처짐)
```

이 경우에는 보통 PR이 아니라 아래 둘 중 하나가 됩니다.

1. `main`에 바로 push
2. 지금 커밋에서 새 브랜치를 따서 push 후 PR 생성

## 현재 상태를 안전하게 PR 흐름으로 바꾸는 방법

### 방법 A. 지금 커밋에서 새 브랜치를 만드는 방법
가장 추천합니다.

```bash
git checkout -b feature/phase1-docs-theme
git push -u origin feature/phase1-docs-theme
```

그 다음 GitHub에서:
- base: `main`
- compare: `feature/phase1-docs-theme`

로 PR을 생성하면 됩니다.

### 방법 B. 이미 main에 올려도 되는 단독 작업이면 그냥 push

```bash
git push origin main
```

하지만 이 경우 PR 없이 바로 main이 업데이트됩니다.

## 앞으로 가장 안전한 권장 순서

```bash
git checkout main
git pull origin main
git checkout -b feature/작업이름
# 코드 수정
git add .
git commit -m "feat: 작업 설명"
git push -u origin feature/작업이름
```

그 다음 GitHub에서 PR을 만듭니다.

## 지금 사용자 상황에서 추천 명령어

이미 로컬 `main`에 커밋이 하나 생긴 상태라면:

```bash
git checkout -b feature/phase1-docs-theme
git push -u origin feature/phase1-docs-theme
```

이후 GitHub에서 PR 생성.

## main을 다시 깨끗하게 유지하고 싶다면

브랜치를 만든 뒤 `main`을 원격 상태로 되돌리는 방법도 있습니다.

```bash
git checkout main
git reset --hard origin/main
```

주의:
- 이 명령은 로컬 `main`에서 아직 원격에 안 올린 커밋을 제거합니다.
- 먼저 새 브랜치에 커밋을 보존한 뒤 사용해야 안전합니다.
