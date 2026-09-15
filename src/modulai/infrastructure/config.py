from __future__ import annotations

import ast
import configparser
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    level: str
    file_enabled: bool
    file_name: str


@dataclass(frozen=True, slots=True)
class WebConfig:
    host: str
    port: int
    open_browser: bool


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    enabled: bool
    token_env: str
    activation_password_env: str
    activation_timeout_seconds: int
    max_activation_attempts: int
    network_timeout_seconds: int
    allowed_user_ids: tuple[int, ...]
    drop_pending_updates: bool


@dataclass(frozen=True, slots=True)
class AppConfig:
    name: str
    data_dir: Path
    module_paths: tuple[Path, ...]
    logging: LoggingConfig
    web: WebConfig
    telegram: TelegramConfig


def load_config(root: Path, config_path: Path | None = None) -> AppConfig:
    _load_dotenv(root / ".env")
    selected = _select_config(root, config_path)
    raw = _read_toml(selected)

    app = raw.get("app", {})
    modules = raw.get("modules", {})
    logging = raw.get("logging", {})
    web = raw.get("web", {})
    telegram = raw.get("telegram", {})

    name = _non_empty_string(app.get("name", "ModulAI"), "app.name")
    data_dir = _resolve(root, _non_empty_string(app.get("data_dir", "data"), "app.data_dir"))

    raw_paths = modules.get("paths", ["modules"])
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ValueError("modules.paths debe ser una lista no vacía")
    module_paths = tuple(
        _resolve(root, _non_empty_string(value, "modules.paths")) for value in raw_paths
    )

    level = _non_empty_string(logging.get("level", "INFO"), "logging.level").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"Nivel de logging inválido: {level}")

    host = _non_empty_string(web.get("host", "127.0.0.1"), "web.host")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("La Entrega 1 solo permite un servidor local")
    port = web.get("port", 8765)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("web.port debe ser un entero entre 1 y 65535")

    token_env = _non_empty_string(
        telegram.get("token_env", "MODULAI_TELEGRAM_TOKEN"),
        "telegram.token_env",
    )
    activation_password_env = _non_empty_string(
        telegram.get("activation_password_env", "MODULAI_TELEGRAM_ACTIVATION_PASSWORD"),
        "telegram.activation_password_env",
    )
    activation_timeout = telegram.get("activation_timeout_seconds", 300)
    if (
        not isinstance(activation_timeout, int)
        or isinstance(activation_timeout, bool)
        or not 30 <= activation_timeout <= 3600
    ):
        raise ValueError("telegram.activation_timeout_seconds debe estar entre 30 y 3600")
    max_attempts = telegram.get("max_activation_attempts", 3)
    if (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or not 1 <= max_attempts <= 10
    ):
        raise ValueError("telegram.max_activation_attempts debe estar entre 1 y 10")
    network_timeout = telegram.get("network_timeout_seconds", 30)
    if (
        not isinstance(network_timeout, int)
        or isinstance(network_timeout, bool)
        or not 5 <= network_timeout <= 120
    ):
        raise ValueError("telegram.network_timeout_seconds debe estar entre 5 y 120")
    raw_allowed = telegram.get("allowed_user_ids", [])
    if not isinstance(raw_allowed, list):
        raise ValueError("telegram.allowed_user_ids debe ser una lista")
    allowed_user_ids: list[int] = []
    for value in raw_allowed:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("telegram.allowed_user_ids solo admite enteros")
        allowed_user_ids.append(value)

    return AppConfig(
        name=name,
        data_dir=data_dir,
        module_paths=module_paths,
        logging=LoggingConfig(
            level=level,
            file_enabled=bool(logging.get("file_enabled", True)),
            file_name=_non_empty_string(
                logging.get("file_name", "modulai.log"),
                "logging.file_name",
            ),
        ),
        web=WebConfig(
            host=host,
            port=port,
            open_browser=bool(web.get("open_browser", True)),
        ),
        telegram=TelegramConfig(
            enabled=bool(telegram.get("enabled", False)),
            token_env=token_env,
            activation_password_env=activation_password_env,
            activation_timeout_seconds=activation_timeout,
            max_activation_attempts=max_attempts,
            network_timeout_seconds=network_timeout,
            allowed_user_ids=tuple(allowed_user_ids),
            drop_pending_updates=bool(telegram.get("drop_pending_updates", False)),
        ),
    )


def _select_config(root: Path, config_path: Path | None) -> Path:
    if config_path is not None:
        selected = config_path if config_path.is_absolute() else root / config_path
        if not selected.is_file():
            raise FileNotFoundError(f"No existe el archivo de configuración: {selected}")
        return selected

    private_config = root / "config" / "app.toml"
    if private_config.is_file():
        return private_config

    example_config = root / "config" / "app.example.toml"
    if not example_config.is_file():
        raise FileNotFoundError("No existe config/app.toml ni config/app.example.toml")
    return example_config


def ensure_config_file(root: Path) -> tuple[Path, bool]:
    """Crea config/app.toml desde el ejemplo cuando la configuración privada falta."""
    config_dir = root / "config"
    target = config_dir / "app.toml"
    if target.is_file():
        return target, False
    example = config_dir / "app.example.toml"
    if not example.is_file():
        raise FileNotFoundError("No existe config/app.example.toml")
    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example, target)
    return target, True


def read_config_values(root: Path) -> tuple[dict[str, Any], bool]:
    path, created = ensure_config_file(root)
    return _read_toml(path), created


def write_config_values(root: Path, values: dict[str, Any]) -> AppConfig:
    allowed_sections = {"app", "modules", "logging", "web", "telegram"}
    allowed_fields = {
        "app": {"name", "data_dir"},
        "modules": {"paths"},
        "logging": {"level", "file_enabled", "file_name"},
        "web": {"host", "port", "open_browser"},
        "telegram": {
            "enabled",
            "token_env",
            "activation_password_env",
            "activation_timeout_seconds",
            "max_activation_attempts",
            "network_timeout_seconds",
            "allowed_user_ids",
            "drop_pending_updates",
        },
    }
    if not isinstance(values, dict) or set(values) - allowed_sections:
        raise ValueError("La configuración contiene secciones no permitidas")
    for section in allowed_sections:
        if section in values and not isinstance(values[section], dict):
            raise ValueError(f"La sección {section} debe ser un objeto")
        if section in values:
            unknown = set(values[section]) - allowed_fields[section]
            if unknown:
                raise ValueError(
                    f"Claves no permitidas en {section}: {', '.join(sorted(unknown))}"
                )

    target, _ = ensure_config_file(root)
    temporary = target.with_suffix(".toml.tmp")
    temporary.write_text(_serialize_toml(values), encoding="utf-8")
    try:
        validated = load_config(root, temporary)
        temporary.replace(target)
        return validated
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _serialize_toml(values: dict[str, Any]) -> str:
    lines: list[str] = []
    for section, section_values in values.items():
        if not isinstance(section_values, dict):
            raise ValueError(f"La sección {section} debe ser un objeto")
        lines.append(f"[{section}]")
        for key, value in section_values.items():
            if not isinstance(key, str) or not key.replace("_", "").isalnum():
                raise ValueError(f"Clave de configuración inválida: {key}")
            lines.append(f"{key} = {_serialize_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def _serialize_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        if not all(isinstance(item, (str, int)) and not isinstance(item, bool) for item in value):
            raise ValueError("Solo se permiten listas de texto o enteros")
        return "[" + ", ".join(_serialize_toml_value(item) for item in value) + "]"
    raise ValueError(f"Tipo de configuración no soportado: {type(value).__name__}")


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def _non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} debe ser texto no vacío")
    return value.strip()


def _read_toml(path: Path) -> dict[str, Any]:
    if tomllib is not None:
        with path.open("rb") as stream:
            return tomllib.load(stream)

    parser = configparser.ConfigParser(interpolation=None)
    with path.open("r", encoding="utf-8") as stream:
        parser.read_file(stream)
    return {
        section: {key: _parse_legacy_value(value) for key, value in parser[section].items()}
        for section in parser.sections()
    }


def _parse_legacy_value(value: str) -> object:
    stripped = value.strip()
    lowered = stripped.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return ast.literal_eval(stripped)
    except (SyntaxError, ValueError):
        return stripped


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", maxsplit=1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name.replace("_", "").isalnum():
            os.environ.setdefault(name, value)
