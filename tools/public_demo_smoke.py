"""Safe public offline HTTP smoke: synthetic source, real HTTP/Graph, fake model."""

from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def _call(base: str, question: str | None, key: str | None = None) -> tuple[int, dict[str, object]]:
    headers: dict[str, str] = {}
    data = None
    path = "/health/ready"
    if question is not None:
        path = "/api/research"
        data = json.dumps({"question": question, "request_id": "public-demo-smoke"}).encode()
        headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Authorization"] = f"Bearer {key}"
    request = Request(base + path, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def main() -> None:
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        port = int(handle.getsockname()[1])
    key = secrets.token_urlsafe(32)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GROQ_", "P2_LLM_"))}
    env.update(
        P2_API_KEY=key,
        P2_API_CORS_ORIGINS="http://127.0.0.1:5173",
        P2_API_MIN_INTERVAL_SECONDS="0",
        PYTHONPATH=str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", ""),
    )
    proc = subprocess.Popen(
        [sys.executable, "tools/public_demo_api.py", "--demo-only", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError("PUBLIC_DEMO_SERVER_EXITED")
            try:
                if _call(base, None)[0] == 200:
                    break
            except (TimeoutError, URLError):
                time.sleep(0.1)
        else:
            raise RuntimeError("PUBLIC_DEMO_SERVER_NOT_READY")
        assert _call(base, "What checks gate a fictional Orion release?")[0] == 401
        code, answer = _call(base, "What checks gate a fictional Orion release?", key)
        assert code == 200 and answer["status"] == "ANSWERED"
        assert len(answer["citations"]) >= 1 and "[E1]" in str(answer["answer"])
        code, refusal = _call(base, "How much revenue did Orion earn yesterday?", key)
        assert code == 200 and refusal["status"] == "INSUFFICIENT_EVIDENCE"
        assert refusal["citations"] == []
        assert answer["claim_support"] == refusal["claim_support"] == "NOT_VERIFIED"
        print(
            "PASS: synthetic Orion -> BM25 -> LangGraph -> FastAPI HTTP; citation/refusal/auth; zero provider requests"
        )
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


if __name__ == "__main__":
    main()
