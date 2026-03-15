"""
cogs/health_check.py  ─  v5  (NOVO)
──────────────────────────────────────
Sistema de Health Check & Monitoramento — Darke Store Bot

Funcionalidades:
  ✅ .status aprimorado — RAM, CPU, latência, uptime, cogs carregados
  ✅ .cogstatus — status detalhado de cada cog (carregado/erro)
  ✅ .ping — latência detalhada (WebSocket + API round-trip)
  ✅ Monitor de latência em background — alerta o dono se latência > threshold
  ✅ Monitor de cogs — detecta cog que descarregou inesperadamente e tenta recarregar
  ✅ Monitor de DB — verifica integridade do banco a cada hora
  ✅ .reloadall — recarrega todos os cogs sem reiniciar o bot
"""

import discord
from discord.ext import commands, tasks
import time
import asyncio
import os
import sqlite3

from utils.logger import log
from utils.cache import OWNER_ID, config
from utils.database import DB_PATH
from utils.constants import REQUIRED_COGS

# ── Threshold padrão para alerta de latência (ms) ────────────────────────────
LATENCY_WARN_MS  = 400   # aviso
LATENCY_CRIT_MS  = 800   # crítico — DM para o dono


def _get_memory_mb() -> float:
    """Retorna uso de memória do processo em MB (sem dependências externas)."""
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    return 0.0


def _get_db_size_kb(path: str = DB_PATH) -> float:
    try:
        return os.path.getsize(path) / 1024
    except Exception:
        return 0.0


def _check_db_integrity(path: str = DB_PATH) -> tuple[bool, str]:
    """Executa PRAGMA integrity_check no banco."""
    try:
        conn = sqlite3.connect(path)
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        return (result == "ok"), result
    except Exception as e:
        return False, str(e)


def _latency_color(ms: int) -> discord.Color:
    if ms < 150:  return discord.Color.green()
    if ms < 400:  return discord.Color.yellow()
    if ms < 800:  return discord.Color.orange()
    return discord.Color.red()


def _latency_icon(ms: int) -> str:
    if ms < 150:  return "🟢"
    if ms < 400:  return "🟡"
    if ms < 800:  return "🟠"
    return "🔴"


class HealthCheck(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot         = bot
        self._start_time = time.time()
        self._cog_errors: dict[str, str] = {}      # cog_name → último erro
        self._last_latency_alert = 0.0
        self._cog_retry_after: dict[str, float] = {}  # Fix #10 — cooldown de retry por cog
        self._owner_cache: discord.User | None = None  # Fix #17 — cache do owner
        self._COG_RETRY_COOLDOWN = 600  # 10min entre retries do watchdog

        self.latency_monitor.start()
        self.cog_watchdog.start()
        self.db_integrity_check.start()

    def cog_unload(self):
        self.latency_monitor.cancel()
        self.cog_watchdog.cancel()
        self.db_integrity_check.cancel()

    # ── Uptime helper ─────────────────────────────────────────────────────────
    def _uptime_str(self) -> str:
        delta = int(time.time() - self._start_time)
        d, rem = divmod(delta, 86400)
        h, rem = divmod(rem, 3600)
        m, s   = divmod(rem, 60)
        parts  = []
        if d: parts.append(f"{d}d")
        if h: parts.append(f"{h}h")
        if m: parts.append(f"{m}m")
        parts.append(f"{s}s")
        return " ".join(parts)

    # ── Background: monitor de latência ──────────────────────────────────────
    @tasks.loop(seconds=60)
    async def latency_monitor(self):
        ms = round(self.bot.latency * 1000)
        if ms >= LATENCY_CRIT_MS:
            # Anti-spam: só alerta a cada 10 minutos
            now = time.time()
            if now - self._last_latency_alert < 600:
                return
            self._last_latency_alert = now

            owner = await self._get_owner()
            if owner:
                try:
                    embed = discord.Embed(
                        title="🔴 ALERTA CRÍTICO — Latência Alta",
                        description=(
                            f"A latência do bot atingiu **{ms}ms** (crítico: ≥{LATENCY_CRIT_MS}ms).\n\n"
                            "Isso pode indicar instabilidade nos servidores do Discord ou sobrecarga local.\n"
                            "Use `.ping` para monitorar em tempo real."
                        ),
                        color=discord.Color.red()
                    )
                    embed.set_footer(text=f"Uptime: {self._uptime_str()}")
                    await owner.send(embed=embed)
                    log.warning(f"[HealthCheck] Alerta de latência crítica enviado ao dono ({ms}ms)")
                except Exception:
                    pass

    @latency_monitor.before_loop
    async def before_latency(self):
        await self.bot.wait_until_ready()

    # ── Background: watchdog de cogs ─────────────────────────────────────────
    @tasks.loop(minutes=5)
    async def cog_watchdog(self):
        for ext in REQUIRED_COGS:
            cog_name = ext.split(".")[-1].replace("_", " ").title().replace(" ", "")
            # Se o cog sumiu do bot, tenta recarregar
            loaded_names = [c.__class__.__name__.lower() for c in self.bot.cogs.values()]
            # Verifica pela extensão carregada
            if ext not in self.bot.extensions:
                # Fix #10 — respeita cooldown de retry para cogs com erro permanente
                now = time.time()
                retry_after = self._cog_retry_after.get(ext, 0)
                if now < retry_after:
                    continue  # ainda no cooldown, pula esta iteração

                log.warning(f"[Watchdog] Cog {ext} não está carregado! Tentando recarregar...")
                try:
                    await self.bot.load_extension(ext)
                    log.info(f"[Watchdog] {ext} recarregado com sucesso.")
                    self._cog_errors.pop(ext, None)
                    self._cog_retry_after.pop(ext, None)

                    owner = await self._get_owner()
                    if owner:
                        try:
                            await owner.send(
                                f"⚠️ **Watchdog:** O cog `{ext}` descarregou inesperadamente e foi **recarregado automaticamente**."
                            )
                        except Exception:
                            pass
                except Exception as e:
                    self._cog_errors[ext] = str(e)
                    self._cog_retry_after[ext] = now + self._COG_RETRY_COOLDOWN
                    log.error(f"[Watchdog] Falha ao recarregar {ext}: {e} (próxima tentativa em {self._COG_RETRY_COOLDOWN//60}min)")

    @cog_watchdog.before_loop
    async def before_watchdog(self):
        await self.bot.wait_until_ready()

    # ── Background: integridade do banco ─────────────────────────────────────
    @tasks.loop(hours=1)
    async def db_integrity_check(self):
        ok, result = _check_db_integrity()
        if not ok:
            log.error(f"[HealthCheck] Integridade do banco FALHOU: {result}")
            owner = await self._get_owner()
            if owner:
                try:
                    await owner.send(
                        f"🚨 **ALERTA DB:** O banco de dados apresentou problema de integridade!\n"
                        f"```{result[:500]}```\nFaça um backup imediatamente com `.backup`."
                    )
                except Exception:
                    pass
        else:
            log.debug("[HealthCheck] Integridade do banco: OK")  # Fix #8 — debug nível para não poluir logs

    @db_integrity_check.before_loop
    async def before_db_check(self):
        await self.bot.wait_until_ready()

    async def _get_owner(self) -> discord.User | None:
        """Fix #17 — cacheia o objeto User para evitar chamadas de API repetidas."""
        if not OWNER_ID:
            return None
        if self._owner_cache:
            return self._owner_cache
        try:
            self._owner_cache = await self.bot.fetch_user(int(OWNER_ID))
            return self._owner_cache
        except Exception:
            return None

    # ── Comandos ──────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="ping", description="Mostra a latência detalhada do bot.")
    async def ping(self, ctx: commands.Context):
        ws_ms = round(self.bot.latency * 1000)
        icon  = _latency_icon(ws_ms)

        # Mede round-trip da API
        t0  = time.perf_counter()
        msg = await ctx.send("📡 Medindo...", ephemeral=True)
        api_ms = round((time.perf_counter() - t0) * 1000)

        color = _latency_color(max(ws_ms, api_ms))
        embed = discord.Embed(title="📡 Latência", color=color)
        embed.add_field(name=f"{icon} WebSocket", value=f"**{ws_ms}ms**", inline=True)
        embed.add_field(name="⚡ API Round-trip", value=f"**{api_ms}ms**", inline=True)
        embed.add_field(name="⏱️ Uptime", value=self._uptime_str(), inline=True)

        status_text = "Excelente" if ws_ms < 150 else ("Boa" if ws_ms < 400 else ("Lenta" if ws_ms < 800 else "⚠️ Crítica"))
        embed.set_footer(text=f"Conexão: {status_text}")
        await msg.edit(content=None, embed=embed)

    @commands.hybrid_command(name="healthcheck", description="Status completo do sistema (Apenas Dono).")
    async def healthcheck(self, ctx: commands.Context):
        if str(ctx.author.id) != str(OWNER_ID):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)

        await ctx.defer(ephemeral=True)

        ws_ms  = round(self.bot.latency * 1000)
        mem_mb = _get_memory_mb()
        db_kb  = _get_db_size_kb()
        ok_db, db_result = _check_db_integrity()

        # Status geral
        issues = []
        if ws_ms >= LATENCY_CRIT_MS:  issues.append(f"🔴 Latência crítica ({ws_ms}ms)")
        if ws_ms >= LATENCY_WARN_MS:  issues.append(f"🟡 Latência elevada ({ws_ms}ms)")
        if not ok_db:                 issues.append(f"🔴 Banco com problema: {db_result}")
        if self._cog_errors:          issues.append(f"⚠️ {len(self._cog_errors)} cog(s) com erro")

        overall = "🟢 Saudável" if not issues else ("🟡 Atenção" if len(issues) == 1 else "🔴 Problemas detectados")

        embed = discord.Embed(
            title="🏥 Health Check — Darke Store Bot",
            description=f"**Status Geral:** {overall}",
            color=discord.Color.green() if not issues else (discord.Color.yellow() if len(issues) == 1 else discord.Color.red())
        )

        # Conexão
        embed.add_field(
            name="📡 Conexão",
            value=(
                f"{_latency_icon(ws_ms)} WebSocket: **{ws_ms}ms**\n"
                f"🌐 Servidores: **{len(self.bot.guilds)}**\n"
                f"👥 Membros: **{sum(g.member_count for g in self.bot.guilds):,}**"
            ),
            inline=True
        )

        # Sistema
        embed.add_field(
            name="💻 Sistema",
            value=(
                f"🧠 RAM: **{mem_mb:.1f} MB**\n"
                f"🗄️ Banco: **{db_kb:.1f} KB** {'✅' if ok_db else '❌'}\n"
                f"⏱️ Uptime: **{self._uptime_str()}**"
            ),
            inline=True
        )

        # Cogs
        total_cogs    = len(self.bot.extensions)
        required_ok   = sum(1 for e in REQUIRED_COGS if e in self.bot.extensions)
        required_fail = len(REQUIRED_COGS) - required_ok
        embed.add_field(
            name="🧩 Cogs",
            value=(
                f"✅ Carregados: **{total_cogs}**\n"
                f"🔴 Críticos faltando: **{required_fail}**\n"
                f"⚠️ Com erro: **{len(self._cog_errors)}**"
            ),
            inline=True
        )

        # Alertas ativos
        if issues:
            embed.add_field(
                name="⚠️ Alertas Ativos",
                value="\n".join(issues),
                inline=False
            )

        embed.set_footer(text="Use .cogstatus para detalhes dos cogs | .reloadall para recarregar tudo")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="cogstatus", description="Status detalhado de cada cog (Apenas Dono).")
    async def cogstatus(self, ctx: commands.Context):
        if str(ctx.author.id) != str(OWNER_ID):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)

        embed = discord.Embed(title="🧩 Status dos Cogs", color=discord.Color.blurple())
        lines = []
        for ext in sorted(self.bot.extensions.keys()):
            short = ext.replace("cogs.", "")
            is_req = ext in REQUIRED_COGS
            req_icon = "⭐" if is_req else "  "
            lines.append(f"{req_icon} ✅ `{short}`")

        # Cogs que deveriam estar mas não estão
        missing = [e for e in REQUIRED_COGS if e not in self.bot.extensions]
        for ext in missing:
            short = ext.replace("cogs.", "")
            err   = self._cog_errors.get(ext, "não carregado")
            lines.append(f"⭐ 🔴 `{short}` — {err[:60]}")

        embed.description = "\n".join(lines)
        embed.set_footer(text="⭐ = cog crítico | Use .reload <cog> para recarregar individualmente")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.command(name="reloadall", hidden=True)
    async def reloadall(self, ctx: commands.Context):
        if str(ctx.author.id) != str(OWNER_ID):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)

        results = []
        failed  = []
        # Fix #15 — recarrega cogs críticos de infraestrutura por último
        _LAST = {"cogs.events", "cogs.health_check"}
        all_exts = list(self.bot.extensions.keys())
        exts = [e for e in all_exts if e not in _LAST] + [e for e in all_exts if e in _LAST]

        for ext in exts:
            try:
                await self.bot.reload_extension(ext)
                results.append(f"✅ `{ext.replace('cogs.', '')}`")
            except Exception as e:
                failed.append(f"❌ `{ext.replace('cogs.', '')}`: {str(e)[:50]}")

        embed = discord.Embed(
            title=f"🔄 Reload Completo — {len(results)}/{len(exts)} cogs",
            color=discord.Color.green() if not failed else discord.Color.orange()
        )
        if results:
            embed.add_field(name="✅ Sucesso", value="\n".join(results[:20]), inline=False)
        if failed:
            embed.add_field(name="❌ Falhas", value="\n".join(failed), inline=False)
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(HealthCheck(bot))
