"""
utils/database.py
─────────────────
Camada de persistência SQLite para o Darke Store Bot.

Tabelas:
  - ai_history       : histórico de conversa por usuário
  - ai_quality       : feedbacks de qualidade da IA
  - warns            : avisos de moderação
  - mod_log          : log de ações de moderação
  - analytics_events : eventos para estatísticas gerais
  - command_usage    : contagem de uso de comandos
  - member_activity  : entradas/saídas de membros
  - backup_registry  : registro de backups com metadados
"""

import sqlite3
import time
from contextlib import contextmanager
from utils.logger import log

DB_PATH = "darke_store.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# INICIALIZAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

def _migrate_db(conn):
    """Migrações de schema incremental — executa na inicialização."""
    # Migration: renomear coluna winners → winners_count na tabela giveaways
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(giveaways)").fetchall()]
        if "winners" in cols and "winners_count" not in cols:
            conn.execute("ALTER TABLE giveaways RENAME COLUMN winners TO winners_count")
            log.info("[DB Migration] giveaways.winners → winners_count")
    except Exception as e:
        log.warning(f"[DB Migration] Falha ao renomear coluna: {e}")

    # Migration: adicionar coluna 'category' na tabela ai_quality se não existir
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(ai_quality)").fetchall()]
        if "category" not in cols:
            conn.execute("ALTER TABLE ai_quality ADD COLUMN category TEXT")
            log.info("[DB Migration] ai_quality + column 'category'")
    except Exception as e:
        log.warning(f"[DB Migration] Falha ao adicionar coluna category: {e}")

    # Migration: criar índices de data que podem não existir em bancos antigos
    _new_indexes = [
        ("idx_analytics_date", "CREATE INDEX IF NOT EXISTS idx_analytics_date ON analytics_events(guild_id, created_at)"),
        ("idx_ai_quality_guild","CREATE INDEX IF NOT EXISTS idx_ai_quality_guild ON ai_quality(guild_id, outcome)"),
        ("idx_ai_quality_date", "CREATE INDEX IF NOT EXISTS idx_ai_quality_date ON ai_quality(user_id, created_at)"),
    ]
    for idx_name, idx_sql in _new_indexes:
        try:
            conn.execute(idx_sql)
        except Exception as e:
            log.warning(f"[DB Migration] Índice {idx_name}: {e}")


def init_db():
    with get_conn() as conn:
        _migrate_db(conn)
        # ── IA ────────────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_history_user ON ai_history(user_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_quality (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                guild_id   INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                outcome    TEXT NOT NULL,
                category   TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_quality_guild ON ai_quality(guild_id, outcome)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_quality_date  ON ai_quality(user_id, created_at)")

        # ── Moderação ────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS warns (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                reason     TEXT,
                moderator  TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS mod_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   INTEGER NOT NULL,
                target_id  INTEGER NOT NULL,
                mod_id     INTEGER NOT NULL,
                action     TEXT NOT NULL,
                reason     TEXT,
                duration   TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mod_log_guild ON mod_log(guild_id, target_id)")

        # ── Analytics & Estatísticas ─────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                user_id    INTEGER,
                extra      TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_guild ON analytics_events(guild_id, event_type)")
        # Índice composto com created_at — queries de analytics sempre filtram por data
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_date ON analytics_events(guild_id, created_at)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS command_usage (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                command_name TEXT NOT NULL,
                success      INTEGER DEFAULT 1,
                created_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cmd_guild ON command_usage(guild_id, command_name)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS member_activity (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id   INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                event      TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_member_guild ON member_activity(guild_id, event)")

        # ── Backup Registry ───────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backup_registry (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER NOT NULL,
                filename     TEXT NOT NULL UNIQUE,
                roles_count  INTEGER DEFAULT 0,
                channels_count INTEGER DEFAULT 0,
                categories_count INTEGER DEFAULT 0,
                file_size_kb REAL DEFAULT 0,
                created_by   INTEGER,
                restored_at  TEXT,
                restored_by  INTEGER,
                created_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_backup_guild ON backup_registry(guild_id)")

        # ── FAQ Suggestions ───────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS faq_suggestions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER NOT NULL,
                suggestion  TEXT NOT NULL,
                period_days INTEGER DEFAULT 7,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)

    log.info("Banco de dados inicializado com sucesso.")


# ─────────────────────────────────────────────────────────────────────────────
# IA HISTORY
# ─────────────────────────────────────────────────────────────────────────────

def get_ai_history(user_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM ai_history WHERE user_id=? ORDER BY id DESC LIMIT 20",
            (user_id,)
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def append_ai_history(user_id: int, role: str, content: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ai_history (user_id, role, content) VALUES (?,?,?)",
            (user_id, role, content)
        )
        # Mantém apenas as últimas 20 mensagens por usuário
        conn.execute("""
            DELETE FROM ai_history WHERE user_id=? AND id NOT IN (
                SELECT id FROM ai_history WHERE user_id=? ORDER BY id DESC LIMIT 20
            )
        """, (user_id, user_id))


def clear_ai_history(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM ai_history WHERE user_id=?", (user_id,))


# ─────────────────────────────────────────────────────────────────────────────
# AI QUALITY
# ─────────────────────────────────────────────────────────────────────────────

def log_ai_quality(user_id: int, guild_id: int, channel_id: int, outcome: str, category: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ai_quality (user_id,guild_id,channel_id,outcome,category) VALUES (?,?,?,?,?)",
            (user_id, guild_id, channel_id, outcome, category)
        )


def get_ai_quality_stats(guild_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as total, SUM(outcome='resolved') as resolved, SUM(outcome='unresolved') as unresolved FROM ai_quality WHERE guild_id=?",
            (guild_id,)
        ).fetchone()
        row_7d = conn.execute(
            "SELECT COUNT(*) as total, SUM(outcome='resolved') as resolved FROM ai_quality WHERE guild_id=? AND created_at >= datetime('now','-7 days')",
            (guild_id,)
        ).fetchone()
        cats = conn.execute(
            "SELECT category as name, COUNT(*) as count FROM ai_quality WHERE guild_id=? AND category IS NOT NULL GROUP BY category ORDER BY count DESC LIMIT 8",
            (guild_id,)
        ).fetchall()

    total     = row["total"]    or 0
    resolved  = row["resolved"] or 0
    rate      = round((resolved / total) * 100, 1) if total else 0
    return {
        "total":       total,
        "resolved":    resolved,
        "unresolved":  row["unresolved"] or 0,
        "rate":        rate,
        "total_7d":    row_7d["total"]    or 0,
        "resolved_7d": row_7d["resolved"] or 0,
        "categories":  [{"name": c["name"], "count": c["count"]} for c in cats],
    }


def get_user_unresolved_count(user_id: int, hours: int = 1) -> int:
    """Quantas vezes o usuário clicou 'ainda preciso de ajuda' nas últimas N horas."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM ai_quality WHERE user_id=? AND outcome='unresolved' "
            "AND created_at >= datetime('now', ?)",
            (user_id, f"-{hours} hours")
        ).fetchone()
    return row["cnt"] if row else 0


def get_top_unresolved_users(guild_id: int, days: int = 7, limit: int = 10) -> list[dict]:
    """Usuários com mais interações não resolvidas — detecta casos recorrentes."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, COUNT(*) as count FROM ai_quality "
            "WHERE guild_id=? AND outcome='unresolved' AND created_at >= datetime('now', ?) "
            "GROUP BY user_id ORDER BY count DESC LIMIT ?",
            (guild_id, f"-{days} days", limit)
        ).fetchall()
    return [{"user_id": r["user_id"], "count": r["count"]} for r in rows]


def get_ai_hourly_volume(guild_id: int, hours: int = 24) -> list[dict]:
    """Volume de chamadas de IA por hora — usado no dashboard de custo."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT strftime('%H', created_at) as hour, COUNT(*) as count "
            "FROM ai_quality WHERE guild_id=? AND created_at >= datetime('now', ?) "
            "GROUP BY hour ORDER BY hour",
            (guild_id, f"-{hours} hours")
        ).fetchall()
    return [{"hour": r["hour"], "count": r["count"]} for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# WARNS
# ─────────────────────────────────────────────────────────────────────────────

def add_warn(guild_id: int, user_id: int, reason: str, moderator: str) -> int:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO warns (guild_id,user_id,reason,moderator) VALUES (?,?,?,?)",
            (guild_id, user_id, reason, moderator)
        )
        total = conn.execute(
            "SELECT COUNT(*) FROM warns WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)
        ).fetchone()[0]
    return total


def get_warns(guild_id: int, user_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT reason, moderator, created_at FROM warns WHERE guild_id=? AND user_id=? ORDER BY id",
            (guild_id, user_id)
        ).fetchall()
    return [dict(r) for r in rows]


def clear_warns(guild_id: int, user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM warns WHERE guild_id=? AND user_id=?", (guild_id, user_id))

def remove_warn_by_id(guild_id: int, user_id: int, warn_index: int) -> bool:
    """Remove um aviso específico pelo número sequencial (1-based). Retorna True se removido."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM warns WHERE guild_id=? AND user_id=? ORDER BY id",
            (guild_id, user_id)
        ).fetchall()
        if not rows or warn_index < 1 or warn_index > len(rows):
            return False
        target_id = rows[warn_index - 1]["id"]
        conn.execute("DELETE FROM warns WHERE id=?", (target_id,))
    return True


# ─────────────────────────────────────────────────────────────────────────────
# MOD LOG
# ─────────────────────────────────────────────────────────────────────────────

def add_mod_log(guild_id: int, target_id: int, mod_id: int, action: str,
                reason: str = None, duration: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO mod_log (guild_id,target_id,mod_id,action,reason,duration) VALUES (?,?,?,?,?,?)",
            (guild_id, target_id, mod_id, action, reason, duration)
        )


def get_mod_log(guild_id: int, target_id: int, limit: int = 15) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM mod_log WHERE guild_id=? AND target_id=? ORDER BY id DESC LIMIT ?",
            (guild_id, target_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def get_mod_log_recent(guild_id: int, limit: int = 20) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM mod_log WHERE guild_id=? ORDER BY id DESC LIMIT ?",
            (guild_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# ANALYTICS EVENTS
# ─────────────────────────────────────────────────────────────────────────────

def log_event(guild_id: int, event_type: str, user_id: int = None, extra: str = None):
    """Registra qualquer evento genérico para análise posterior."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO analytics_events (guild_id,event_type,user_id,extra) VALUES (?,?,?,?)",
            (guild_id, event_type, user_id, extra)
        )


def log_command(guild_id: int, user_id: int, command_name: str, success: bool = True):
    """Registra uso de comando."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO command_usage (guild_id,user_id,command_name,success) VALUES (?,?,?,?)",
            (guild_id, user_id, command_name, int(success))
        )


def log_member_event(guild_id: int, user_id: int, event: str):
    """Registra entrada ou saída de membro."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO member_activity (guild_id,user_id,event) VALUES (?,?,?)",
            (guild_id, user_id, event)
        )


def get_analytics_summary(guild_id: int) -> dict:
    """Retorna resumo analítico do servidor."""
    with get_conn() as conn:
        # Membros entrados/saídos nos últimos 30 dias
        joins_30d = conn.execute(
            "SELECT COUNT(*) FROM member_activity WHERE guild_id=? AND event='join' AND created_at >= datetime('now','-30 days')",
            (guild_id,)
        ).fetchone()[0]
        leaves_30d = conn.execute(
            "SELECT COUNT(*) FROM member_activity WHERE guild_id=? AND event='leave' AND created_at >= datetime('now','-30 days')",
            (guild_id,)
        ).fetchone()[0]

        # Top 5 comandos mais usados
        top_commands = conn.execute(
            "SELECT command_name, COUNT(*) as uses FROM command_usage WHERE guild_id=? GROUP BY command_name ORDER BY uses DESC LIMIT 5",
            (guild_id,)
        ).fetchall()

        # Ações de moderação nos últimos 7 dias
        mod_7d = conn.execute(
            "SELECT action, COUNT(*) as count FROM mod_log WHERE guild_id=? AND created_at >= datetime('now','-7 days') GROUP BY action ORDER BY count DESC",
            (guild_id,)
        ).fetchall()

        # Total de warns ativos
        total_warns = conn.execute(
            "SELECT COUNT(*) FROM warns WHERE guild_id=?",
            (guild_id,)
        ).fetchone()[0]

        # Usuários únicos que usaram a IA hoje (filtrado pelo guild via analytics)
        # ai_history não tem guild_id — usa analytics_events como proxy
        ai_today = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM analytics_events WHERE guild_id=? AND event_type='ai_message' AND created_at >= datetime('now','start of day')",
            (guild_id,)
        ).fetchone()[0]

        # Total de perguntas IA nos últimos 7 dias (via analytics_events)
        ai_7d = conn.execute(
            "SELECT COUNT(*) FROM analytics_events WHERE guild_id=? AND event_type='ai_message' AND created_at >= datetime('now','-7 days')",
            (guild_id,)
        ).fetchone()[0]

        # Eventos de segurança (anti-nuke, anti-spam) nos últimos 7 dias
        security_events = conn.execute(
            "SELECT event_type, COUNT(*) as count FROM analytics_events WHERE guild_id=? AND created_at >= datetime('now','-7 days') GROUP BY event_type ORDER BY count DESC LIMIT 10",
            (guild_id,)
        ).fetchall()

        # Atividade por hora hoje
        activity_hourly = conn.execute(
            "SELECT strftime('%H', created_at) as hour, COUNT(*) as count FROM command_usage WHERE guild_id=? AND created_at >= datetime('now','start of day') GROUP BY hour ORDER BY hour",
            (guild_id,)
        ).fetchall()

    return {
        "joins_30d":       joins_30d,
        "leaves_30d":      leaves_30d,
        "net_growth_30d":  joins_30d - leaves_30d,
        "top_commands":    [{"name": r["command_name"], "uses": r["uses"]} for r in top_commands],
        "mod_actions_7d":  [{"action": r["action"], "count": r["count"]} for r in mod_7d],
        "total_warns":     total_warns,
        "ai_users_today":  ai_today,
        "ai_questions_7d": ai_7d,
        "security_events": [{"type": r["event_type"], "count": r["count"]} for r in security_events],
        "activity_hourly": [{"hour": r["hour"], "count": r["count"]} for r in activity_hourly],
    }


# ─────────────────────────────────────────────────────────────────────────────
# BACKUP REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

def register_backup(guild_id: int, filename: str, roles: int, channels: int,
                    categories: int, file_size_kb: float, created_by: int):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO backup_registry
            (guild_id,filename,roles_count,channels_count,categories_count,file_size_kb,created_by)
            VALUES (?,?,?,?,?,?,?)
        """, (guild_id, filename, roles, channels, categories, file_size_kb, created_by))


def mark_backup_restored(filename: str, restored_by: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE backup_registry SET restored_at=datetime('now'), restored_by=? WHERE filename=?",
            (restored_by, filename)
        )


def get_backup_history(guild_id: int, limit: int = 10) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM backup_registry WHERE guild_id=? ORDER BY id DESC LIMIT ?",
            (guild_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# FAQ DINÂMICO — queries para análise de perguntas frequentes
# ─────────────────────────────────────────────────────────────────────────────

def get_recent_user_messages(days: int = 7, limit: int = 200) -> list[str]:
    """
    Retorna as mensagens dos USUÁRIOS (role='user') dos últimos N dias.
    Usado pelo FAQ dinâmico para analisar o que as pessoas estão perguntando.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT content FROM ai_history
            WHERE role = 'user'
              AND created_at >= datetime('now', ?)
            ORDER BY id DESC
            LIMIT ?
            """,
            (f'-{days} days', limit)
        ).fetchall()
    return [r["content"] for r in rows if len(r["content"]) > 10]


def save_faq_suggestion(guild_id: int, suggestion: str, period_days: int):
    """Salva uma sugestão de FAQ gerada pela IA no banco."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO faq_suggestions (guild_id, suggestion, period_days) VALUES (?,?,?)",
            (guild_id, suggestion, period_days)
        )


def get_faq_suggestions(guild_id: int, limit: int = 5) -> list[dict]:
    """Retorna as últimas sugestões de FAQ geradas."""
    with get_conn() as conn:
        try:
            rows = conn.execute(
                "SELECT suggestion, period_days, created_at FROM faq_suggestions WHERE guild_id=? ORDER BY id DESC LIMIT ?",
                (guild_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []


# ─────────────────────────────────────────────────────────────────────────────
# ESTADO PERSISTENTE — chave/valor genérico (usado para maintenance_mode etc.)
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_state_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

def set_state(key: str, value: str):
    with get_conn() as conn:
        _ensure_state_table(conn)
        conn.execute(
            "INSERT INTO bot_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )

def get_state(key: str, default: str = "") -> str:
    with get_conn() as conn:
        _ensure_state_table(conn)
        row = conn.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


# ─────────────────────────────────────────────────────────────────────────────
# PURGE GLOBAL DE ai_history ANTIGA (evita crescimento indefinido da tabela)
# ─────────────────────────────────────────────────────────────────────────────

def purge_old_ai_history(days: int = 30) -> int:
    """Remove mensagens de ai_history mais antigas que N dias. Retorna quantas linhas deletadas."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM ai_history WHERE created_at < datetime('now', ?)",
            (f'-{days} days',)
        )
    return cur.rowcount


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG DO SERVIDOR (usado por safety, word_filter, anti_raid, owner)
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_config_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS server_config (
            guild_id   INTEGER NOT NULL,
            key        TEXT NOT NULL,
            value      TEXT NOT NULL,
            PRIMARY KEY (guild_id, key)
        )
    """)

def get_config(guild_id: int, key: str, default: str = "") -> str:
    with get_conn() as conn:
        _ensure_config_table(conn)
        row = conn.execute(
            "SELECT value FROM server_config WHERE guild_id=? AND key=?",
            (guild_id, key)
        ).fetchone()
    return row["value"] if row else default

def set_config(guild_id: int, key: str, value: str):
    with get_conn() as conn:
        _ensure_config_table(conn)
        conn.execute(
            "INSERT INTO server_config(guild_id,key,value) VALUES(?,?,?) "
            "ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value",
            (guild_id, key, value)
        )

# ─────────────────────────────────────────────────────────────────────────────
# WORD FILTER (usado por word_filter.py)
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_word_filter_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS word_filter (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   INTEGER NOT NULL,
            word       TEXT NOT NULL,
            UNIQUE(guild_id, word)
        )
    """)

def get_word_filter(guild_id: int) -> list[str]:
    with get_conn() as conn:
        _ensure_word_filter_table(conn)
        rows = conn.execute(
            "SELECT word FROM word_filter WHERE guild_id=? ORDER BY word",
            (guild_id,)
        ).fetchall()
    return [r["word"] for r in rows]

def add_word_filter(guild_id: int, word: str) -> bool:
    """Adiciona palavra ao filtro. Retorna True se adicionada, False se já existia."""
    try:
        with get_conn() as conn:
            _ensure_word_filter_table(conn)
            cur = conn.execute(
                "INSERT OR IGNORE INTO word_filter(guild_id, word) VALUES(?,?)",
                (guild_id, word.lower().strip())
            )
        return cur.rowcount > 0
    except Exception:
        return False

def remove_word_filter(guild_id: int, word: str) -> bool:
    """Remove palavra do filtro. Retorna True se removida."""
    with get_conn() as conn:
        _ensure_word_filter_table(conn)
        cur = conn.execute(
            "DELETE FROM word_filter WHERE guild_id=? AND word=?",
            (guild_id, word.lower().strip())
        )
    return cur.rowcount > 0

# ─────────────────────────────────────────────────────────────────────────────
# ANTI-RAID MODE (usado por anti_raid.py)
# ─────────────────────────────────────────────────────────────────────────────

def get_raid_mode(guild_id: int) -> bool:
    """Retorna True se o modo anti-raid está ativo para o servidor."""
    return get_config(guild_id, "raid_mode", "false").lower() == "true"

def set_raid_mode(guild_id: int, active: bool):
    """Ativa ou desativa o modo anti-raid."""
    set_config(guild_id, "raid_mode", "true" if active else "false")

# ─────────────────────────────────────────────────────────────────────────────
# WHITELIST ANTI-NUKE (usado por owner.py)
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_whitelist_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nuke_whitelist (
            guild_id INTEGER NOT NULL,
            user_id  INTEGER NOT NULL,
            added_by INTEGER,
            added_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (guild_id, user_id)
        )
    """)

def add_to_whitelist(guild_id: int, user_id: int, added_by: int = 0) -> bool:
    try:
        with get_conn() as conn:
            _ensure_whitelist_table(conn)
            conn.execute(
                "INSERT OR IGNORE INTO nuke_whitelist(guild_id,user_id,added_by) VALUES(?,?,?)",
                (guild_id, user_id, added_by)
            )
        # Atualiza o set em memória
        try:
            from utils.cache import NUKE_WHITELIST
            NUKE_WHITELIST.add(user_id)
        except Exception:
            pass
        return True
    except Exception:
        return False

def remove_from_whitelist(guild_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        _ensure_whitelist_table(conn)
        cur = conn.execute(
            "DELETE FROM nuke_whitelist WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)
        )
    try:
        from utils.cache import NUKE_WHITELIST
        NUKE_WHITELIST.discard(user_id)
    except Exception:
        pass
    return cur.rowcount > 0

def get_whitelist(guild_id: int) -> list[int]:
    with get_conn() as conn:
        _ensure_whitelist_table(conn)
        rows = conn.execute(
            "SELECT user_id FROM nuke_whitelist WHERE guild_id=?",
            (guild_id,)
        ).fetchall()
    return [r["user_id"] for r in rows]

def load_whitelist_to_cache(guild_id: int):
    """Carrega whitelist do banco para o set em memória (chamado no on_ready)."""
    try:
        from utils.cache import NUKE_WHITELIST
        for uid in get_whitelist(guild_id):
            NUKE_WHITELIST.add(uid)
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# SELF-ROLE PANELS (usado por roles.py)
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_selfrole_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS selfrole_panels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    INTEGER NOT NULL,
            channel_id  INTEGER NOT NULL,
            message_id  INTEGER NOT NULL UNIQUE,
            title       TEXT NOT NULL,
            description TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS selfrole_buttons (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            panel_id   INTEGER NOT NULL REFERENCES selfrole_panels(id) ON DELETE CASCADE,
            role_id    INTEGER NOT NULL,
            label      TEXT NOT NULL,
            emoji      TEXT,
            style      TEXT DEFAULT 'secondary'
        )
    """)

def create_selfrole_panel(guild_id: int, channel_id: int, message_id: int,
                           title: str, description: str = "") -> int:
    with get_conn() as conn:
        _ensure_selfrole_tables(conn)
        cur = conn.execute(
            "INSERT INTO selfrole_panels(guild_id,channel_id,message_id,title,description) VALUES(?,?,?,?,?)",
            (guild_id, channel_id, message_id, title, description)
        )
    return cur.lastrowid

def add_selfrole_button(panel_id: int, role_id: int, label: str,
                         emoji: str = "", style: str = "secondary"):
    with get_conn() as conn:
        _ensure_selfrole_tables(conn)
        conn.execute(
            "INSERT INTO selfrole_buttons(panel_id,role_id,label,emoji,style) VALUES(?,?,?,?,?)",
            (panel_id, role_id, label, emoji, style)
        )

def get_selfrole_panels(guild_id: int) -> list[dict]:
    with get_conn() as conn:
        _ensure_selfrole_tables(conn)
        panels = conn.execute(
            "SELECT * FROM selfrole_panels WHERE guild_id=? ORDER BY id DESC",
            (guild_id,)
        ).fetchall()
        result = []
        for p in panels:
            buttons = conn.execute(
                "SELECT * FROM selfrole_buttons WHERE panel_id=?", (p["id"],)
            ).fetchall()
            result.append({"panel": dict(p), "buttons": [dict(b) for b in buttons]})
    return result

def get_selfrole_panel_by_message(message_id: int) -> dict | None:
    with get_conn() as conn:
        _ensure_selfrole_tables(conn)
        p = conn.execute(
            "SELECT * FROM selfrole_panels WHERE message_id=?", (message_id,)
        ).fetchone()
        if not p:
            return None
        buttons = conn.execute(
            "SELECT * FROM selfrole_buttons WHERE panel_id=?", (p["id"],)
        ).fetchall()
    return {"panel": dict(p), "buttons": [dict(b) for b in buttons]}

def delete_selfrole_panel(message_id: int) -> bool:
    """Remove painel pelo message_id do Discord. Retorna True se removido."""
    with get_conn() as conn:
        _ensure_selfrole_tables(conn)
        cur = conn.execute("DELETE FROM selfrole_panels WHERE message_id=?", (message_id,))
    return cur.rowcount > 0

# ─────────────────────────────────────────────────────────────────────────────
# GIVEAWAY (usado por giveaway.py)
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_giveaway_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS giveaways (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id     INTEGER NOT NULL,
            channel_id   INTEGER NOT NULL,
            message_id   INTEGER NOT NULL UNIQUE,
            prize        TEXT NOT NULL,
            host_id      INTEGER NOT NULL,
            ends_at      TEXT NOT NULL,
            winners_count INTEGER DEFAULT 1,
            ended        INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS giveaway_entries (
            giveaway_id INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            entered_at  TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (giveaway_id, user_id)
        )
    """)

def create_giveaway(guild_id: int, channel_id: int, message_id: int,
                     prize: str, host_id: int, ends_at: str, winners_count: int = 1) -> int:
    with get_conn() as conn:
        _ensure_giveaway_tables(conn)
        cur = conn.execute(
            "INSERT INTO giveaways(guild_id,channel_id,message_id,prize,host_id,ends_at,winners_count) "
            "VALUES(?,?,?,?,?,?,?)",
            (guild_id, channel_id, message_id, prize, host_id, ends_at, winners_count)
        )
    return cur.lastrowid

def get_active_giveaways(guild_id: int = None) -> list[dict]:
    with get_conn() as conn:
        _ensure_giveaway_tables(conn)
        if guild_id:
            rows = conn.execute(
                "SELECT * FROM giveaways WHERE ended=0 AND guild_id=? ORDER BY ends_at",
                (guild_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM giveaways WHERE ended=0 ORDER BY ends_at"
            ).fetchall()
    return [dict(r) for r in rows]

def get_giveaway_by_message(message_id: int) -> dict | None:
    with get_conn() as conn:
        _ensure_giveaway_tables(conn)
        row = conn.execute(
            "SELECT * FROM giveaways WHERE message_id=?", (message_id,)
        ).fetchone()
    return dict(row) if row else None

def end_giveaway(message_id: int) -> list[int]:
    """Marca sorteio como encerrado e retorna lista de IDs dos participantes."""
    with get_conn() as conn:
        _ensure_giveaway_tables(conn)
        row = conn.execute(
            "SELECT id FROM giveaways WHERE message_id=?", (message_id,)
        ).fetchone()
        if not row:
            return []
        gid = row["id"]
        conn.execute("UPDATE giveaways SET ended=1 WHERE id=?", (gid,))
        entries = conn.execute(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (gid,)
        ).fetchall()
    return [r["user_id"] for r in entries]

def add_giveaway_entry(message_id: int, user_id: int) -> bool:
    """Adiciona participante ao sorteio. Retorna False se já participou (PRIMARY KEY conflict)."""
    with get_conn() as conn:
        _ensure_giveaway_tables(conn)
        row = conn.execute(
            "SELECT id FROM giveaways WHERE message_id=?", (message_id,)
        ).fetchone()
        if not row:
            return False
        existing = conn.execute(
            "SELECT 1 FROM giveaway_entries WHERE giveaway_id=? AND user_id=?",
            (row["id"], user_id)
        ).fetchone()
        if existing:
            return False  # já participava
        conn.execute(
            "INSERT INTO giveaway_entries(giveaway_id,user_id) VALUES(?,?)",
            (row["id"], user_id)
        )
        return True

def remove_giveaway_entry(message_id: int, user_id: int) -> bool:
    """Remove participante do sorteio (toggle out). Retorna True se removido."""
    with get_conn() as conn:
        _ensure_giveaway_tables(conn)
        row = conn.execute(
            "SELECT id FROM giveaways WHERE message_id=?", (message_id,)
        ).fetchone()
        if not row:
            return False
        cur = conn.execute(
            "DELETE FROM giveaway_entries WHERE giveaway_id=? AND user_id=?",
            (row["id"], user_id)
        )
        return cur.rowcount > 0

def get_giveaway_entry_count(message_id: int) -> int:
    """Retorna número de participantes cadastrados no sorteio."""
    with get_conn() as conn:
        _ensure_giveaway_tables(conn)
        row = conn.execute(
            "SELECT id FROM giveaways WHERE message_id=?", (message_id,)
        ).fetchone()
        if not row:
            return 0
        return conn.execute(
            "SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id=?", (row["id"],)
        ).fetchone()[0]

def get_giveaway_entries(message_id: int) -> list[int]:
    """Retorna lista de user_ids dos participantes de um sorteio (inclusive encerrado)."""
    with get_conn() as conn:
        _ensure_giveaway_tables(conn)
        row = conn.execute(
            "SELECT id FROM giveaways WHERE message_id=?", (message_id,)
        ).fetchone()
        if not row:
            return []
        entries = conn.execute(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (row["id"],)
        ).fetchall()
    return [r["user_id"] for r in entries]

# ─────────────────────────────────────────────────────────────────────────────
# MÉTRICAS AVANÇADAS (Item 6)
# ─────────────────────────────────────────────────────────────────────────────

def get_daily_summary(guild_id: int) -> dict:
    """Retorna um resumo das últimas 24h para o dashboard."""
    with get_conn() as conn:
        # Atendimentos totais (IA + Quality)
        row = conn.execute(
            "SELECT COUNT(*) as total, SUM(outcome='resolved') as resolved FROM ai_quality WHERE guild_id=? AND created_at >= datetime('now','-1 day')",
            (guild_id,)
        ).fetchone()
        
        # Comandos mais usados
        cmds = conn.execute(
            "SELECT command_name, COUNT(*) as count FROM command_usage WHERE guild_id=? AND created_at >= datetime('now','-1 day') GROUP BY command_name ORDER BY count DESC LIMIT 3",
            (guild_id,)
        ).fetchall()
        
        # Novos membros
        members = conn.execute(
            "SELECT COUNT(*) FROM member_activity WHERE guild_id=? AND event='join' AND created_at >= datetime('now','-1 day')",
            (guild_id,)
        ).fetchone()[0]

    total = row["total"] or 0
    resolved = row["resolved"] or 0
    rate = round((resolved / total) * 100, 1) if total else 0
    
    return {
        "total_24h": total,
        "resolved_24h": resolved,
        "rate_24h": rate,
        "top_commands": [{"name": c["command_name"], "count": c["count"]} for c in cmds],
        "new_members_24h": members
    }
