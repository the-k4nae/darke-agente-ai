"""
utils/helpers.py
────────────────
Utilitários genéricos do Darke Store Bot.
Fonte única de verdade para funções auxiliares compartilhadas.
"""
import time
from datetime import datetime, timezone


def now_ts() -> float:
    """Retorna timestamp UTC atual como float."""
    return time.time()


def format_duration(seconds: int) -> str:
    """Converte segundos para string legível (ex: 1h30m)."""
    parts = []
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m, s   = divmod(rem, 60)
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s or not parts: parts.append(f"{s}s")
    return "".join(parts)


def truncate(text: str, limit: int = 1024, suffix: str = "…") -> str:
    """Trunca texto para o limite dado, adicionando sufixo se necessário."""
    if len(text) <= limit:
        return text
    return text[:limit - len(suffix)] + suffix


def utcnow() -> datetime:
    """Retorna datetime UTC atual (timezone-aware)."""
    return datetime.now(timezone.utc)
