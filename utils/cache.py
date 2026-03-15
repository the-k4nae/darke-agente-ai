"""
utils/cache.py
──────────────
Configurações em memória, variáveis de ambiente e caches do bot.
"""
import os
from dotenv import load_dotenv
from collections import defaultdict, deque

# Importa now_ts de helpers — fonte única de verdade
from utils.helpers import now_ts  # noqa: F401 — re-exportado para compatibilidade

load_dotenv()

# ── Tokens & IDs ──────────────────────────────────────────────────────────────
DISCORD_TOKEN      = os.getenv("DISCORD_TOKEN", "")
OWNER_ID           = os.getenv("OWNER_ID", "")
SUPPORT_CHANNEL_ID = int(os.getenv("SUPPORT_CHANNEL_ID", "0")) or None
_logs_ch_raw       = os.getenv("LOGS_CHANNEL_ID", "")
LOGS_CHANNEL_ID: int | None = int(_logs_ch_raw) if _logs_ch_raw.isdigit() else None

# ── Groq ──────────────────────────────────────────────────────────────────────
GROQ_API_KEYS: list[str] = [
    v for k, v in os.environ.items()
    if k.startswith("GROQ_API_KEY") and v
]

# ── Configuração em tempo real (alterável via .config) ───────────────────────
config: dict = {
    "ai_cooldown":          10,
    "cache_cleanup_hours":  1,
    "anti_spam_threshold":  5,
    "anti_spam_window":     10,
    "auto_slowmode_threshold": 8,
    "auto_slowmode_delay":  5,
    "max_backups_per_guild": 10,
    # Anti-nuke proteções (novas)
    "anti_mass_ban":        True,
    "anti_mass_kick":       True,
    "anti_webhook_spam":    True,
    "anti_guild_update":    True,
}

# ── Caches em memória ─────────────────────────────────────────────────────────
actions_cache:     dict[str, float]       = {}
spam_cache:        defaultdict[int, deque] = defaultdict(deque)
ai_cooldown_cache: dict[int, float]        = {}


# ── Whitelist (anti-nuke) ─────────────────────────────────────────────────────
# Lista de user IDs que são imunes ao sistema anti-nuke
NUKE_WHITELIST: set[int] = set()

def is_whitelisted(user_id: int) -> bool:
    """Retorna True se o usuário está na whitelist do anti-nuke."""
    if OWNER_ID and str(user_id) == str(OWNER_ID):
        return True
    return user_id in NUKE_WHITELIST

# ── Config persistente em cache ───────────────────────────────────────────────
_config_dirty = False

def save_config():
    """
    Persiste o dict `config` no banco de dados.
    Chamado pelo owner.py ao alterar configurações.
    """
    global _config_dirty
    try:
        from utils.database import set_state
        import json
        set_state("bot_config", json.dumps(config))
        _config_dirty = False
    except Exception as e:
        import logging
        logging.getLogger("darke_store").warning(f"[cache] save_config falhou: {e}")

def load_config():
    """Carrega config persistida do banco (chamado no startup)."""
    try:
        from utils.database import get_state
        import json
        raw = get_state("bot_config", "")
        if raw:
            loaded = json.loads(raw)
            config.update(loaded)
    except Exception:
        pass

# ── Ticket URL ────────────────────────────────────────────────────────────────
TICKET_URL = os.getenv("TICKET_URL", "https://discord.com/channels/")
