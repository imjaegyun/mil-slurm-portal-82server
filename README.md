# Machine Intelligence Lab Compute Portal — 82server

Machine Intelligence Lab의 82server Slurm GPU 자원을 웹에서 확인하고,
노드별 자원 요청과 포털에서 생성한 Job 취소를 처리하는 개인 계정용 포털입니다.

## 주요 기능

- 파티션과 노드별 GPU, CPU, 전체/남은 메모리 확인
- 노드 상세 화면에서 GPU 번호와 사용자별 점유 현황 확인
- 사용할 노드를 직접 선택해 GPU, CPU, 메모리와 실행 시간을 요청
- 현재 여유 모드는 남은 GPU·CPU·메모리까지만 허용하고, 대기 요청은 노드 수용량 안에서 허용
- 실행 시간 제한 없음(`No limit`) 지원
- 포털에서 생성한 현재 사용자 소유 Job 취소
- 시스템 설정에 따른 라이트/다크 모드 자동 전환

## 처음 설치

82server에 접속한 뒤 저장소를 복제합니다.

```bash
ssh -F ~/.ssh/config 82server
git clone git@github.com:imjaegyun/mil-slurm-portal-82server.git ~/slurm-portal
cd ~/slurm-portal
cp .env.example .env
```

이미 `~/slurm-portal` 디렉터리가 있다면 다시 복제하지 않고 아래의
`실행` 절차부터 진행하면 됩니다.

## 설정

기본 설정은 82server의 실제 Slurm 구성에 맞춰져 있습니다.

```text
SLURM_BIN=/TGM/SLURM/bin
PORTAL_SERVER_NAME=82server
PORTAL_CLUSTER_NAME=tgmv2
PORTAL_ALLOWED_PARTITIONS=g1,g2,g3
PORTAL_PORT=18765
```

필요한 경우 Git에 포함되지 않는 `.env` 파일만 수정합니다.

## 실행

```bash
cd ~/slurm-portal
./scripts/start.sh
./scripts/status.sh
./scripts/token.sh
```

`token.sh`가 출력한 접근 토큰은 브라우저 로그인 화면에 입력합니다.
토큰은 서버의 `.state/access-token`에만 저장되며 Git에는 올라가지 않습니다.

## 접속

새 터미널에서 SSH 터널을 실행한 채로 유지합니다.

```bash
ssh -F ~/.ssh/config -N \
  -L 127.0.0.1:18765:127.0.0.1:18765 \
  82server
```

브라우저에서 <http://127.0.0.1:18765>를 열고 서버에서 확인한 토큰을
입력합니다.

## 업데이트

```bash
cd ~/slurm-portal
git pull --ff-only
./scripts/stop.sh
./scripts/start.sh
```

## 상태 확인과 중지

```bash
./scripts/status.sh
./scripts/stop.sh
```

문제가 생기면 아래 로그를 확인합니다.

```bash
tail -n 100 .state/server.log
```

## 테스트

```bash
python3 -m unittest discover -s tests -v
```

## 보안 범위

- 웹 서버는 외부 인터페이스가 아닌 `127.0.0.1:18765`에만 바인딩됩니다.
- API는 `.state/access-token`의 랜덤 토큰을 요구합니다.
- Slurm 명령은 셸 문자열이 아닌 인자 배열로 실행됩니다.
- Job 취소는 포털이 만든 현재 사용자 소유 Job에만 허용됩니다.
- `.env`, 접근 토큰, PID, 로그는 `.gitignore`로 제외됩니다.
- 현재 버전은 랩 전체 공용 인증 서비스가 아니라 사용자별 테스트 배포용입니다.
