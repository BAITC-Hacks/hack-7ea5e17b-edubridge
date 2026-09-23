"""Launch the dashboard with its portable theme and local-only defaults."""
import subprocess
import sys
from pathlib import Path


if __name__ == "__main__":
    app = Path(__file__).resolve().with_name("app.py")
    command = [
        sys.executable, "-m", "streamlit", "run", str(app),
        "--server.address=127.0.0.1", "--server.headless=true",
        "--browser.gatherUsageStats=false", "--theme.base=light",
        "--theme.primaryColor=#137C68", "--theme.backgroundColor=#F5F7F8",
        "--theme.secondaryBackgroundColor=#FFFFFF", "--theme.textColor=#182F38",
        *sys.argv[1:],
    ]
    try:
        raise SystemExit(subprocess.call(command))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
