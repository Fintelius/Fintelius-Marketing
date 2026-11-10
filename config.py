"""Settings. Secrets are read from the private .env file on the server, never from the code."""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent


def _load_env():
    # .env sits next to the code, or one folder above it (server layout: ~/marketing/.env)
    for p in (BASE / ".env", BASE.parent / ".env"):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
            return str(p)
    return None


ENV_FILE = _load_env()


class Settings:
    anthropic_api_key = property(lambda s: os.environ.get("ANTHROPIC_API_KEY", ""))
    tenant_id = property(lambda s: os.environ.get("GRAPH_TENANT_ID", ""))
    client_id = property(lambda s: os.environ.get("GRAPH_CLIENT_ID", ""))
    client_secret = property(lambda s: os.environ.get("GRAPH_CLIENT_SECRET", ""))
    mailbox = property(lambda s: os.environ.get("MAILBOX", ""))
    digest_to = property(lambda s: os.environ.get("DIGEST_TO", "swapnil@fintelius.com"))
    # review = drafts wait for approval; auto = drafts that pass every guard rule are sent
    send_mode = property(lambda s: os.environ.get("SEND_MODE", "review").lower())
    model = property(lambda s: os.environ.get("MODEL", "claude-sonnet-5-5"))
    tz = property(lambda s: os.environ.get("TZ_NAME", "Asia/Kolkata"))

    @property
    def data_dir(self):
        d = os.environ.get("DATA_DIR")
        return Path(d) if d else BASE.parent / "data"

    @property
    def db_path(self):
        return self.data_dir / "marketing.db"

    @property
    def halt_file(self):
        return self.data_dir / "HALT"


settings = Settings()
