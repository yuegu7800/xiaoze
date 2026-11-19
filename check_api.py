"""Check saved DeepSeek credentials without printing secrets or API bodies."""
import base64
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

import requests
import win32crypt
from core import prepare_api_config


def main():
    path = Path(__file__).resolve().parent / "data" / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    stored = cfg["api_key"]
    if not stored.startswith("dpapi:"):
        raise RuntimeError("Expected encrypted local credentials.")
    try:
        key = win32crypt.CryptUnprotectData(base64.b64decode(stored[6:]), None, None, None, 0)[1].decode("utf-8")
    except Exception:
        print(json.dumps({"decryption_ok": False}))
        return
    host = urlparse(cfg["endpoint"]).hostname
    summary = {
        "decryption_ok": True, "host": host, "model": cfg["model"],
        "key_present": bool(key), "key_starts_sk": key.startswith("sk-"),
        "key_length": len(key), "key_has_whitespace": any(c.isspace() for c in key),
        "key_has_bearer_prefix": key.lower().startswith("bearer "),
        "key_has_quotes": key.startswith(('\"', "'", "“")),
        "key_is_ascii": key.isascii(),
        "key_is_masked_placeholder": bool(key) and set(key) <= set("*•●·"),
        "key_looks_like_uuid": bool(re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", key)),
        "sk_token_count": len(re.findall(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{16,}", key)),
        "ascii_token_lengths": [len(token) for token in re.findall(r"[A-Za-z0-9_-]{16,}", key)],
        "non_ascii_count": sum(not char.isascii() for char in key),
    }
    if "--format-only" in sys.argv:
        print(json.dumps(summary, ensure_ascii=True))
        return
    if host != "api.deepseek.com":
        print(json.dumps({**summary, "network_check": "skipped_non_official_host"}))
        return
    try:
        prepare_api_config(dict(cfg, api_key=key))
    except RuntimeError as error:
        print(json.dumps({**summary, "local_validation_error": str(error), "request_sent": False}, ensure_ascii=True))
        return
    try:
        response = requests.get("https://api.deepseek.com/models",
            headers={"Authorization": "Bearer " + key}, timeout=(10, 25))
    except requests.RequestException as error:
        print(json.dumps({**summary, "network_error_type": type(error).__name__}))
        raise SystemExit(2)
    summary["http_status"] = response.status_code
    if response.status_code == 200:
        try:
            models = [m["id"] for m in response.json().get("data", []) if isinstance(m.get("id"), str)]
            summary.update(models=models, configured_model_available=cfg["model"] in models)
        except (ValueError, KeyError, TypeError):
            summary["model_list_valid"] = False
    print(json.dumps(summary, ensure_ascii=True))


if __name__ == "__main__":
    main()
