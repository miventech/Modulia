from __future__ import annotations

import importlib
import json
import logging
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from modulai.core.modules import Module, ModuleContext, ModuleManifest

MODULE_ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


@dataclass(slots=True)
class ModuleRecord:
    manifest: ModuleManifest
    path: Path
    status: str = "discovered"
    instance: Module | None = None
    error: str | None = None
    config: dict[str, object] | None = None
    config_schema: dict[str, object] | None = None


class ModuleManager:
    def __init__(
        self,
        module_paths: tuple[Path, ...],
        config_dir: Path,
        context: ModuleContext,
        logger: logging.Logger,
    ) -> None:
        self.module_paths = module_paths
        self.config_dir = config_dir
        self.context = context
        self.logger = logger
        self.records: list[ModuleRecord] = []

    @property
    def active_count(self) -> int:
        return sum(record.status == "active" for record in self.records)

    @property
    def error_count(self) -> int:
        return sum(record.status == "error" for record in self.records)

    async def load_and_start(self) -> None:
        self.records = self.discover()
        seen_ids: set[str] = set()
        for record in self.records:
            if record.manifest.id in seen_ids:
                record.status = "error"
                record.error = f"Identificador de módulo duplicado: {record.manifest.id}"
                self.logger.error(record.error)
                continue
            seen_ids.add(record.manifest.id)
            if record.config is not None and record.config.get("enabled") is False:
                record.status = "disabled"
                continue
            await self._start_record(record)

    def discover(self) -> list[ModuleRecord]:
        records: list[ModuleRecord] = []
        for base_path in self.module_paths:
            if not base_path.exists():
                self.logger.warning("El directorio de módulos no existe: %s", base_path)
                continue
            for manifest_path in sorted(base_path.glob("*/module.json")):
                try:
                    manifest = read_manifest(manifest_path)
                    schema = _read_config_schema(manifest_path.parent, manifest)
                    config = self._read_module_config(manifest, schema)
                    records.append(
                        ModuleRecord(
                            manifest=manifest,
                            path=manifest_path.parent,
                            config=config,
                            config_schema=schema,
                        )
                    )
                except Exception as error:
                    fallback = ModuleManifest(
                        schema_version=0,
                        id=f"invalid.{manifest_path.parent.name}",
                        name=manifest_path.parent.name,
                        version="0",
                        entrypoint="",
                        requires_core="",
                        capabilities=(),
                    )
                    records.append(
                        ModuleRecord(
                            manifest=fallback,
                            path=manifest_path.parent,
                            status="error",
                            error=f"Manifiesto inválido: {error}",
                        )
                    )
        return records

    def update_config(self, module_id: str, value: dict[str, object]) -> ModuleRecord:
        record = next(
            (item for item in self.records if item.manifest.id == module_id),
            None,
        )
        if record is None:
            raise LookupError(f"Módulo no encontrado: {module_id}")
        schema = record.config_schema or {"type": "object", "properties": {}}
        _validate_config(value, schema)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        target = self.config_dir / f"{module_id}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
        record.config = dict(value)
        return record

    def _read_module_config(
        self,
        manifest: ModuleManifest,
        schema: dict[str, object],
    ) -> dict[str, object]:
        defaults = _schema_defaults(schema)
        path = self.config_dir / f"{manifest.id}.json"
        if not path.is_file():
            return defaults
        with path.open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
        if not isinstance(raw, dict):
            raise ValueError(f"La configuración de {manifest.id} debe ser un objeto")
        value = {**defaults, **raw}
        _validate_config(value, schema)
        return value

    async def _start_record(self, record: ModuleRecord) -> None:
        if record.status == "error":
            self.logger.error("No se cargó %s: %s", record.path, record.error)
            return
        try:
            instance = _create_instance(record)
            await instance.setup(replace(self.context, config=record.config or {}))
            await instance.start()
            record.instance = instance
            record.status = "active"
            self.logger.info("Módulo activo: %s %s", record.manifest.id, record.manifest.version)
        except Exception as error:
            record.status = "error"
            record.error = f"{type(error).__name__}: {error}"
            self.logger.exception("Falló el módulo %s", record.manifest.id)

    async def stop(self) -> None:
        for record in reversed(self.records):
            if record.status != "active" or record.instance is None:
                continue
            try:
                await record.instance.stop()
                record.status = "stopped"
            except Exception as error:
                record.status = "error"
                record.error = f"Error al detener: {type(error).__name__}: {error}"
                self.logger.exception("No se pudo detener el módulo %s", record.manifest.id)


def read_manifest(path: Path) -> ModuleManifest:
    with path.open("r", encoding="utf-8") as stream:
        raw = json.load(stream)
    required = ("schema_version", "id", "name", "version", "entrypoint", "requires_core")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"Faltan campos: {', '.join(missing)}")
    if raw["schema_version"] != 1:
        raise ValueError(f"schema_version no soportado: {raw['schema_version']}")
    if not isinstance(raw["id"], str) or not MODULE_ID.fullmatch(raw["id"]):
        raise ValueError("id tiene un formato inválido")
    if not isinstance(raw.get("capabilities", []), list):
        raise ValueError("capabilities debe ser una lista")
    if ":" not in raw["entrypoint"]:
        raise ValueError("entrypoint debe tener el formato paquete.modulo:factory")
    return ModuleManifest(
        schema_version=raw["schema_version"],
        id=raw["id"],
        name=str(raw["name"]),
        version=str(raw["version"]),
        entrypoint=raw["entrypoint"],
        requires_core=str(raw["requires_core"]),
        capabilities=tuple(str(item) for item in raw.get("capabilities", [])),
        config_schema=str(raw["config_schema"]) if raw.get("config_schema") else None,
    )


def _create_instance(record: ModuleRecord) -> Module:
    module_name, factory_name = record.manifest.entrypoint.split(":", maxsplit=1)
    source_path = record.path / "src"
    if not source_path.is_dir():
        raise FileNotFoundError(f"No existe el directorio de código: {source_path}")

    source = str(source_path.resolve())
    if source not in sys.path:
        sys.path.insert(0, source)
    imported = importlib.import_module(module_name)
    factory = getattr(imported, factory_name)
    instance = factory()
    for method in ("setup", "start", "stop"):
        if not callable(getattr(instance, method, None)):
            raise TypeError(f"El módulo no implementa {method}()")
    return instance


def _read_config_schema(module_path: Path, manifest: ModuleManifest) -> dict[str, object]:
    if manifest.config_schema is None:
        return {"type": "object", "properties": {}}
    schema_path = (module_path / manifest.config_schema).resolve()
    if module_path.resolve() not in schema_path.parents:
        raise ValueError("config_schema debe permanecer dentro del módulo")
    with schema_path.open("r", encoding="utf-8") as stream:
        schema = json.load(stream)
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("El esquema de configuración debe describir un objeto")
    return schema


def _schema_defaults(schema: dict[str, object]) -> dict[str, object]:
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("properties del esquema debe ser un objeto")
    return {
        str(name): definition["default"]
        for name, definition in properties.items()
        if isinstance(definition, dict) and "default" in definition
    }


def _validate_config(value: dict[str, object], schema: dict[str, object]) -> None:
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("properties del esquema debe ser un objeto")
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(value) - set(properties))
        if unknown:
            raise ValueError(f"Opciones desconocidas: {', '.join(unknown)}")
    for name, current in value.items():
        definition = properties.get(name)
        if not isinstance(definition, dict):
            continue
        expected = definition.get("type")
        valid = (
            (expected == "boolean" and isinstance(current, bool))
            or (expected == "string" and isinstance(current, str))
            or (
                expected == "integer"
                and isinstance(current, int)
                and not isinstance(current, bool)
            )
            or (expected == "array" and isinstance(current, list))
            or (expected == "object" and isinstance(current, dict))
            or expected is None
        )
        if not valid:
            raise ValueError(f"{name} debe ser de tipo {expected}")
