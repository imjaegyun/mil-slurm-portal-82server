# Machine Intelligence Lab Compute Portal — 82server

Machine Intelligence Lab의 82server Slurm GPU 자원을 웹에서 확인하고,
노드별 자원 요청과 포털에서 생성한 Job 취소를 처리하는 개인 계정용 포털입니다.

## 주요 기능

- 파티션과 노드별 GPU, CPU, 전체/남은 메모리 확인
- 노드 상세 화면에서 GPU 번호와 사용자별 점유 현황 확인
- 같은 파티션·GPU 종류의 노드를 여러 대 선택해 노드당 GPU, CPU, 메모리와 실행 시간을 요청
- 현재 여유 모드는 남은 GPU·CPU·메모리까지만 허용하고, 대기 요청은 노드 수용량 안에서 허용
- 실행 시간 제한 없음(`No limit`) 지원
- 포털에서 생성한 현재 사용자 소유 Job 취소
- `mil-jobs` 터미널 화면에서 사용 중인 GPU 노드, 사용자와 Job 확인
- 시스템 설정에 따른 라이트/다크 모드 자동 전환

## 처음 설치

각 사용자가 자신의 82server 계정으로 접속한 뒤 원하는 디렉터리에
저장소를 복제합니다. 82server에서는 GitHub SSH 포트가 차단될 수 있으므로
HTTPS 주소를 사용합니다.

```bash
ssh 82server

INSTALL_DIR="$HOME/apps/mil-slurm-portal-82server"
git clone \
  https://github.com/imjaegyun/mil-slurm-portal-82server.git \
  "$INSTALL_DIR"

cd "$INSTALL_DIR"
./scripts/setup.sh
```

`setup.sh`는 설치 디렉터리를 자동으로 인식하고 다음 작업을 처리합니다.

- 사용자 UID를 기준으로 충돌하지 않는 개인용 서버 포트 선택
- 개인 `.env` 생성
- `~/.local/bin/mil-jobs` 연결
- 정확한 실행·접속 명령 안내

이미 설치했다면 다시 복제하지 않고 기존 디렉터리에서 실행하면 됩니다.

## 설정

기본 설정은 82server의 실제 Slurm 구성에 맞춰져 있습니다.

```text
SLURM_BIN=/TGM/SLURM/bin
PORTAL_SERVER_NAME=82server
PORTAL_CLUSTER_NAME=tgmv2
PORTAL_ALLOWED_PARTITIONS=g1,g2,g3
PORTAL_MAX_REQUEST_NODES=32
PORTAL_PORT=<사용자별 자동 선택>
```

각 사용자의 서버 포트와 접근 토큰은 서로 다릅니다. 기존 `.env`가 있으면
`setup.sh`가 덮어쓰지 않습니다. 필요한 경우 Git에 포함되지 않는 `.env`
파일만 수정합니다.

## 다중 노드 요청

- 첫 노드를 선택하면 같은 파티션과 같은 GPU 종류의 노드만 추가로 선택할 수 있습니다.
- GPU, CPU, 메모리는 모두 노드당 값으로 입력하며 화면에서 전체 요청량을 계산합니다.
- 현재 여유 모드는 선택한 모든 노드에서 즉시 확보할 수 있는 공통 범위만 허용합니다.
- 대기 요청은 각 노드의 전체 수용량 안에서 허용되며 모든 노드의 자원이 함께 확보될 때 시작됩니다.
- 서버는 제출 직전에 선택 노드와 자원을 다시 확인합니다.

## 실행

```bash
cd "$INSTALL_DIR"
./scripts/start.sh
./scripts/status.sh
./scripts/token.sh
```

`token.sh`가 출력한 접근 토큰은 브라우저 로그인 화면에 입력합니다.
토큰은 서버의 `.state/access-token`에만 저장되며 Git에는 올라가지 않습니다.

## 터미널 GPU 현황

`setup.sh`가 `~/.local/bin/mil-jobs`를 자동으로 연결하므로 어느
디렉터리에서든 실행할 수 있습니다.

```bash
mil-jobs                 # 현재 GPU를 사용하는 노드
mil-jobs --watch         # 5초마다 자동 갱신
mil-jobs --all           # 유휴 GPU 노드까지 모두 표시
mil-jobs --user "$USER"  # 내 Job만 표시
mil-jobs --partition g2  # 특정 파티션만 표시
```

노드별 GPU 사용량과 인덱스, CPU, 남은 메모리, Job ID, 사용자,
GPU 요청 수, 경과·남은 시간을 조회합니다. 자원을 변경하거나 Job을
제출·취소하지 않는 읽기 전용 명령입니다.

## 접속

서버에서 아래 명령을 실행하면 현재 사용자의 실제 서버 포트에 맞는
SSH 터널 명령이 출력됩니다.

```bash
cd "$INSTALL_DIR"
./scripts/access.sh 82server
```

`82server`는 자신의 SSH 설정에 등록한 호스트 별칭입니다. 별칭이 없다면
`사용자명@서버주소`를 전달합니다.

```bash
./scripts/access.sh 사용자명@서버주소
```

출력된 SSH 명령을 자신의 컴퓨터 새 터미널에서 실행한 채로 유지합니다.
이 명령은 macOS/Linux의 Terminal과 Windows의 PowerShell 또는 명령
프롬프트에서 동일하게 사용할 수 있습니다.

```text
ssh -N -L 127.0.0.1:18765:127.0.0.1:<개인 서버 포트> 82server
```

SSH 호스트 설정 파일의 기본 위치는 다음과 같습니다.

- macOS/Linux: `~/.ssh/config`
- Windows: `%USERPROFILE%\.ssh\config`

Windows에서 `ssh` 명령을 찾을 수 없다는 메시지가 나오면 Windows
OpenSSH Client를 먼저 활성화해야 합니다.

모든 사용자는 자신의 컴퓨터에서 <http://127.0.0.1:18765>를 열지만,
SSH 터널의 원격 포트는 각자의 개인 포털로 연결됩니다.

자신의 컴퓨터에서 `18765` 포트를 이미 사용 중이라면 두 번째 인자로
다른 로컬 포트를 지정합니다.

```bash
./scripts/access.sh 82server 18766
```

출력된 명령을 실행한 뒤 <http://127.0.0.1:18766>으로 접속하면 됩니다.

다른 사람의 토큰을 공유하거나 다른 사람의 포털을 함께 사용하지 마세요.
Job 제출과 취소는 포털 프로세스를 실행한 Unix 계정 권한으로 처리됩니다.

## 업데이트

```bash
cd "$INSTALL_DIR"
git pull --ff-only origin main
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

- 웹 서버는 외부 인터페이스가 아닌 사용자별 `127.0.0.1:<개인 포트>`에만
  바인딩됩니다.
- API는 `.state/access-token`의 랜덤 토큰을 요구합니다.
- 각 사용자는 자신의 Unix 계정에서 별도 포털·포트·토큰을 사용합니다.
- Slurm 명령은 셸 문자열이 아닌 인자 배열로 실행됩니다.
- Job 취소는 포털이 만든 현재 사용자 소유 Job에만 허용됩니다.
- `.env`, 접근 토큰, PID, 로그는 `.gitignore`로 제외됩니다.
- 현재 버전은 랩 전체 공용 인증 서비스가 아니라 사용자별 테스트 배포용입니다.
