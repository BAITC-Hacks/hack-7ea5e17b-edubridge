"""Start the real backend and the participant-owned dashboard with one command."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def stop_process(process: subprocess.Popen) -> None:
    """Stop only the service process tree created by this launcher."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def wait_ready(process: subprocess.Popen, url: str, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Service exited during startup: {url}")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.2)
    raise RuntimeError(f"Service did not become ready: {url}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/archive-model.json")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--ui-port", type=int, default=8501)
    args = parser.parse_args(argv)
    processes = []
    try:
        if args.api_port == args.ui_port:
            raise ValueError("API and UI ports must differ")
        for port in (args.api_port, args.ui_port):
            if not 1 <= port <= 65535:
                raise ValueError("Ports must be between 1 and 65535")
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError as error:
                    raise ValueError(f"Port {port} is occupied; stop the existing service or choose another port") from error
        config_path = Path(args.config)
        if not config_path.is_absolute():
            config_path = ROOT / config_path
        config = json.loads(config_path.read_text("utf-8"))
        if config.get("mode") == "archive" and not config.get("model_artifact_path"):
            raise ValueError("Archive launch requires a model package; use config/archive-model.json")
        # Import dependencies before creating child processes for an actionable startup error.
        import streamlit  # noqa: F401
        import uvicorn  # noqa: F401
        if config.get("mode") == "archive":
            import eccodes  # noqa: F401
            import sklearn  # noqa: F401
        env = os.environ.copy()
        api_url = f"http://127.0.0.1:{args.api_port}"
        ui_url = f"http://127.0.0.1:{args.ui_port}"
        env["WIND_API_BASE_URL"] = api_url
        options = {"cwd": ROOT, "env": env}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        backend = subprocess.Popen([
            sys.executable, "-m", "wind_agent", "--config", str(config_path),
            "serve", "--host", "127.0.0.1", "--port", str(args.api_port),
        ], **options)
        processes.append(backend)
        wait_ready(backend, api_url + "/health")
        dashboard = subprocess.Popen([
            sys.executable, "-m", "streamlit", "run", str(ROOT / "ui/app.py"),
            "--server.address=127.0.0.1", "--server.headless=true",
            "--browser.gatherUsageStats=false", "--theme.base=light",
            "--theme.primaryColor=#137C68", "--theme.backgroundColor=#F5F7F8",
            "--theme.secondaryBackgroundColor=#FFFFFF", "--theme.textColor=#182F38",
            "--server.port", str(args.ui_port),
        ], **options)
        processes.append(dashboard)
        wait_ready(dashboard, ui_url + "/_stcore/health")
        print(f"READY: Dashboard {ui_url} | API {api_url}/docs | mode={config.get('mode')}", flush=True)
        print("Select API mode in the dashboard, check connection, then request a forecast. Ctrl+C stops both services.", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        raise RuntimeError("A service stopped; shutting down the other service")
    except KeyboardInterrupt:
        return 0
    except ImportError as error:
        print(f"Missing dependency: {error}. Install: python -m pip install -e '.[dev,weather,model]' -r ui/requirements.txt", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Startup failed: {error}", file=sys.stderr)
        return 1
    finally:
        for process in reversed(processes):
            stop_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
