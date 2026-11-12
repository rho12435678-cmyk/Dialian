# Dialian · Dial Dev Hub

DDH는 Roblox 개발자가 **포트폴리오를 공유하고 구인·구직으로 연결되는 커뮤니티**입니다.
의뢰와 거래 조건은 이용자들이 DM에서 직접 협의합니다. Dialian은 주문·견적·결제·납품을 처리하지 않습니다.

## 구현된 기능

- 채널·음성 채널·역할·권한을 생성/갱신하는 `/ddh setup`
- 규칙 동의 버튼으로 멤버 역할 지급
- GFX / UI / 복장 / 스크립터 / VFX / 빌더 / 모델러 / 애니메이터의 8개 분야
- 분야별 포트폴리오, 구인·구직, 작품 피드백, 개발 질문·자료 채널
- 파트너 문의 / 서버 피드백 / 개발자 신청 비공개 티켓
- 개발 분야 복수 선택 → 포트폴리오 제출 → 운영진 승인/반려 → 해당 분야 역할 지급
- 파트너 승인 시 파트너 역할 지급 (파트너 소개 게시물은 운영진이 검토 후 직접 게시)
- 한국 시간 오후 6시, 기본 2일 간격 자동 이용 안내 (전체 멘션 없음)
- 재시작 후에도 작동하는 패널·티켓 버튼, 중복 티켓/공지 방지
- 처리 중 중단된 티켓 생성·승인·종료 재시도, 운영 로그, 검증된 일일 DB 백업

## 최초 실행

Python **3.12 이상**이 필요합니다. CI는 3.12에서 실행합니다.

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows에서는 .venv\Scripts\activate
python -m pip install -r requirements.txt
cp .env.example .env
```

`.env`에 **기존 Dialian 봇 토큰**과 **새 DDH 서버 ID**를 넣습니다.
토큰은 GitHub 코드·Discord 메시지에 붙여넣지 마세요.

```dotenv
TOKEN=봇_토큰
DDH_GUILD_ID=새_DDH_서버_ID
DDH_DATABASE=data/ddh.db
DDH_NOTICE_HOUR=18
DDH_NOTICE_INTERVAL_DAYS=2
DDH_NOTICES_ENABLED=true
```

서버 ID 복사: Discord 설정 → 고급 → 개발자 모드 → DDH 서버 메뉴 → 서버 ID 복사.
설정된 ID와 다른 서버에서는 명령어·버튼·공지·서버 구성이 작동하지 않습니다.
ID가 없거나 잘못된 형식이면 로그인 전에 종료합니다.

### 봇 초대와 권한

Discord Developer Portal의 Dialian 애플리케이션에서 OAuth2 URL Generator를 열고
`bot`, `applications.commands` 범위로 **DDH 서버에 봇을 초대**합니다.

봇 자체 역할에는 다음 권한이 필요합니다.

- 역할 관리, 채널 관리, 채널 보기, 메시지 보내기, 메시지 기록 보기
- 링크 첨부, 파일 첨부, 메시지 관리
- 멤버 타임아웃, 멤버 음소거, 멤버 헤드셋 음소거, 멤버 이동
- 연결, 말하기

운영진 역할에 위임할 권한까지 봇이 보유해야 역할을 생성할 수 있습니다.
봇 역할을 **DDH 운영진·개발자·파트너·멤버·분야 역할보다 위**에 놓으세요.
관리자 권한은 필수가 아닙니다. Server Members / Message Content 같은 특권 인텐트도 필요 없습니다.
이 구현은 메시지 내용을 읽는 `!명령어` 대신 슬래시 명령어를 사용합니다.

```bash
python dial.py --check-config  # 설정만 검증; Discord 접속 없음
python dial.py
```

1. DDH에서 `/ddh plan`으로 구성표를 확인합니다.
2. `/ddh setup`으로 권한·이름 중복·기존 채널 재사용 여부를 점검합니다 (변경 없음).
3. `/ddh setup apply:True`로 실제 구성과 안내 패널을 생성합니다.
4. 소유자가 믿을 수 있는 운영자에게 **DDH 운영진** 역할을 직접 부여합니다.
5. 시작·규칙의 참여 버튼, 문의·신청의 신청 버튼을 일반 계정으로 확인합니다.

봇 로그인만으로 역할·채널을 생성하거나 기존 DDS 채널을 변경하지 않습니다.
구성을 반복 적용하면 저장된 ID를 기준으로 이름·분류·권한을 복구합니다.
도중에 권한/네트워크 오류가 나면 원인을 해결한 뒤 같은 명령을 다시 실행하세요.
구성이 끝나기 전에는 새 티켓과 자동 공지를 활성화하지 않습니다.

## 채널 구성

| 분류 | 채널 |
|---|---|
| 안내 | 시작·규칙, 공지사항, 이용안내 |
| 커뮤니티 | 개발자료, 자유채팅, english-chat, 개발질문, 작품공유·피드백, 모임일정, 잡담 |
| 구인·구직 | 구인, 구직 |
| 포트폴리오 | gfx / ui / clothing / scripting / vfx / building / modeling / animation 분야별 채널 |
| 음성 | 라운지, 공동작업실, 미팅룸 |
| 문의 | 문의·신청, 파트너-소개 |
| 비공개 문의 | 문의 진행, 문의 보관 (채워지면 추가 카테고리 생성) |
| 운영 | 운영진-대화, 운영-로그, 운영진-회의 |

기본 구성은 **역할 12개, 카테고리 9개, 텍스트 24개, 음성 4개**입니다.
`@everyone`과 봇 역할은 역할 수에서 제외합니다. 문의 채널은 필요할 때 추가됩니다.

사진의 `Information`, `Text Channels`, `Voice Channels` 카테고리와
`welcome-and-rules`, `announcements`, `resources`, `general`, `meeting-plans`,
`off-topic`, `Lounge`, `Meeting Room`은 이름이 유일하면 재사용합니다.
이름이 겹치거나 종류가 다르면 적용 전에 오류를 표시합니다.
목록 밖의 기존 채널·메시지는 자동 삭제하지 않습니다.

### 권한 정책

| 역할 | 열람 | 작성·음성 | 비공개 문의 |
|---|---|---|---|
| 방문자 | 공개 채널 | 규칙 동의 전 채팅·음성 이용 불가 | 없음 |
| 멤버 | 공개 채널 | 커뮤니티·구인·공개 음성 | 본인의 진행 중 티켓 |
| 개발자 | 공개 채널 | 멤버 권한 + 구직 | 본인의 진행 중 티켓 |
| 분야 역할 | 공개 채널 | 승인받은 분야의 포트폴리오 작성 | 본인의 진행 중 티켓 |
| 파트너 | 공개 채널 | 멤버와 동일; 운영 권한 없음 | 본인의 진행 중 티켓 |
| DDH 운영진 | 모든 관리 대상 채널 | 공지·심사·메시지 관리·타임아웃·음성 관리 | 모든 티켓·보관함 |

서버 소유자와 Discord 관리자 권한 보유자는 Discord의 관리자 권한으로 모든 채널에 접근할 수 있습니다.
구성 명령은 소유자/관리자 전용입니다. 운영진 역할 자체에 관리자·역할 관리·채널 관리 권한은 주지 않습니다.
규칙 동의·승인으로 지급하는 역할에 관리 권한이 추가되어 있으면 역할 지급을 중단합니다.

`/ddh setup apply:True`는 `@everyone`의 기본 권한도 열람 중심으로 맞춥니다.
관리 대상 채널의 권한 덮어쓰기를 지정된 정책으로 교체하므로, 수동 예외 권한은 다시 설정해야 합니다.
새로운 DDH 서버에 적용하는 구성을 전제로 합니다.

## 문의·심사 흐름

- 규칙 동의 후 문의 종류를 선택합니다.
- 개발자 신청에서는 분야를 복수 선택하고 Roblox 사용자명, 포트폴리오 링크, 작업 경력·담당 부분을 작성합니다.
- 신청자·운영진·봇만 보는 텍스트 채널이 생성됩니다. 같은 종류의 진행 중 티켓은 1개, 새 생성은 1분에 1회입니다.
- 운영진은 **승인/반려 버튼 → 처리 사유 입력**으로 결정합니다. 일반 개발자/파트너/신청자는 심사할 수 없습니다.
- 개발자 승인: 멤버 + 개발자 + 선택한 분야 역할. 파트너 승인: 멤버 + 파트너.
- 피드백은 승인/반려 없이 대화 후 종료합니다. 종료는 신청자 또는 운영진만 가능합니다.
- 종료하면 신청자 접근을 제거하고 운영진 전용 보관함에 보관합니다. 자동 영구 삭제는 하지 않습니다.
- 신청 정보와 심사 상태는 SQLite, 대화·첨부 파일은 Discord 채널에 남습니다.
- 승인 이후 역할 회수는 운영진이 Discord에서 직접 처리합니다. 승인된 역할을 재시작 때 자동으로 재지급하지 않습니다.

신청 화면(분야 선택 5분, 입력 10분)은 일시적이므로 재시작 후 다시 열어야 할 수 있습니다.
이미 게시된 규칙·문의·티켓 관리 버튼은 재시작 후에도 작동합니다.
DB 저장 직전 연결이 끊겨도 채널 topic의 티켓 ID로 기존 티켓을 찾아 복구합니다.
역할 지급 전에 승인 의사를 기록하므로 일부 역할만 지급된 채 중단되어도 자동으로 재시도합니다.
승인 중에는 반려/종료를 막습니다. 봇 권한·역할 순서를 복구하면 승인 작업이 이어집니다.

## 명령어와 공지

| 명령어 | 사용자 | 동작 |
|---|---|---|
| `/ddh plan` | 관리자 | 구성표 미리보기 |
| `/ddh setup` | 관리자 | 적용 전 검사 |
| `/ddh setup apply:True` | 관리자 | 구성/권한/패널 생성·복구 |
| `/ddh panels` | 관리자 | 삭제된 안내 패널 복구 또는 문구 갱신 |
| `/ddh notices enabled:True/False` | 관리자 | 정기 공지 켜기/끄기 (DB에 유지) |
| `/ddh status` | 누구나 | 연결·구성·공지 상태 |

정기 공지는 최초 구성 날짜를 기준으로 2일 간격, 한국 시간 18시에 공지사항에 게시합니다.
해당 날짜의 18시 이후 재접속하면 당일 공지를 확인/복구하며, 지난 날짜 공지를 몰아서 보내지 않습니다.
공지 메시지 ID와 고유 푸터로 재시작 및 DB 기록 실패 후 중복 발송을 줄입니다.
주기/시각은 환경 변수로 변경합니다. `/ddh notices`로 설정한 켜기/끄기가 환경 변수보다 우선합니다.

## 기존 DDS 기능 정리

다음 기능과 관련 코드·테스트·종속성을 제거했습니다.

- GFX·복장·UI 주문, 가격표·묶음·할인, 디자이너 선택·배정, 계좌·결제 안내
- 진행률·납기·작업 완료·구매자 역할, 구매 후기·별점·매출·디자이너 통계
- 출석·포인트·랭킹·단골, 미니게임·포인트 베팅, FAMILY 유료 구독
- DDS 판매·출시·업데이트 공지와 하드코딩된 DDS 채널/역할/서버 ID
- 개인 DM 거래 차단, 강제 Roblox 착용 아이템 가격 인증, 자동 닉네임 변경
- OpenAI 자동 번역과 번역 API 설정, 채팅에서 원격 git 업데이트/재시작 명령

기존 `data/dialian.db` 및 `database/database.db`, 서버의 과거 티켓·메시지를 삭제하거나 변환하지 않습니다.
새 DB 기본 경로는 `data/ddh.db`입니다. DDS 테이블이 들어 있는 파일을 지정하면 열람 점검 후 초기화를 거부합니다.
기존 소스는 Git 이력에 남습니다. 과거 거래 기록은 필요 시 별도로 보관/열람하세요.

## 기존 VPS에서 재가동

### DigitalOcean 콘솔에서 직접 실행

등록할 DDH 서버 ID는 `1570276882669240383`입니다. 봇을 이 서버에 초대하고
필수 권한을 설정한 뒤 기존 VPS의 root Bash 콘솔에서 실행합니다.
현재 DDH 변경안은 `feat/ddh-community-rebuild` 브랜치에 있습니다.

```bash
(
set -e
cd /root/Dialian
git diff --quiet
git diff --cached --quiet
git fetch origin feat/ddh-community-rebuild
git checkout --detach origin/feat/ddh-community-rebuild
bash deploy/console.sh
)
```

토큰은 실행 중 표시되는 입력란에만 넣습니다. 입력 내용은 화면에 표시되지 않습니다.
스크립트는 토큰을 Git이나 셸 명령 이력에 쓰지 않고 설치 프로그램에 환경 변수로 전달합니다.
성공하면 Discord에서 `/ddh setup`으로 점검하고 `/ddh setup apply:True`로 서버를 구성합니다.
GitHub Actions 배포도 사용할 경우 Secrets의 `TOKEN`을 현재 토큰과 일치시켜 주세요.

### GitHub Actions에서 실행

기존 저장소에 있던 `/root/Dialian` 경로와 `HOST`, `USERNAME`, `SSH_PRIVATE_KEY`, `TOKEN` GitHub Secrets를 재사용합니다.
자동 `main` 푸시 배포는 **수동 실행 Deploy DDH Bot**으로 바꿨습니다.
DDH 서버 ID를 확인하기 전에 기존 DDS 봇을 자동으로 교체하지 않기 위한 전환 절차입니다.

1. 이 변경을 검토하고 병합합니다.
2. 봇을 DDH에 초대하고 역할 순서·권한을 설정합니다.
3. GitHub Actions → **Deploy DDH Bot** → Run workflow → `ddh_guild_id`에 새 서버 ID를 입력합니다.
4. 선택한 커밋의 테스트가 통과하면 기존 VPS에서 해당 커밋을 받아 검증합니다.
5. Discord 로그인·대상 서버 권한 확인이 통과한 뒤 기존 `nohup` 실행을 종료하고 systemd 서비스로 전환합니다.
6. Discord에서 `/ddh setup apply:True`를 실행합니다.

VPS에는 Python 3.12+, `python3-venv`, Git, systemd와 `/root/Dialian` 접근 권한이 필요합니다.
로컬 소스 변경이 있으면 덮어쓰지 않고 배포를 중단합니다. 별도로 보관/커밋한 후 다시 실행하세요.
`venv-ddh/`에 전용 환경을 만들고 `.env.ddh`(권한 600)에 서비스 환경을 저장합니다.
봇은 서버 재부팅·비정상 종료 시 systemd가 재시작합니다. **동일 봇을 두 프로세스로 실행하지 마세요.**

```bash
systemctl status dialian.service
journalctl -u dialian.service -n 100 --no-pager
systemctl restart dialian.service
```

배포 검증은 대상 서버 로그인과 Gateway 연결까지 확인합니다. 서버 구성·티켓 실제 조작 확인은 별도입니다.
배포 실패 시 Actions 로그와 systemd 로그를 확인하세요. 소스 checkout은 바뀌었을 수 있으므로,
구버전으로 되돌릴 때는 서비스를 중지하고 원하는 이전 커밋으로 checkout한 뒤 기존 가상환경/설정으로 실행해야 합니다.

## 데이터와 백업

프로세스 시작 시와 이후 매일 UTC 날짜가 바뀐 후 한 번 `data/ddh-backups/`에 SQLite 백업을 만듭니다.
백업의 무결성과 필수 테이블을 확인한 뒤 14일 초과 백업만 정리합니다. 기존 DDS 백업에는 손대지 않습니다.
DB·백업·환경 파일·로그는 Git에 포함하지 않습니다. 복구 시 먼저 서비스를 중지하고
검증된 백업을 `data/ddh.db`에 복사하며 이전 `ddh.db-wal`, `ddh.db-shm`은 함께 별도 보관하세요.

## 검증

```bash
python -m unittest discover -s tests -v
python -m compileall -q config.py dial.py ddh tests
```

테스트는 임시 DB와 Discord REST 모의 경계를 사용합니다. 권한 검사는 실제 discord.py 권한 계산을 사용합니다.
설정·역할·채널 재구성·권한 격리·심사 권한·중복 제출·부분 실패 복구·공지 중복 방지·백업을 검증합니다.
**Discord 실서버 생성/심사 및 기존 VPS 배포는 토큰·DDH 서버 ID를 연결한 뒤 확인해야 합니다.**

구현 API 기준: [discord.py 2.5.2 API](https://discordpy.readthedocs.io/en/v2.5.2/api.html),
[Interactions API](https://discordpy.readthedocs.io/en/v2.5.2/interactions/api.html).
