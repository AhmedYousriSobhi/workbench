"""python -m webui: serve the UI on 127.0.0.1 (W-8), login token in compose/generated/secrets/ui_token."""
import os
import secrets
import sys
from pathlib import Path

import uvicorn

from benchctl.workbench import live_sim_path

from .app import create_app

TOKEN_FILE = Path("compose/generated/secrets/ui_token")
MIN_TOKEN = 16  # an empty token would match a request with no cookie (#986): a shorter one is replaced


def load_token(path: Path | None = None) -> str:
    """The login token, created 0600 on first use and kept so bookmarks keep working. A missing, empty or short file (a full
    disk during the first write, a truncated file) gets a new token, written to a temporary file and renamed into place."""
    path = path or TOKEN_FILE
    if path.exists() and len(token := path.read_text().strip()) >= MIN_TOKEN:
        return token
    path.parent.mkdir(parents=True, exist_ok=True)
    token, tmp = secrets.token_urlsafe(24), path.with_name(path.name + ".new")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.replace(tmp, path)
    return token


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8088
    token = load_token()
    url = f"http://127.0.0.1:{port}/login"
    # No secret is printed anywhere: a link with the token would land in logs and browser history (SEC2).
    print(f"workbench UI: {url}", flush=True)
    if sys.stdout.isatty():
        print("sign in: ./scripts/benchctl open  (prints a one-time link)", flush=True)
    uvicorn.run(create_app(token, sim_yaml=live_sim_path()), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
