# 수집 서버 배포

미국 시장 데이터 수집을 24시간 도는 리눅스 호스트로 옮기는 절차다.

## 왜 서버인가

- **미장 정규장이 한국시간 22:30~05:00이다.** 마감 후 수집은 새벽이고, 그 시각에
  PC가 켜져 있기를 기대할 수 없다.
- **후보 풀 백필이 몇 시간이다.** 그동안 PC를 쓸 수 없다.
- **미장에는 KRX 회원 로그인이 없다.** 한국 시장을 접으면서 서버 이전의 가장 큰
  리스크가 이미 사라졌다.

실주문은 이 문서의 범위가 아니다. `broker/kis.py`는 여전히 미구현이고, 두 전략
모두 승격 기준에 미달이다. **여기서 옮기는 것은 수집과 리서치뿐이다.**

## 1. 호스트 준비

작은 리눅스 VM이면 된다. 미국 리전을 권한다 — EDGAR와 가격 소스에 가깝다.

| 항목 | 최소 | 근거 |
|---|---|---|
| vCPU | 2 | 수집은 대부분 네트워크 대기다 |
| RAM | 2GB | pandas가 종목별로 처리한다 |
| 디스크 | 20GB | 300종목 × 11년 일봉이 수백 MB, 여유분 포함 |

```bash
sudo timedatectl set-timezone America/New_York
sudo apt-get update && sudo apt-get install -y git python3 python3-venv tzdata
```

**시간대를 거래소 기준으로 둔다.** cron 시각을 "장 마감 후"로 생각할 수 있어
헷갈림이 줄고, `exchange_calendars`와 어긋나지 않는다.

## 2. 코드와 의존성

```bash
git clone <repo> ~/trading-bot && cd ~/trading-bot
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

`uv sync`가 리눅스에서 도는 것은 `pyproject.toml`의 win32 pin을 제거한 뒤부터다.
그 전 커밋을 체크아웃하면 여기서 막힌다.

## 3. 자격증명

```bash
cp .env.template .env
$EDITOR .env
```

미국만 쓴다면 `SEC_USER_AGENT` 한 줄이면 된다. 이름과 이메일을 적는다 — SEC가
신원 없는 요청을 403으로 막는다.

```
SEC_USER_AGENT=Hong Gildong hong@example.com
```

`.env`는 gitignore 대상이고 CLI가 시작할 때 자동으로 읽는다.

## 4. 사전 점검 — 백필 전에 반드시

```bash
uv run python -m tradingbot data preflight
```

데이터 소스가 이 호스트에서 실제로 닿는지 30초 안에 확인한다. **클라우드 IP는
가정용 IP와 다르게 취급된다** — Yahoo는 데이터센터 주소에 403을 훨씬 잘 낸다.
여기서 막히면 유니버스 규모와 가격 소스를 다시 정해야 하므로, 몇 시간짜리
백필을 시작하기 전에 돌린다.

전부 성공하면 종료코드 0이다. 하나라도 실패하면 1이고, 어느 항목인지 출력한다.

## 5. 백필

```bash
# 티커 → CIK, 후보 풀
uv run python -m tradingbot --config config/us_equity.toml data pipeline --market US

# 확인용 소수 종목부터
uv run python -m tradingbot --config config/us_equity.toml \
  data update --market US --symbols AAPL MSFT NVDA --start 2015-01-01
```

수백 종목 백필은 시간이 걸린다. `tmux`나 `nohup`으로 띄워 세션이 끊겨도
이어지게 한다.

## 6. 정기 실행

```bash
crontab -e   # deploy/crontab.example 참고
```

두 개면 충분하다. 장 마감 후 일일 수집, 주 1회 유니버스·목록 갱신.

## 7. 관측

수집 결과를 폰으로 받으려면 텔레그램 봇이 가장 싸다. 봇 토큰과 chat id를
`.env`에 넣고 cron 뒤에 붙인다.

```bash
curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_TOKEN/sendMessage" \
  -d chat_id="$TELEGRAM_CHAT_ID" --data-urlencode "text=$(tail -5 /var/log/tradingbot/collect.log)"
```

정식 알림 모듈은 아직 없다. 필요해지면 만든다.

## 8. 백업

`data/`와 `state/`가 전부다. 코드는 git에 있다.

```bash
tar czf backup-$(date +%F).tar.gz data state
```

수집은 재실행하면 복구되지만 시간이 걸리고, `state/`의 모의계좌 상태는
재현되지 않는다.

## 알려진 한계

- 가격 소스가 클라우드 IP를 어떻게 대하는지는 4번에서 확인해야 안다. 막히면
  대안은 유료 소스이고, 그것은 별도 결정이다.
- 데이터 소스 이용약관 확인은 사용자 몫이다.
- 실주문 경로는 없다. 이 호스트는 데이터를 모으고 리서치를 돌릴 뿐이다.
