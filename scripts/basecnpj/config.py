from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://basecnpj.app/api/v1/"
DEFAULT_TIMEOUT = 10.0
HEAVY_ROUTE_TIMEOUT = 60.0
HEAVY_ROUTES = frozenset({"avancada", "rede"})
# raiz da skill (onde ficam pyproject.toml e .env), independente de onde o comando é chamado
SKILL_ROOT = Path(__file__).resolve().parents[2]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = _unquote(value.strip())


def load_dotenv_files(paths: Iterable[Path] | None = None) -> None:
    """Carrega o `.env` da raiz da skill e, como fallback, o do diretório atual.

    Variável já definida no ambiente sempre vence; entre os arquivos, vale o primeiro da lista.
    """
    for path in paths if paths is not None else (SKILL_ROOT / ".env", Path.cwd() / ".env"):
        _load_dotenv(path)


load_dotenv_files()


@dataclass(frozen=True)
class Settings:
    token: str | None
    base_url: str
    default_timeout: float
    heavy_route_timeout: float

    def timeout_for(self, route: str) -> float:
        return self.heavy_route_timeout if route in HEAVY_ROUTES else self.default_timeout


def get_settings() -> Settings:
    return Settings(
        token=os.environ.get("BASECNPJ_TOKEN"),
        base_url=os.environ.get("BASECNPJ_BASE_URL", DEFAULT_BASE_URL),
        default_timeout=DEFAULT_TIMEOUT,
        heavy_route_timeout=HEAVY_ROUTE_TIMEOUT,
    )
