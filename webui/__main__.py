"""python -m webui: serve the UI on 127.0.0.1 (W-8), login token in compose/generated/secrets/ui_token."""
import os
import secrets
import sys
from pathlib import Path

import uvicorn

from .app import create_app

TOKEN_FILE = Path("compose/generated/secrets/ui_token")


def load_token(path: Path | None = None) -> str:
    """The login token, created 0600 on first use and kept so bookmarks keep working."""
    path = path or TOKEN_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(24))
    return path.read_text().strip()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8088
    token = load_token()
    print(f"opti-sim UI: http://127.0.0.1:{port}/login?token={token}", flush=True)
    uvicorn.run(create_app(token), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
