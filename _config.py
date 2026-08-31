import os
from pathlib import Path


def load_env(prefix: str, defaults: dict | None = None) -> dict:
    env_path = Path(__file__).parent / ".env"
    values = dict(defaults or {})
    if env_path.is_file():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key.startswith(prefix):
                    values[key] = val
    resolved = {}
    for key, default in (defaults or {}).items():
        resolved[key] = os.environ.get(key, values.get(key, default))
    return resolved
