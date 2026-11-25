# 초대 집계 확인

봇이 `active (running)`인 것만으로 집계 성공을 확인할 수 없습니다.
순위표의 `집계 확인` 시각은 점수가 그대로여도 약 5분 간격으로 갱신됩니다.
Discord 조회 실패 시 이 시각도 오래된 상태로 남습니다.

서버의 봇 저장소에서 실제 서비스가 쓰는 Python으로 실행하세요.

```bash
cd /root/Dialian
venv/bin/python -m ddh.invite_status
journalctl -u dialian.service --since '30 minutes ago' --no-pager -o cat
```

가상환경이 `venv-ddh`이면 실행 경로를 바꾸세요. 진단은 `.env` 및 환경 변수의
`DDH_DATABASE`와 `DDH_GUILD_ID`를 읽습니다. 서비스가 별도 EnvironmentFile을
사용한다면 `--database /실제/경로/ddh.db --guild 서버ID`를 지정하세요.
토큰이나 `.env` 내용은 공유하지 않아도 됩니다. 진단은 DB를 읽기 전용으로 열며,
초대 코드 및 개별 회원 목록은 출력하지 않습니다.

- `not_initialized`: 선택한 DB에 이벤트가 없습니다. DB 경로/설정/배포를 확인하세요.
- `legacy_no_diagnostics`: 수정 전 상태입니다. 새 코드 실행 후 첫 조회까지 기다리세요.
- `retrying`: 가입·초대 API 수치 불일치로 최대 120초간 재확인 중입니다.
- `awarded_total`: 실제 저장된 총 점수입니다. 순위표와 비교하세요.
- `unresolved_since_fix`: 수정 적용 후 초대자를 확정하지 못한 신규 가입 수입니다.
- `last_checked_utc`: 성공적으로 관측한 시각입니다. 서비스 로그와 함께 확인하세요.

재확인 동안 기준 사용 횟수와 가입 관측은 DB에 유지되어 재시작 후에도 이어집니다.
확정할 수 없는 구간은 임의로 점수를 주지 않고 최근 50개 관측 근거를 저장합니다.
이미 기존 코드가 소비해 버린 기록은 자동 복구하지 않습니다. 개인 링크 발급 자체는
점수가 아니며, 공용 링크를 공유하면 공유자가 아닌 링크 생성자가 집계될 수 있습니다.
기존 이벤트 기간·보상·확정 점수·역할·채널 권한은 변경하지 않습니다.
신고자 한 명의 내역은 `venv/bin/python -m ddh.invite_status --inviter 회원ID`로 확인합니다.
`confirmed`는 이벤트 확정 점수, `lifetime_uses`는 마지막 조회에 남은 해당 계정 링크들의
누적 사용 횟수입니다. 이벤트 전 사용이나 재가입이 포함될 수 있어 차이를 그대로 가산하지 마세요.
배포 후 v1.5.2 공지는 업데이트 채널에 한 번 게시됩니다.
