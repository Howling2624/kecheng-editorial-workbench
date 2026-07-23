import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AppConfig:
    deepseek_api_key: str
    deepseek_api_url: str
    deepseek_model: str
    work_dir: Path
    ojs_base_url: str
    request_timeout: int
    download_timeout: int
    user_agent: str
    host: str
    port: int
    debug: bool


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_path() -> Path:
    configured_path = Path(os.environ.get("ETHICS_CONFIG", "config.json"))
    if configured_path.is_absolute():
        return configured_path
    return app_base_dir() / configured_path


def _load_json_config() -> Dict[str, Any]:
    config_path = globals()["config_path"]()
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _get(config: Dict[str, Any], key: str, env_name: str, default: Any = None) -> Any:
    return os.environ.get(env_name, config.get(key, default))


def _load_api_key_file() -> str:
    configured_path = os.environ.get("DEEPSEEK_API_KEY_FILE")
    if not configured_path:
        return ""
    api_key_path = Path(configured_path)
    if not api_key_path.is_absolute():
        api_key_path = app_base_dir() / api_key_path
    if not api_key_path.exists():
        return ""
    return api_key_path.read_text(encoding="utf-8").strip()


def save_api_settings(api_key: str, api_url: str = "", model: str = "") -> Path:
    path = config_path()
    config = _load_json_config()
    config["deepseek_api_key"] = api_key.strip()
    if api_url.strip():
        config["deepseek_api_url"] = api_url.strip()
    if model.strip():
        config["deepseek_model"] = model.strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
    return path


def load_config() -> AppConfig:
    config = _load_json_config()
    work_dir = Path(_get(config, "work_dir", "ETHICS_WORK_DIR", "work_temp"))
    if not work_dir.is_absolute():
        work_dir = app_base_dir() / work_dir
    deepseek_api_key = str(
        _get(config, "deepseek_api_key", "DEEPSEEK_API_KEY", "") or _load_api_key_file()
    )

    return AppConfig(
        deepseek_api_key=deepseek_api_key,
        deepseek_api_url=str(
            _get(
                config,
                "deepseek_api_url",
                "DEEPSEEK_API_URL",
                "https://api.deepseek.com/v1/chat/completions",
            )
        ),
        deepseek_model=str(_get(config, "deepseek_model", "DEEPSEEK_MODEL", "deepseek-chat")),
        work_dir=work_dir,
        ojs_base_url=str(
            _get(config, "ojs_base_url", "OJS_BASE_URL", "")
        ).rstrip("/"),
        request_timeout=_int(_get(config, "request_timeout", "REQUEST_TIMEOUT", 15), 15),
        download_timeout=_int(_get(config, "download_timeout", "DOWNLOAD_TIMEOUT", 30), 30),
        user_agent=str(_get(config, "user_agent", "USER_AGENT", "Mozilla/5.0")),
        host=str(_get(config, "host", "APP_HOST", "127.0.0.1")),
        port=_int(_get(config, "port", "APP_PORT", 5000), 5000),
        debug=_bool(_get(config, "debug", "APP_DEBUG", False), False),
    )
