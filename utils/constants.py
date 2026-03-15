"""
utils/constants.py
──────────────────
Constantes centralizadas do Darke Store Bot.
Evita duplicação de listas e valores hardcoded espalhados pelo código.
"""
import os

# ── Prefixo do bot ────────────────────────────────────────────────────────────
BOT_PREFIX = os.getenv("BOT_PREFIX", ".")

# ── Cogs obrigatórios (críticos para funcionamento do bot) ────────────────────
# Fonte única de verdade — usado em bot.py e health_check.py
REQUIRED_COGS = [
    "cogs.events",
    "cogs.moderation",
    "cogs.anti_nuke",
    "cogs.anti_raid",
    "cogs.safety",
    "cogs.ai_support",
    "cogs.warns",
    "cogs.backup",
    "cogs.analytics",
    "cogs.health_check",
]

# ── Todos os cogs carregados no startup ───────────────────────────────────────
ALL_COGS = [
    "cogs.events",
    "cogs.moderation",
    "cogs.anti_nuke",
    "cogs.safety",
    "cogs.word_filter",
    "cogs.ai_support",
    "cogs.warns",
    "cogs.member_logs",
    "cogs.message_logs",
    "cogs.backup",
    "cogs.anti_raid",
    "cogs.owner",
    "cogs.roles",
    "cogs.giveaway",
    "cogs.modlog",
    "cogs.analytics",
    "cogs.health_check",
    "cogs.alerts",
    "cogs.ux",
    "cogs.ai_tools",
    "cogs.groq_metrics",       # novo: dashboard de custo Groq
    "cogs.support_shortcuts",  # novo: respostas rápidas para staff
]
