# Dialian

Discord 티켓 및 커미션 관리 봇입니다.

## 설치

```powershell
python -m pip install -r requirements.txt
```

프로젝트 최상단에 `.env` 파일을 만들고 값을 설정합니다.

```env
TOKEN=디스코드_봇_토큰
OPENAI_API_KEY=OpenAI_API_키
TRANSLATION_CHANNEL_ID=번역을_실행할_채널_ID
```

`OPENAI_API_KEY`는 선택 사항입니다. 설정하지 않아도 봇은 실행되며, 자동 번역 기능만 비활성화됩니다. `TRANSLATION_CHANNEL_ID`를 설정하면 해당 채널에서만 자동 번역이 실행됩니다.

## 실행

```powershell
python dial.py
```

## 주요 명령어

- `!명령어`, `!도움말`, `!help`: 명령어 목록 표시
- `!티켓생성`: 티켓 생성 패널 전송
- `!계좌전송`, `!티켓닫기`, `!티켓삭제`: 기존 티켓 복구용 명령어
- `!진행 0|25|50|75|100`, `!예상 1일|2일|3일`, `!완료`: 작업 진행 관리

일부 명령어는 관리자 또는 담당 디자이너 권한이 필요합니다.

## 데이터 및 보안

- `.env`, SQLite DB, 백업 파일, 배포 백업 파일은 Git에 포함하지 않습니다.
- 계좌 정보와 티켓 기록은 `data/dialian.db`에 저장됩니다.
- DB는 시작 시와 이후 24시간마다 `data/backups/`에 백업하며, 14일이 지난 백업은 정리합니다.
- 자동 번역 전송 전 이메일, 전화번호, 긴 숫자 형식의 식별 정보는 마스킹합니다.
- GitHub 배포 Secrets에는 `HOST`, `USERNAME`, `SSH_PRIVATE_KEY`, `TOKEN`이 필요하며 자동 번역 사용 시 `OPENAI_API_KEY`도 추가해야 합니다.
