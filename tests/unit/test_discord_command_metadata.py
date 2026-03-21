import ast
from pathlib import Path

from five08.discord_bot.bot import DISCORD_COMMAND_DESCRIPTION_LIMIT


COGS_DIR = (
    Path(__file__).resolve().parents[2] / "apps/discord_bot/src/five08/discord_bot/cogs"
)


def _command_descriptions() -> list[tuple[Path, int, str, str]]:
    descriptions: list[tuple[Path, int, str, str]] = []

    for path in sorted(COGS_DIR.glob("*.py")):
        module = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(module):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "command"
                ):
                    continue

                command_name = node.name
                description: str | None = None
                for keyword in decorator.keywords:
                    if keyword.arg == "name":
                        value = ast.literal_eval(keyword.value)
                        if isinstance(value, str):
                            command_name = value
                    if keyword.arg == "description":
                        value = ast.literal_eval(keyword.value)
                        if isinstance(value, str):
                            description = value

                if description is not None:
                    descriptions.append((path, node.lineno, command_name, description))

    return descriptions


def test_discord_app_command_descriptions_fit_discord_limit() -> None:
    violations = [
        (
            f"{path.relative_to(Path.cwd())}:{lineno} "
            f"/{name} has {len(description)} characters"
        )
        for path, lineno, name, description in _command_descriptions()
        if len(description) > DISCORD_COMMAND_DESCRIPTION_LIMIT
    ]

    assert not violations, "\n".join(violations)
