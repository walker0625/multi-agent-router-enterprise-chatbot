"""로컬에서 전체를 한 번에 띄운다 — 라우터(8000)·수행(8001)·영업(8002) + 웹(5173).

    uv run python scripts/dev.py            # API 키 없이 fake LLM 으로
    uv run python scripts/dev.py --fresh    # 대화 저장소를 비우고 시작
    # 실제 모델로: LLM_PROVIDER=openai LLM_API_KEY=... LLM_CHAT_MODEL=... LLM_EMBEDDING_MODEL=...

개발용 서명 키가 없으면 `.devkeys/` 에 만든다. 세 서비스가 같은 키 파일을 보도록 절대 경로로 넘긴다.
Ctrl+C(또는 SIGTERM)를 받으면 띄운 프로세스를 모두 정리한다.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from chatcore.auth import generate_keypair

ROOT = Path(__file__).resolve().parents[1]
KEYS = ROOT / ".devkeys"
RUN = ROOT / ".run"

SERVICES = [
    ("delivery", "delivery_agent.main:app", 8001),
    ("sales", "sales_agent.main:app", 8002),
    ("router", "router_service.main:app", 8000),
]


def ensure_keys() -> tuple[Path, Path]:
    private, public = KEYS / "jwt_private.pem", KEYS / "jwt_public.pem"
    if not (private.exists() and public.exists()):
        KEYS.mkdir(exist_ok=True)
        priv, pub = generate_keypair()
        private.write_bytes(priv)
        private.chmod(0o600)
        public.write_bytes(pub)
        print(f"개발용 서명 키를 만들었습니다: {KEYS}")
    return private, public


def pipe(name: str, proc: subprocess.Popen[str]) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(f"[{name:8}] {line}")


def wait_live(port: int, timeout_s: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=1) as resp:
                if resp.status == 200:
                    return True
        except OSError:
            time.sleep(0.3)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--fresh", action="store_true", help="대화 저장소(.run/router.db)를 지우고 시작")
    parser.add_argument("--no-web", action="store_true", help="웹 개발 서버는 띄우지 않는다")
    args = parser.parse_args()

    private, public = ensure_keys()
    RUN.mkdir(exist_ok=True)
    db = RUN / "router.db"
    if args.fresh:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(f"{db}{suffix}").unlink(missing_ok=True)

    env = {
        **os.environ,
        "APP_ENV": os.environ.get("APP_ENV", "dev"),
        "AUTH_PUBLIC_KEY_PATH": str(public),
        "AUTH_PRIVATE_KEY_PATH": str(private),
        "ROUTER_DB_PATH": str(db),
        # fake 모드에서도 답변이 흘러나오는 모습이 보이도록 조각마다 약간 쉰다
        "LLM_FAKE_CHUNK_DELAY_S": os.environ.get("LLM_FAKE_CHUNK_DELAY_S", "0.02"),
    }
    env.pop("VIRTUAL_ENV", None)

    procs: list[tuple[str, subprocess.Popen[str]]] = []

    def start(name: str, cmd: list[str], cwd: Path) -> None:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        procs.append((name, proc))
        threading.Thread(target=pipe, args=(name, proc), daemon=True).start()

    def stop(*_signal: object) -> None:
        for _, proc in reversed(procs):
            if proc.poll() is None:
                proc.terminate()
        for _, proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    for name, app, port in SERVICES:
        start(name, ["uv", "run", "uvicorn", app, "--port", str(port), "--no-access-log"], ROOT)
    for name, _, port in SERVICES:
        if not wait_live(port):
            print(f"{name} 서비스가 {port} 포트에서 뜨지 않았습니다. 위 로그를 확인하세요.")
            stop()
    if not args.no_web:
        if not (ROOT / "web/node_modules").exists():
            subprocess.run(["npm", "install", "--no-fund", "--no-audit"], cwd=ROOT / "web", check=True)
        start("web", ["npm", "run", "dev", "--", "--strictPort"], ROOT / "web")

    provider = env.get("LLM_PROVIDER", "fake")
    print(
        f"\n준비됐습니다 (LLM_PROVIDER={provider}). 웹: http://localhost:5173  API: http://localhost:8000\n"
    )
    while all(proc.poll() is None for _, proc in procs):
        time.sleep(0.5)
    dead = [name for name, proc in procs if proc.poll() is not None]
    print(f"프로세스가 종료됐습니다: {', '.join(dead)}")
    stop()
    return 1


if __name__ == "__main__":
    sys.exit(main())
