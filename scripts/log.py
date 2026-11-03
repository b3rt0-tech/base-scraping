from __future__ import annotations

import logging
import sys

LOGGER_NAME = "base_scraping"


def get_logger(nome: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{nome}")


def configure(*, verbose: bool = False, quiet: bool = False) -> None:
    """Liga os logs do pipeline em **stderr**. O stdout fica só com o JSON final — é o contrato
    que o skill/IA lê. Padrão: progresso (INFO); `verbose` acrescenta o detalhe por lead (DEBUG);
    `quiet` deixa só avisos e erros.
    """
    nivel = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    handler = logging.StreamHandler(sys.stderr)  # resolvido agora, não no import (CliRunner troca o stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"))
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers = [handler]
    logger.setLevel(nivel)
    logger.propagate = False
