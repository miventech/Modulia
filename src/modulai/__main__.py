from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from modulai.bootstrap import Application


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Asistente personal modular")
    parser.add_argument(
        "--config",
        type=Path,
        help="Ruta al archivo TOML. Por defecto usa config/app.toml o el ejemplo.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--command", help="Ejecuta un comando y termina.")
    mode.add_argument("--console", action="store_true", help="Abre la consola interactiva.")
    mode.add_argument("--web", action="store_true", help="Abre la interfaz visual local.")
    mode.add_argument("--list-modules", action="store_true", help="Lista los módulos detectados.")
    return parser


async def run(args: argparse.Namespace) -> int:
    application = Application.create(Path.cwd(), args.config)
    await application.start()
    try:
        if args.command:
            result = await application.execute_text(args.command)
            print(result.text)
            return 0 if result.ok else 1

        if args.list_modules:
            for module in application.modules.records:
                detail = f" — {module.error}" if module.error else ""
                print(f"{module.manifest.id}: {module.status}{detail}")
            return 0

        if args.console:
            print("ModulAI listo. Escribe /salir para terminar.")
            while True:
                try:
                    text = await asyncio.to_thread(input, "> ")
                except (EOFError, KeyboardInterrupt):
                    break
                if text.strip().lower() == "/salir":
                    break
                result = await application.execute_text(text)
                print(result.text)
            return 0

        if args.web:
            from modulai.web import serve

            await serve(application)
            return 0

        print("ModulAI inició correctamente.")
        print("Usa --web, --console, --command o --list-modules.")
        return 0
    finally:
        await application.stop()


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
