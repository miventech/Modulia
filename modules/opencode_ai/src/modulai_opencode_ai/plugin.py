from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

from modulai.core.commands import (
    CommandContext,
    CommandDefinition,
    CommandResult,
    ParsedCommand,
    parse_command,
)
from modulai.core.modules import ModuleContext


class OpenCodeAIModule:
    def __init__(self) -> None:
        self._context: ModuleContext | None = None
        self._base_url = ""
        self._api_key_env = "OPENCODE_API_KEY"
        self._model = ""
        self._timeout_seconds = 60
        self._max_tokens = 1200
        self._max_tool_rounds = 4
        self._context_message_limit = 10
        self._allow_tools = True
        self._system_prompt = "Responde en español, sé claro y confirma los resultados."

    async def setup(self, context: ModuleContext) -> None:
        self._context = context
        self._base_url = str(context.config.get("base_url", "")).strip()
        self._api_key_env = str(context.config.get("api_key_env", "OPENCODE_API_KEY")).strip()
        self._model = str(context.config.get("model", "")).strip()
        self._timeout_seconds = max(5, min(300, int(context.config.get("timeout_seconds", 60))))
        self._max_tokens = max(1, min(16_000, int(context.config.get("max_tokens", 1200))))
        self._max_tool_rounds = max(1, min(12, int(context.config.get("max_tool_rounds", 4))))
        self._context_message_limit = max(0, min(100, int(context.config.get("context_message_limit", 10))))
        self._allow_tools = bool(context.config.get("allow_tools", True))
        self._system_prompt = str(context.config.get("system_prompt", self._system_prompt)).strip()
        context.commands.register(
            CommandDefinition("ia", "Consulta la IA OpenCode con acceso a los módulos.", self.ask)
        )
        context.commands.register(
            CommandDefinition("ia_estado", "Muestra el estado de la conexión con la IA.", self.status)
        )

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def list_models(self) -> list[str]:
        """Obtiene los modelos publicados por el endpoint configurado."""
        if not self._base_url:
            raise ValueError("Configura primero el endpoint de OpenCode.")
        key = os.environ.get(self._api_key_env, "")
        if not key and not self._is_local_endpoint():
            raise ValueError(f"Falta la variable de entorno {self._api_key_env}.")
        payload = await asyncio.to_thread(self._request_models, key)
        raw_models = payload.get("data", payload.get("models", []))
        if not isinstance(raw_models, list):
            raise ValueError("La respuesta de modelos no tiene una lista válida.")
        models = [
            str(item.get("id"))
            for item in raw_models
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
        ]
        return sorted(set(models))

    def status(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        del context, command
        if not self._base_url:
            return CommandResult.failure("Configura la URL del endpoint en el módulo local.opencode_ai.")
        if not self._model:
            return CommandResult.failure("Configura el identificador del modelo en el módulo local.opencode_ai.")
        key_configured = bool(os.environ.get(self._api_key_env))
        local_endpoint = self._base_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))
        if not key_configured and not local_endpoint:
            return CommandResult.failure(
                f"No existe la variable {self._api_key_env}. Configúrala en .env sin escribir la clave en module.json."
            )
        tools = "activadas" if self._allow_tools else "desactivadas"
        return CommandResult.success(
            f"IA configurada: modelo {self._model}, endpoint {self._base_url}, herramientas {tools}."
        )

    async def ask(self, context: CommandContext, command: ParsedCommand) -> CommandResult:
        prompt = command.raw_args.strip()
        if not prompt:
            return CommandResult.failure("Uso: /ia <pregunta o instrucción>")
        return await self._ask_prompt(context, prompt)

    async def handle_message(self, context: CommandContext) -> CommandResult | None:
        """Responde texto normal cuando la IA está activa, sin exigir una barra."""
        prompt = (context.message.text or "").strip()
        if not prompt:
            return None
        if not self._base_url or not self._model:
            return None
        return await self._ask_prompt(context, prompt)

    async def _ask_prompt(self, context: CommandContext, prompt: str) -> CommandResult:
        if not self._base_url or not self._model:
            return CommandResult.failure("Configura endpoint y modelo en local.opencode_ai y reinicia ModulAI.")
        if not self._api_key_env:
            return CommandResult.failure("Configura api_key_env o usa un endpoint local sin clave.")
        key = os.environ.get(self._api_key_env, "")
        if not key and not self._is_local_endpoint():
            return CommandResult.failure(f"Falta la variable de entorno {self._api_key_env}.")
        assert self._context is not None
        messages: list[dict[str, Any]] = [{"role": "system", "content": self._build_system_prompt()}]
        if self._context_message_limit:
            history = self._context.store.list_messages(
                principal_id=context.message.principal_id,
                conversation_id=context.message.conversation_id,
                limit=self._context_message_limit,
            )
            for item in reversed(history):
                messages.append({"role": "user" if item["direction"] == "inbound" else "assistant", "content": str(item["text"])})
        messages.append({"role": "user", "content": prompt})
        tools = self._tool_schema() if self._allow_tools else []
        session_id = _session_id(context.message.conversation_id)
        for _ in range(self._max_tool_rounds):
            try:
                response = await asyncio.to_thread(self._request, messages, tools, key, session_id)
            except (OSError, ValueError, TimeoutError) as error:
                self._context.logger.warning("Falló la petición a OpenCode: %s", error)
                return CommandResult.failure(f"No pude conectar con OpenCode: {error}")
            assistant = self._assistant_message(response)
            if assistant is None:
                return CommandResult.failure("OpenCode devolvió una respuesta sin mensaje.")
            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                text = _content_text(assistant.get("content"))
                return CommandResult.success(text or "La IA no devolvió texto.")
            messages.append(assistant)
            for call in tool_calls:
                tool_id = str(call.get("id", ""))
                function = call.get("function", {})
                command_text = _tool_command(function.get("arguments"))
                result_text = await self._execute_tool(context, command_text)
                messages.append({"role": "tool", "tool_call_id": tool_id, "content": result_text})
        return CommandResult.failure("La IA alcanzó el máximo de rondas de comandos sin terminar.")

    async def _execute_tool(self, context: CommandContext, command_text: str | None) -> str:
        if not command_text:
            return "Herramienta inválida: se esperaba un comando como /finanzas 2026-09."
        parsed = parse_command(command_text)
        if parsed is None:
            return "Herramienta inválida: el comando debe comenzar con '/'."
        if parsed.name in {"ia", "ia_estado"}:
            return "No se permite invocar la herramienta de IA de forma recursiva."
        assert self._context is not None
        result = self._context.commands.execute(parsed, CommandContext(message=context.message))
        if hasattr(result, "__await__"):
            result = await result
        assert isinstance(result, CommandResult)
        return result.text

    def _build_system_prompt(self) -> str:
        assert self._context is not None
        commands = "; ".join(
            f"/{definition.name}: {definition.description}"
            for definition in self._context.commands.definitions
            if definition.name not in {"ia", "ia_estado"}
        )
        return (
            f"{self._system_prompt}\n"
            "Eres el asistente de ModulAI. Tienes acceso a los comandos y módulos instalados. "
            "Cuando necesites datos o una acción, usa la herramienta ejecutar_comando; no inventes resultados. "
            f"Comandos disponibles: {commands}"
        )

    def _tool_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "ejecutar_comando",
                    "description": "Ejecuta un comando de ModulAI para consultar o modificar información.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "Comando completo, por ejemplo /finanzas 2026-09 o /gasto 10 comida.",
                            }
                        },
                        "required": ["command"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

    def _request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        key: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ModulAI/0.1 (OpenCode-compatible client)",
        }
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if session_id:
            headers["x-opencode-session"] = session_id
        request = urllib.request.Request(
            _completion_url(self._base_url),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                raw = response.read(4 * 1024 * 1024)
        except urllib.error.HTTPError as error:
            detail = error.read(2_000).decode("utf-8", errors="replace")
            raise ValueError(f"HTTP {error.code}: {detail[:500]}") from error
        except urllib.error.URLError as error:
            raise OSError(error.reason) from error
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("respuesta JSON inválida")
        return value

    def _request_models(self, key: str) -> dict[str, Any]:
        models_url = self._base_url
        for suffix in ("/chat/completions", "/responses"):
            if models_url.endswith(suffix):
                models_url = models_url[: -len(suffix)]
                break
        models_url = models_url.rstrip("/") + "/models"
        headers = {
            "Accept": "application/json",
            "User-Agent": "ModulAI/0.1 (OpenCode-compatible client)",
        }
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(models_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                raw = response.read(2 * 1024 * 1024)
        except urllib.error.HTTPError as error:
            detail = error.read(2_000).decode("utf-8", errors="replace")
            raise ValueError(f"HTTP {error.code}: {detail[:500]}") from error
        except urllib.error.URLError as error:
            raise OSError(error.reason) from error
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("respuesta JSON inválida")
        return value

    def _assistant_message(self, response: dict[str, Any]) -> dict[str, Any] | None:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return None
        message = choices[0].get("message")
        return message if isinstance(message, dict) else None

    def _is_local_endpoint(self) -> bool:
        return self._base_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))


def _tool_command(arguments: object) -> str | None:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    if not isinstance(arguments, dict) or not isinstance(arguments.get("command"), str):
        return None
    return arguments["command"].strip()


def _completion_url(base_url: str) -> str:
    """Acepta por tolerancia una URL /models pegada desde la documentación."""
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/models"):
        return normalized[: -len("/models")] + "/chat/completions"
    return normalized


def _session_id(conversation_id: str) -> str:
    """Convierte la conversación de ModulAI en un ID estable y válido para OpenCode Go."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"modulai:opencode:{conversation_id}"))


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        return "".join(str(part) for part in parts).strip()
    return ""


def create_module() -> OpenCodeAIModule:
    return OpenCodeAIModule()
