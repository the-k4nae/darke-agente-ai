"""
utils/logger.py
───────────────
Logger centralizado do Darke Store Bot.
"""
import logging
import os
import discord
from logging.handlers import RotatingFileHandler

# ── Logger padrão ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("darke_store")

# ── Logger de arquivo rotativo ────────────────────────────────────────────────
# FIX #17 — RotatingFileHandler evita crescimento ilimitado do bot.log em produção.
# Mantém até 3 arquivos de 5MB cada (total máximo: 15MB de logs).
try:
    os.makedirs("logs", exist_ok=True)
    fh = RotatingFileHandler(
        "logs/bot.log",
        maxBytes=5 * 1024 * 1024,   # 5 MB por arquivo
        backupCount=3,               # bot.log, bot.log.1, bot.log.2, bot.log.3
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    log.addHandler(fh)
except Exception:
    pass  # fallback silencioso — não impede o bot de iniciar


async def log_action(
    guild: discord.Guild,
    message: str,
    color: discord.Color = discord.Color.red(),
    embed: discord.Embed = None,
):
    """Envia uma mensagem de log para o canal de logs configurado."""
    from utils.cache import LOGS_CHANNEL_ID
    if not LOGS_CHANNEL_ID:
        return
    channel = guild.get_channel(LOGS_CHANNEL_ID)
    if not channel:
        return
    try:
        if embed:
            await channel.send(embed=embed)
        else:
            e = discord.Embed(description=message, color=color)
            await channel.send(embed=e)
    except Exception as exc:
        log.warning(f"Falha ao enviar log_action: {exc}")
