"""
cogs/analytics.py  ─  v4  (NOVO)
──────────────────────────────────
Estatísticas e Analytics — Darke Store Bot

Comandos:
  .stats          → Dashboard geral do servidor
  .statsmod       → Estatísticas de moderação (7 dias)
  .statsai        → Estatísticas da IA de suporte
  .statsmembros   → Crescimento de membros (30 dias)
  .statscomandos  → Ranking de comandos mais usados
"""

import discord
from discord.ext import commands
import time
from utils.cache import OWNER_ID
from utils.database import get_analytics_summary, get_ai_quality_stats, get_daily_summary
from utils.logger import log

# Fix #17 — cache simples de 60s para queries pesadas de analytics
_stats_cache: dict[int, tuple[float, dict, dict]] = {}  # guild_id → (ts, data, ai_data)
_STATS_TTL = 60  # segundos

def _get_stats_cached(guild_id: int) -> tuple[dict, dict]:
    """Retorna (data, ai_data) do cache se fresco, senão consulta o banco."""
    now = time.time()
    cached = _stats_cache.get(guild_id)
    if cached and (now - cached[0]) < _STATS_TTL:
        return cached[1], cached[2]
    data    = get_analytics_summary(guild_id)
    ai_data = get_ai_quality_stats(guild_id)
    _stats_cache[guild_id] = (now, data, ai_data)
    return data, ai_data


def _bar(value: int, total: int, width: int = 10) -> str:
    """Gera uma barra de progresso ASCII."""
    if total == 0:
        return "░" * width
    filled = round((value / total) * width)
    return "█" * filled + "░" * (width - filled)


import asyncio
from datetime import datetime, timezone, timedelta

class Analytics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._daily_report_task = None

    async def cog_load(self):
        self._daily_report_task = self.bot.loop.create_task(self._daily_report_loop())

    def cog_unload(self):
        if self._daily_report_task:
            self._daily_report_task.cancel()

    async def _daily_report_loop(self):
        """Envia um resumo diário para o dono às 09:00 da manhã."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.now(timezone.utc)
            # Agenda para as 09:00 UTC
            target = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            
            wait_secs = (target - now).total_seconds()
            log.info(f"[Analytics] Próximo resumo diário em {wait_secs/3600:.1f}h")
            await asyncio.sleep(wait_secs)
            
            await self._send_daily_report_to_owner()

    async def _send_daily_report_to_owner(self):
        try:
            owner = self.bot.get_user(int(OWNER_ID))
            if not owner:
                owner = await self.bot.fetch_user(int(OWNER_ID))
            
            # Pega a primeira guild do bot (assumindo que é o servidor principal)
            if not self.bot.guilds: return
            guild = self.bot.guilds[0]
            
            stats = get_daily_summary(guild.id)
            
            embed = discord.Embed(
                title=f"📅 Resumo Diário — {guild.name}",
                description=f"Aqui está o desempenho das últimas 24h na **Darke Store**.",
                color=discord.Color.blurple(),
                timestamp=datetime.now(timezone.utc)
            )
            
            ai_emoji = "🟢" if stats["rate_24h"] >= 70 else ("🟡" if stats["rate_24h"] >= 40 else "🔴")
            embed.add_field(
                name="🤖 Suporte IA",
                value=(
                    f"💬 Atendimentos: **{stats['total_24h']}**\n"
                    f"{ai_emoji} Taxa de Resolução: **{stats['rate_24h']}%**"
                ),
                inline=True
            )
            
            embed.add_field(
                name="👥 Comunidade",
                value=f"✅ Novos membros: **{stats['new_members_24h']}**",
                inline=True
            )
            
            if stats["top_commands"]:
                cmds = "\n".join(f"• `.{c['name']}`: **{c['count']}x**" for c in stats["top_commands"])
                embed.add_field(name="⌨️ Comandos mais usados", value=cmds, inline=False)
            
            embed.set_footer(text="Relatório Automático • Darke Store")
            await owner.send(embed=embed)
            log.info("[Analytics] Resumo diário enviado ao dono.")
        except Exception as e:
            log.error(f"[Analytics] Erro ao enviar resumo diário: {e}")

    def _is_staff(self, ctx: commands.Context) -> bool:
        return (
            str(ctx.author.id) == str(OWNER_ID)
            or ctx.author.guild_permissions.manage_guild
        )

    @commands.hybrid_command(name="stats", description="Dashboard geral de estatísticas do servidor.")
    async def stats(self, ctx: commands.Context):
        if not self._is_staff(ctx):
            return await ctx.send("❌ Você precisa de permissão `Gerenciar Servidor` para usar este comando.", ephemeral=True)

        await ctx.defer(ephemeral=True)
        data, ai_data = _get_stats_cached(ctx.guild.id)  # Fix #17 — cached
        guild   = ctx.guild

        embed = discord.Embed(
            title=f"📊 Dashboard — {guild.name}",
            color=discord.Color.blurple()
        )
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)

        # ── Membros ──────────────────────────────────────────────────────────
        growth_icon = "📈" if data["net_growth_30d"] >= 0 else "📉"
        embed.add_field(
            name="👥 Membros (últimos 30 dias)",
            value=(
                f"✅ Entradas: **{data['joins_30d']}**\n"
                f"❌ Saídas:   **{data['leaves_30d']}**\n"
                f"{growth_icon} Saldo:    **{data['net_growth_30d']:+d}**\n"
                f"👥 Total atual: **{guild.member_count}**"
            ),
            inline=True
        )

        # ── IA ───────────────────────────────────────────────────────────────
        ai_emoji = "🟢" if ai_data["rate"] >= 70 else ("🟡" if ai_data["rate"] >= 40 else "🔴")
        embed.add_field(
            name="🤖 IA de Suporte",
            value=(
                f"💬 Perguntas (7d): **{data['ai_questions_7d']}**\n"
                f"👤 Usuários hoje: **{data['ai_users_today']}**\n"
                f"{ai_emoji} Resolvidos: **{ai_data['rate']}%**\n"
                f"📊 Total feedbacks: **{ai_data['total']}**"
            ),
            inline=True
        )

        # ── Moderação ────────────────────────────────────────────────────────
        mod_str = ""
        if data["mod_actions_7d"]:
            mod_str = "\n".join(f"• {r['action'].upper()}: **{r['count']}**" for r in data["mod_actions_7d"][:5])
        else:
            mod_str = "_Nenhuma ação registrada_"

        embed.add_field(
            name=f"🛡️ Moderação (7 dias) | ⚠️ Warns ativos: {data['total_warns']}",
            value=mod_str,
            inline=False
        )

        # ── Top Comandos ─────────────────────────────────────────────────────
        if data["top_commands"]:
            max_uses = data["top_commands"][0]["uses"] if data["top_commands"] else 1
            cmd_str  = "\n".join(
                f"`{r['name']}` {_bar(r['uses'], max_uses)} **{r['uses']}x**"
                for r in data["top_commands"]
            )
            embed.add_field(name="⌨️ Top 5 Comandos", value=cmd_str, inline=True)

        # ── Eventos de segurança ─────────────────────────────────────────────
        if data["security_events"]:
            sec_str = "\n".join(f"• `{r['type']}`: **{r['count']}**" for r in data["security_events"][:5])
            embed.add_field(name="🔐 Eventos de Segurança (7d)", value=sec_str, inline=True)

        # ── Atividade horária ────────────────────────────────────────────────
        if data["activity_hourly"]:
            max_h  = max(r["count"] for r in data["activity_hourly"])
            hr_str = "  ".join(
                f"**{r['hour']}h**{'▲' if r['count'] == max_h else ''}"
                for r in data["activity_hourly"][:6]
            )
            embed.add_field(
                name="⏰ Horários mais ativos hoje",
                value=hr_str or "_Sem dados_",
                inline=False
            )

        embed.set_footer(text="Darke Store Bot v4 • Analytics | Use .statsmod .statsai .statsmembros")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="hoje", description="Resumo rápido das últimas 24h (Apenas Staff).")
    async def hoje(self, ctx: commands.Context):
        if not self._is_staff(ctx):
            return await ctx.send("❌ Sem permissão.", ephemeral=True)
        
        stats = get_daily_summary(ctx.guild.id)
        embed = discord.Embed(title="📅 Resumo das últimas 24h", color=discord.Color.blurple())
        
        ai_emoji = "🟢" if stats["rate_24h"] >= 70 else ("🟡" if stats["rate_24h"] >= 40 else "🔴")
        embed.add_field(name="🤖 Suporte IA", value=f"💬 {stats['total_24h']} atendimentos\n{ai_emoji} {stats['rate_24h']}% resolvidos", inline=True)
        embed.add_field(name="👥 Membros", value=f"✅ {stats['new_members_24h']} novos", inline=True)
        
        if stats["top_commands"]:
            cmds = "\n".join(f"• `.{c['name']}`: **{c['count']}x**" for c in stats["top_commands"])
            embed.add_field(name="⌨️ Top Comandos", value=cmds, inline=False)
            
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="statsmod", description="Estatísticas detalhadas de moderação.")
    async def statsmod(self, ctx: commands.Context):
        if not self._is_staff(ctx):
            return await ctx.send("❌ Sem permissão.", ephemeral=True)

        await ctx.defer(ephemeral=True)
        data = get_analytics_summary(ctx.guild.id)

        embed = discord.Embed(
            title=f"🛡️ Moderação — {ctx.guild.name}",
            color=discord.Color.orange()
        )

        if data["mod_actions_7d"]:
            total_mod = sum(r["count"] for r in data["mod_actions_7d"])
            embed.description = f"**Total de ações nos últimos 7 dias: {total_mod}**"
            for r in data["mod_actions_7d"]:
                bar = _bar(r["count"], total_mod, 12)
                embed.add_field(
                    name=r["action"].upper(),
                    value=f"{bar} **{r['count']}** ({round(r['count']/total_mod*100)}%)",
                    inline=False
                )
        else:
            embed.description = "✅ Nenhuma ação de moderação nos últimos 7 dias."

        embed.add_field(name="⚠️ Warns Ativos (total)", value=str(data["total_warns"]), inline=True)
        embed.set_footer(text="Período: últimos 7 dias")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="statsai", description="Estatísticas detalhadas da IA de suporte.")
    async def statsai(self, ctx: commands.Context):
        if not self._is_staff(ctx):
            return await ctx.send("❌ Sem permissão.", ephemeral=True)

        await ctx.defer(ephemeral=True)
        ai   = get_ai_quality_stats(ctx.guild.id)
        data = get_analytics_summary(ctx.guild.id)

        emoji = "🟢" if ai["rate"] >= 70 else ("🟡" if ai["rate"] >= 40 else "🔴")
        embed = discord.Embed(
            title=f"🤖 IA de Suporte — {ctx.guild.name}",
            color=discord.Color.blurple()
        )
        embed.add_field(name="📩 Total de Feedbacks", value=str(ai["total"]),    inline=True)
        embed.add_field(name="✅ Resolvidos",           value=str(ai["resolved"]),inline=True)
        embed.add_field(name="❌ Não Resolvidos",       value=str(ai["unresolved"]),inline=True)
        embed.add_field(name=f"{emoji} Taxa de Resolução", value=f"**{ai['rate']}%**\n{_bar(ai['resolved'], ai['total'], 15)}", inline=False)
        embed.add_field(
            name="📅 Últimos 7 dias",
            value=f"✅ {ai['resolved_7d']} resolvidos de {ai['total_7d']} feedbacks",
            inline=False
        )
        embed.add_field(name="💬 Perguntas (7d)", value=str(data["ai_questions_7d"]), inline=True)
        embed.add_field(name="👤 Usuários hoje",  value=str(data["ai_users_today"]),  inline=True)

        if ai["categories"]:
            cats = "\n".join(f"• {c['name']}: **{c['count']}**" for c in ai["categories"][:6])
            embed.add_field(name="📁 Por Categoria", value=cats, inline=False)

        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="statsmembros", description="Crescimento de membros nos últimos 30 dias.")
    async def statsmembros(self, ctx: commands.Context):
        if not self._is_staff(ctx):
            return await ctx.send("❌ Sem permissão.", ephemeral=True)

        await ctx.defer(ephemeral=True)
        data  = get_analytics_summary(ctx.guild.id)
        guild = ctx.guild

        growth_icon = "📈" if data["net_growth_30d"] >= 0 else "📉"
        embed = discord.Embed(
            title=f"👥 Membros — {guild.name}",
            color=discord.Color.green() if data["net_growth_30d"] >= 0 else discord.Color.red()
        )
        embed.add_field(name="👥 Total Atual",    value=str(guild.member_count),   inline=True)
        embed.add_field(name="✅ Entradas (30d)", value=str(data["joins_30d"]),     inline=True)
        embed.add_field(name="❌ Saídas (30d)",   value=str(data["leaves_30d"]),   inline=True)
        embed.add_field(
            name=f"{growth_icon} Saldo Líquido (30d)",
            value=f"**{data['net_growth_30d']:+d} membros**",
            inline=False
        )

        total_moves = data["joins_30d"] + data["leaves_30d"]
        if total_moves > 0:
            retention = round(data["joins_30d"] / total_moves * 100, 1)
            embed.add_field(
                name="📊 Retenção",
                value=f"{_bar(data['joins_30d'], total_moves, 15)} **{retention}%**",
                inline=False
            )
        embed.set_footer(text="Período: últimos 30 dias")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="statscomandos", description="Ranking dos comandos mais utilizados.")
    async def statscomandos(self, ctx: commands.Context):
        if not self._is_staff(ctx):
            return await ctx.send("❌ Sem permissão.", ephemeral=True)

        await ctx.defer(ephemeral=True)
        data = get_analytics_summary(ctx.guild.id)

        embed = discord.Embed(
            title=f"⌨️ Comandos Mais Usados — {ctx.guild.name}",
            color=discord.Color.blurple()
        )

        if data["top_commands"]:
            max_uses = data["top_commands"][0]["uses"]
            for i, r in enumerate(data["top_commands"], 1):
                medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i - 1] if i <= 5 else f"#{i}"
                embed.add_field(
                    name=f"{medal} .{r['name']}",
                    value=f"{_bar(r['uses'], max_uses, 15)} **{r['uses']}x**",
                    inline=False
                )
        else:
            embed.description = "_Nenhum uso de comando registrado ainda._"

        embed.set_footer(text="Dados acumulados desde a instalação do bot v4")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Analytics(bot))
