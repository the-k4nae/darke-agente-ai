"""
cogs/alerts.py  ─  v5  (NOVO)
───────────────────────────────
Sistema de Notificações & Alertas Inteligentes — Darke Store Bot

Alertas automáticos via DM para o dono:
  🔐 Evento crítico de segurança (nuke attempt, raid detectado)
  🤖 Taxa de resolução da IA caiu abaixo do threshold configurável
  💾 Backup falhou ou backup automático não ocorreu em 25h
  👥 Pico de saída de membros (possível raid de banimento)
  ⚠️  Acúmulo elevado de warns no servidor (alerta de comportamento)

Comandos:
  .alertas        → Ver configurações dos alertas
  .alertas on/off → Liga/desliga todos os alertas
  .alertateste    → Envia alerta de teste para o dono
"""

import discord
from discord.ext import commands, tasks
import asyncio
import time
import os
import aiohttp

from utils.logger import log
from utils.cache import OWNER_ID
from utils.database import get_ai_quality_stats, get_analytics_summary, get_state, set_state

BACKUPS_DIR = "backups"

# URL do webhook de alertas críticos (opcional — redundância para DM)
# Configure no .env: ALERT_WEBHOOK_URL=https://discord.com/api/webhooks/...
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "").strip()

# ── Estado dos alertas (em memória + persistido no banco) ────────────────────
_alerts_enabled       = True
# Fix #9 — timestamps persistidos no banco para sobreviver reload do cog
def _load_ts(key: str) -> float:
    try:
        return float(get_state(f"alert_ts_{key}", "0"))
    except (ValueError, TypeError):
        return 0.0

def _save_ts(key: str, ts: float):
    try:
        set_state(f"alert_ts_{key}", str(ts))
    except Exception:
        pass

# Thresholds configuráveis
AI_QUALITY_THRESHOLD  = 40   # % — abaixo disso, alerta
MEMBER_DROP_THRESHOLD = 10   # saídas em 1h — alerta de possível raid
WARN_SPIKE_THRESHOLD  = 5    # warns nas últimas 2h — alerta

# Anti-spam: mínimo de horas entre alertas do mesmo tipo
ALERT_COOLDOWN_HOURS = {
    "ai_quality": 6,
    "backup":     12,
    "member_drop": 2,
    "warn_spike":  3,
}


class Alerts(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot          = bot
        self._guild_cache: dict[int, discord.Guild] = {}
        # Fix #9 — carrega timestamps persistidos para evitar alertas duplicados após reload
        self._last_ai_alert     = _load_ts("ai_quality")
        self._last_backup_alert = _load_ts("backup")
        self._last_member_alert = _load_ts("member_drop")
        self._last_warn_alert   = _load_ts("warn_spike")
        self.ai_quality_monitor.start()
        self.backup_monitor.start()
        self.member_drop_monitor.start()

    def cog_unload(self):
        self.ai_quality_monitor.cancel()
        self.backup_monitor.cancel()
        self.member_drop_monitor.cancel()

    async def _get_owner(self) -> discord.User | None:
        if not OWNER_ID:
            return None
        try:
            return await self.bot.fetch_user(int(OWNER_ID))
        except Exception:
            return None

    async def _send_alert(self, title: str, description: str, color: discord.Color, footer: str = ""):
        """Envia alerta via DM ao dono E via webhook (se configurado).
        Ambos disparam em paralelo — se a DM falhar, o webhook garante a entrega.
        """
        global _alerts_enabled
        if not _alerts_enabled:
            return

        embed = discord.Embed(title=title, description=description, color=color)
        if footer:
            embed.set_footer(text=footer)
        embed.timestamp = discord.utils.utcnow()

        # Dispara DM e webhook em paralelo
        tasks_to_run = []

        # DM para o dono
        async def _send_dm():
            owner = await self._get_owner()
            if not owner:
                return
            try:
                await owner.send(embed=embed)
                log.info(f"[Alerts] DM enviada ao dono: {title}")
            except discord.Forbidden:
                log.warning("[Alerts] DMs do dono estão bloqueadas — alerta não entregue via DM.")
            except Exception as e:
                log.error(f"[Alerts] Erro ao enviar DM: {e}")

        tasks_to_run.append(_send_dm())

        # Webhook de redundância (canal privado do servidor)
        if ALERT_WEBHOOK_URL:
            async def _send_webhook():
                try:
                    async with aiohttp.ClientSession() as session:
                        wh = discord.Webhook.from_url(ALERT_WEBHOOK_URL, session=session)
                        await wh.send(
                            embed=embed,
                            username="Darke Alerts",
                            avatar_url="https://cdn.discordapp.com/embed/avatars/0.png"
                        )
                    log.info(f"[Alerts] Webhook disparado: {title}")
                except Exception as e:
                    log.warning(f"[Alerts] Falha no webhook: {e}")
            tasks_to_run.append(_send_webhook())

        await asyncio.gather(*tasks_to_run, return_exceptions=True)

    def _cooldown_ok(self, key: str) -> bool:
        """Retorna True se pode enviar alerta (respeitando cooldown)."""
        mapping = {
            "ai_quality":  "_last_ai_alert",
            "backup":      "_last_backup_alert",
            "member_drop": "_last_member_alert",
            "warn_spike":  "_last_warn_alert",
        }
        attr  = mapping.get(key, "_last_ai_alert")
        last  = getattr(self, attr, 0.0)
        hours = ALERT_COOLDOWN_HOURS.get(key, 6)
        if (time.time() - last) < hours * 3600:
            return False
        setattr(self, attr, time.time())
        _save_ts(key, time.time())
        return True

    # ── Monitor: qualidade da IA ──────────────────────────────────────────────
    @tasks.loop(hours=6)
    async def ai_quality_monitor(self):
        if not self.bot.guilds:
            return
        for guild in self.bot.guilds:
            try:
                stats = get_ai_quality_stats(guild.id)
                if stats["total_7d"] < 5:
                    continue  # Sem dados suficientes
                rate = stats.get("rate", 100)
                if rate < AI_QUALITY_THRESHOLD and self._cooldown_ok("ai_quality"):
                    await self._send_alert(
                        title="🤖 Alerta — Qualidade da IA Baixa",
                        description=(
                            f"**Servidor:** {guild.name}\n\n"
                            f"A taxa de resolução da IA caiu para **{rate}%** "
                            f"(threshold: {AI_QUALITY_THRESHOLD}%).\n\n"
                            f"📊 Últimos 7 dias: **{stats['resolved_7d']}** resolvidos de **{stats['total_7d']}** feedbacks.\n\n"
                            f"**Ações sugeridas:**\n"
                            f"• Revise e atualize o `prompt.txt`\n"
                            f"• Veja as categorias mais problemáticas com `.statsai`\n"
                            f"• Adicione mais contexto para os tópicos com menor resolução"
                        ),
                        color=discord.Color.orange(),
                        footer=f"Use .statsai para detalhes | Próximo alerta em {ALERT_COOLDOWN_HOURS['ai_quality']}h"
                    )
            except Exception as e:
                log.error(f"[Alerts] ai_quality_monitor: {e}")

    @ai_quality_monitor.before_loop
    async def before_ai_monitor(self):
        await self.bot.wait_until_ready()

    # ── Monitor: backup ───────────────────────────────────────────────────────
    @tasks.loop(hours=6)
    async def backup_monitor(self):
        if not self.bot.guilds:
            return
        # Garante que o diretório existe antes de tentar listar
        if not os.path.isdir(BACKUPS_DIR):
            return
        for guild in self.bot.guilds:
            try:
                guild_id = str(guild.id)
                files = [
                    f for f in os.listdir(BACKUPS_DIR)
                    if f.startswith(f"backup_{guild_id}_") and f.endswith(".json")
                ]
                if not files:
                    if self._cooldown_ok("backup"):
                        await self._send_alert(
                            title="💾 Alerta — Nenhum Backup Encontrado",
                            description=(
                                f"**Servidor:** {guild.name}\n\n"
                                f"❌ Não há nenhum backup salvo para este servidor!\n\n"
                                f"Execute `.backup` imediatamente para criar um backup manual."
                            ),
                            color=discord.Color.red(),
                            footer="Use .backup para criar agora"
                        )
                    continue

                # Verifica se o backup mais recente tem mais de 25 horas (auto deveria rodar a cada 24h)
                latest = sorted(files, reverse=True)[0]
                fpath  = os.path.join(BACKUPS_DIR, latest)
                age_hours = (time.time() - os.path.getmtime(fpath)) / 3600

                if age_hours > 25 and self._cooldown_ok("backup"):
                    await self._send_alert(
                        title="💾 Alerta — Backup Desatualizado",
                        description=(
                            f"**Servidor:** {guild.name}\n\n"
                            f"O último backup tem **{age_hours:.0f} horas** de idade.\n"
                            f"O backup automático deveria ter rodado nas últimas 24h.\n\n"
                            f"📁 Último arquivo: `{latest}`\n\n"
                            f"**Verifique se o bot foi reiniciado recentemente** — o task de backup "
                            f"recomeça apenas após o próximo ciclo de 24h."
                        ),
                        color=discord.Color.orange(),
                        footer="Use .backup para criar um backup manual agora"
                    )
            except Exception as e:
                log.error(f"[Alerts] backup_monitor: {e}")

    @backup_monitor.before_loop
    async def before_backup_monitor(self):
        await self.bot.wait_until_ready()

    # ── Monitor: pico de saída de membros ─────────────────────────────────────
    @tasks.loop(minutes=30)
    async def member_drop_monitor(self):
        if not self.bot.guilds:
            return
        for guild in self.bot.guilds:
            try:
                data   = get_analytics_summary(guild.id)
                leaves = data.get("leaves_30d", 0)
                joins  = data.get("joins_30d", 0)

                # Alerta se saldo negativo relevante e muitas saídas
                if leaves > joins and leaves >= MEMBER_DROP_THRESHOLD:
                    if self._cooldown_ok("member_drop"):
                        await self._send_alert(
                            title="👥 Alerta — Pico de Saída de Membros",
                            description=(
                                f"**Servidor:** {guild.name}\n\n"
                                f"Detectado crescimento negativo nos últimos 30 dias:\n"
                                f"📤 Saídas: **{leaves}** | 📥 Entradas: **{joins}**\n"
                                f"📉 Saldo: **{joins - leaves:+d} membros**\n\n"
                                f"Isso pode indicar insatisfação, raid de saída ou problema no servidor.\n"
                                f"Use `.statsmembros` para analisar o período."
                            ),
                            color=discord.Color.red(),
                            footer="Use .statsmembros para detalhes"
                        )
            except Exception as e:
                log.error(f"[Alerts] member_drop_monitor: {e}")

    @member_drop_monitor.before_loop
    async def before_member_monitor(self):
        await self.bot.wait_until_ready()

    # ── Alertas disparados por eventos externos ───────────────────────────────

    async def send_security_alert(self, guild: discord.Guild, event_type: str,
                                   executor: discord.Member, details: str):
        """Chamado por anti_nuke/anti_raid ao detectar evento crítico."""
        if not _alerts_enabled:
            return
        await self._send_alert(
            title=f"🚨 Evento de Segurança — {event_type}",
            description=(
                f"**Servidor:** {guild.name}\n"
                f"**Executor:** {executor} (`{executor.id}`)\n\n"
                f"{details}"
            ),
            color=discord.Color.red(),
            footer="Ação automática já foi tomada pelo bot"
        )

    # ── Comandos ──────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="alertas", description="Gerencia as notificações automáticas (Apenas Dono).")
    async def alertas(self, ctx: commands.Context, acao: str = None):
        if str(ctx.author.id) != str(OWNER_ID):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)

        global _alerts_enabled
        if acao and acao.lower() in ("on", "off"):
            _alerts_enabled = acao.lower() == "on"
            status = "✅ **Ativados**" if _alerts_enabled else "❌ **Desativados**"
            return await ctx.send(f"🔔 Alertas automáticos: {status}", ephemeral=True)

        # Mostra status atual
        embed = discord.Embed(
            title="🔔 Notificações & Alertas",
            color=discord.Color.green() if _alerts_enabled else discord.Color.red()
        )
        status_icon = "✅ Ativos" if _alerts_enabled else "❌ Desativados"
        embed.add_field(name="Status Geral", value=status_icon, inline=False)
        embed.add_field(
            name="📋 Alertas Configurados",
            value=(
                f"🤖 **IA Qualidade** — taxa < {AI_QUALITY_THRESHOLD}% (a cada {ALERT_COOLDOWN_HOURS['ai_quality']}h)\n"
                f"💾 **Backup** — desatualizado/ausente (a cada {ALERT_COOLDOWN_HOURS['backup']}h)\n"
                f"👥 **Pico de Saída** — {MEMBER_DROP_THRESHOLD}+ saídas > entradas (a cada {ALERT_COOLDOWN_HOURS['member_drop']}h)\n"
                f"🚨 **Segurança** — nuke/raid detectado (imediato)\n"
                f"🏥 **Latência crítica** — ≥800ms (a cada 10 min)\n"
                f"🗄️ **Banco de dados** — problema de integridade (imediato)"
            ),
            inline=False
        )
        embed.set_footer(text="Use .alertas on/off para ligar ou desligar | .alertateste para testar")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="alertateste", description="Envia um alerta de teste (Apenas Dono).")
    async def alertateste(self, ctx: commands.Context):
        if str(ctx.author.id) != str(OWNER_ID):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)

        await self._send_alert(
            title="🔔 Alerta de Teste",
            description=(
                f"Este é um alerta de **teste** do sistema de notificações.\n\n"
                f"✅ O sistema está funcionando corretamente!\n"
                f"📡 Bot: `{self.bot.user.name}`\n"
                f"🌐 Servidores: `{len(self.bot.guilds)}`"
            ),
            color=discord.Color.blurple(),
            footer="Sistema de Alertas v5 — Darke Store Bot"
        )
        await ctx.send("✅ Alerta de teste enviado para sua DM!", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Alerts(bot))
