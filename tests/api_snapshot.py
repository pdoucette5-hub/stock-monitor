"""Record and compare API responses, as a safety net while refactoring.

Usage (from the repository root):

    python tests/api_snapshot.py record snapshots/before.json
    python tests/api_snapshot.py record snapshots/after.json
    python tests/api_snapshot.py compare snapshots/before.json snapshots/after.json

How it stays safe and repeatable:

- The app runs against a throwaway copy of backend/, config/ and cache/ in a
  temporary folder, so nothing in your real cache is modified.
- Google sign-in, Google Cloud Storage and Google Sheets are switched off, and
  all outbound network connections are blocked. Responses come only from the
  local cache, so two runs on the same code should match exactly.
- Every GET endpoint is called as a full-access user and again as a
  view-only (limited) user, so the redaction logic is covered too.

Only GET endpoints are called; nothing is written.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Ticker-specific endpoints are exercised for these tickers when present.
SAMPLE_TICKER_COUNT = 3

# Fields whose value is "the current time" and so differ on every run.
VOLATILE_KEYS = {"snapshot_timestamp"}

LIMITED_TEST_USER = {
    "email": "snapshot-viewer@example.com",
    "name": "Snapshot Viewer",
    "picture": "",
    "role": "limited",
}


def _isolate_environment() -> None:
    for key in (
        "GOOGLE_AUTH_ENABLED",
        "PRICE_HISTORY_GCS_BUCKET",
        "STOCK_MONITOR_STATE_GCS_BUCKET",
        "SHEETS_TICKER_PUSH_URL",
        "SHEETS_TICKER_PUSH_SECRET",
        "FINNHUB_API_KEY",
        "INTRADAY_FINNHUB_API_KEY",
        "PRICE_IMPORT_SECRET",
    ):
        os.environ[key] = ""
    # Stop python-dotenv from loading the real .env over these values.
    os.environ["PYTHONDOTENV_DISABLED"] = "1"

    def _blocked(*_args, **_kwargs):
        raise OSError("Network access is disabled during API snapshots")

    socket.socket.connect = _blocked  # type: ignore[assignment]
    socket.create_connection = _blocked  # type: ignore[assignment]


def _copy_app(workdir: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "static", "*.pyc")
    for name in ("backend", "config", "cache"):
        src = REPO_ROOT / name
        if src.exists():
            shutil.copytree(src, workdir / name, ignore=ignore)


def _mask_volatile(value):
    if isinstance(value, dict):
        return {
            key: "<volatile>" if key in VOLATILE_KEYS else _mask_volatile(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask_volatile(item) for item in value]
    return value


def _endpoints(tickers: list[str]) -> list[str]:
    paths = [
        "/api/health",
        "/api/auth/config",
        "/api/auth/me",
        "/api/tickers",
        "/api/tickers/shared",
        "/api/events",
        "/api/forecasts",
        "/api/fundamentals/reported",
        "/api/earnings-calendar",
        "/api/performance/portfolio",
        "/api/performance/portfolio?range=1y&mode=managed",
        "/api/compensation",
        "/api/prices/status",
        "/api/accounts",
        "/api/management",
        "/api/portfolio/view",
        "/api/portfolio",
        "/api/config/tickers",
        "/api/settings",
    ]
    for ticker in tickers:
        paths += [
            f"/api/events?ticker={ticker}",
            f"/api/transactions/{ticker}",
            f"/api/position/{ticker}",
            f"/api/prices/history?ticker={ticker}",
            f"/api/stock/{ticker}",
        ]
    if len(tickers) >= 2:
        paths.append(f"/api/prices/compare?tickers={','.join(tickers[:2])}")
    return paths


def record(output: Path) -> None:
    _isolate_environment()
    workdir = Path(tempfile.mkdtemp(prefix="stock-monitor-snapshot-"))
    try:
        _copy_app(workdir)
        os.chdir(workdir)
        sys.path.insert(0, str(workdir))

        from fastapi.testclient import TestClient
        from backend.main import app

        role_holder: dict[str, dict | None] = {"user": None}

        @app.middleware("http")
        async def _simulate_user(request, call_next):  # noqa: ANN001
            if role_holder["user"] is not None:
                request.state.user = dict(role_holder["user"])
            return await call_next(request)

        results: dict[str, dict] = {}
        with TestClient(app) as client:
            config = client.get("/api/config/tickers").json()
            tickers = [
                str(item.get("ticker", "")).upper()
                for item in (config.get("portfolio") or [])
                if item.get("ticker")
            ][:SAMPLE_TICKER_COUNT]

            for role, user in (("full", None), ("limited", LIMITED_TEST_USER)):
                role_holder["user"] = user
                for path in _endpoints(tickers):
                    response = client.get(path)
                    try:
                        body = response.json()
                    except ValueError:
                        body = response.text
                    results[f"{role} GET {path}"] = {
                        "status": response.status_code,
                        "body": _mask_volatile(body),
                    }

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(results, indent=1, sort_keys=True, default=str))
        print(f"Recorded {len(results)} responses to {output}")
    finally:
        os.chdir(REPO_ROOT)
        shutil.rmtree(workdir, ignore_errors=True)


def _diff(a, b, path: str, out: list[str], limit: int = 20) -> None:
    if len(out) >= limit:
        return
    if type(a) is not type(b):
        out.append(f"{path}: {a!r:.80} -> {b!r:.80}")
    elif isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                out.append(f"{path}.{key}: added")
            elif key not in b:
                out.append(f"{path}.{key}: removed")
            else:
                _diff(a[key], b[key], f"{path}.{key}", out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: list length {len(a)} -> {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"{path}: {a!r:.80} -> {b!r:.80}")


def compare(before_file: Path, after_file: Path) -> int:
    before = json.loads(before_file.read_text())
    after = json.loads(after_file.read_text())
    changed = 0
    for key in sorted(set(before) | set(after)):
        if key not in after:
            print(f"MISSING  {key}")
            changed += 1
        elif key not in before:
            print(f"NEW      {key}")
            changed += 1
        elif before[key] != after[key]:
            changed += 1
            details: list[str] = []
            _diff(before[key], after[key], "", details)
            print(f"CHANGED  {key}")
            for line in details:
                print(f"           {line}")
    if changed:
        print(f"\n{changed} of {len(before)} responses differ.")
        return 1
    print(f"All {len(before)} responses match.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "record":
        record(Path(sys.argv[2]).resolve())
    elif len(sys.argv) == 4 and sys.argv[1] == "compare":
        sys.exit(compare(Path(sys.argv[2]), Path(sys.argv[3])))
    else:
        print(__doc__)
        sys.exit(2)
