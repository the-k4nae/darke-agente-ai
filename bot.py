import os
import sys
import asyncio
import random
import discord
from discord.ext import commands
from dotenv import load_dotenv

from utils.logger import log
from utils.cache import DISCORD_TOKEN
from utils.constants import ALL_COGS, BOT_PREFIX

load_dotenv()

# ===================== BACKOFF CONFIG =====================
# Evita banimento por Cloudflare em caso de crash-loop
_MAX_RETRIES   = 5       # tentativas máximas antes de desistir
_BASE_DELAY    = 10      # segundos de espera na 1ª falha
_MAX_DELAY     = 300     # teto de 5 minutos entre tentativas

# ===================== CONFIGURAÇÃO DO BOT =====================
intents = discord.Intents.default()
intents.message_content = True
intents.members         = True
intents.guilds          = True
intents.moderation      = True

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents, help_command=None)

# ===================== EXTENSÕES =====================
async def load_extensions():
    for ext in ALL_COGS:
        try:
            await bot.load_extension(ext)
            log.info(f"Módulo carregado: {ext}")
        except Exception as e:
            log.error(f"Falha ao carregar {ext}: {e}")

@bot.event
async def setup_hook():
    await load_extensions()
    # Injeta referência ao bot no pool Groq para alertas de 401
    try:
        from utils.groq_pool import get_pool
        get_pool().set_bot(bot)
        log.info("Pool Groq: bot injetado.")
    except Exception as e:
        log.warning(f"Pool Groq: falha ao injetar bot ({e})")
    log.info("Setup completo — Darke Store Bot v4")

async def main():
    from utils.cache import now_ts
    from utils.database import init_db

    if not DISCORD_TOKEN:
        log.error("Configure DISCORD_TOKEN no arquivo .env")
        sys.exit(1)

    init_db()

    # Carrega configurações persistidas e whitelist do banco para memória
    try:
        from utils.cache import load_config
        load_config()
        log.info("Configurações carregadas do banco.")
    except Exception as e:
        log.warning(f"Falha ao carregar config: {e}")

    try:
        from utils.database import load_whitelist_to_cache
        # Carregar a whitelist globalmente (guild_id=0 para configs globais)
        # Será recarregada por guild no on_ready dos cogs
        log.info("Whitelist será carregada no on_ready de cada guild.")
    except Exception as e:
        log.warning(f"Falha ao preparar whitelist: {e}")

    log.info(f"Iniciando Darke Store Bot v4... [{now_ts()}]")

    # FIX #3 — asyncio.get_event_loop() é depreciado no Python 3.10+
    # get_running_loop() é seguro pois já estamos dentro de uma corrotina
    loop = asyncio.get_running_loop()

    async def _shutdown(sig_name: str):
        log.warning(f"[Bot] Sinal {sig_name} — encerrando gracefully...")
        try:
            await bot.close()
        except Exception as e:
            log.error(f"[Bot] Erro ao fechar: {e}")

    if sys.platform != "win32":
        import signal
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(_shutdown(s.name))
            )

    # ── Exponential backoff no login ──────────────────────────────────────────
    # Previne banimento por Cloudflare (erro 1015/429) em caso de crash-loop.
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with bot:
                await bot.start(DISCORD_TOKEN)
            break  # saiu normalmente (SIGTERM/KeyboardInterrupt)
        except discord.errors.HTTPException as e:
            if e.status == 429:
                delay = min(_BASE_DELAY * (2 ** (attempt - 1)), _MAX_DELAY)
                delay += random.uniform(0, delay * 0.2)  # jitter ±20%
                log.error(
                    f"[Bot] Discord retornou 429 (rate limited pelo Cloudflare). "
                    f"Tentativa {attempt}/{_MAX_RETRIES}. "
                    f"Aguardando {delay:.0f}s antes de tentar novamente..."
                )
                if attempt == _MAX_RETRIES:
                    log.error("[Bot] Máximo de tentativas atingido. Encerrando.")
                    sys.exit(1)
                await asyncio.sleep(delay)
            else:
                log.error(f"[Bot] HTTPException inesperada: {e}")
                raise
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            log.error(f"[Bot] Erro inesperado na tentativa {attempt}: {e}")
            raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("[Bot] Encerrado por KeyboardInterrupt.")
