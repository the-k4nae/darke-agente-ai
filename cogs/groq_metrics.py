"""
cogs/groq_metrics.py  ─  v1
────────────────────────────
Dashboard de custo e uso da API Groq — Darke Store Bot

Funcionalidades:
  ✅ .groqcusto    → estimativa de custo diário/mensal por modelo
  ✅ .groqvolume   → volume de chamadas por hora (últimas 24h)
  ✅ .groqcasos    → usuários com mais tickets não resolvidos (identifica casos difíceis)
  ✅ Alerta automático ao dono quando consumo de tokens > threshold diário
"""

import discord
from discord.ext import commands, tasks
import time
from datetime import datetime, timezone

from utils.logger import log
from utils.cache import OWNER_ID
from utils.database import (
    get_ai_quality_stats,
    get_top_unresolved_users,
    get_ai_hourly_volume,
)
from utils.groq_pool import get_pool

# ── Preços estimados por modelo (USD por 1M tokens) ──────────────────────────
# Fonte: console.groq.com/settings/billing — atualize conforme necessário
_MODEL_PRICES: dict[str, dict] = {
    "llama-3.1-8b-instant":                    {"input": 0.05,  "output": 0.08},
    "meta-llama/llama-4-scout-17b-16e-instruct": {"input": 0.11,  "output": 0.34},
    "llama3-70b-8192":                          {"input": 0.59,  "output": 0.79},
    "mixtral-8x7b-32768":                       {"input": 0.24,  "output": 0.24},
}

# Threshold de tokens/dia para alerta (padrão conservador)
_DAILY_TOKEN_ALERT = 400_000   # 400k tokens/dia → ~80% do limite free tier


def _estimate_cost_usd(tokens: int, model: str) -> float:
    """Estimativa de custo em USD. Assume 50/50 input/output como heurística."""
    prices = _MODEL_PRICES.get(model, {"input": 0.10, "output": 0.20})
    avg_price = (prices["input"] + prices["output"]) / 2
    return (tokens / 1_000_000) * avg_price


def _bar_chart(data: list[dict], key_field: str, value_field: str, width: int = 12) -> str:
    """Gera gráfico de barras ASCII para o embed."""
    if not data:
        return "_Sem dados._"
    max_val = max(d[value_field] for d in data) or 1
    lines = []
    for d in data:
        pct   = d[value_field] / max_val
        bar   = "█" * round(pct * width) + "░" * (width - round(pct * width))
        lines.append(f"`{str(d[key_field]).zfill(2)}h` {bar} **{d[value_field]}**")
    return "\n".join(lines)


class GroqMetrics(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._daily_alert_sent_date: str = ""   # "YYYY-MM-DD" — evita alertas duplicados
        self._cost_check.start()

    def cog_unload(self):
        self._cost_check.cancel()

    def _is_owner(self, ctx: commands.Context) -> bool:
        return str(ctx.author.id) == str(OWNER_ID)

    # ── Monitor automático de consumo ─────────────────────────────────────────
    @tasks.loop(hours=1)
    async def _cost_check(self):
        """Alerta o dono se qualquer chave Groq ultrapassar o threshold diário."""
        try:
            pool   = get_pool()
            status = pool.status()
            today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            if self._daily_alert_sent_date == today:
                return  # já alertou hoje

            for s in status:
                if s.get("tokens", 0) >= _DAILY_TOKEN_ALERT:
                    self._daily_alert_sent_date = today
                    await self._alert_owner(
                        f"⚠️ **Alerta de Consumo Groq**\n"
                        f"A **{s['key']}** consumiu **{s['tokens']:,} tokens** hoje "
                        f"({s.get('budget_pct', 0)}% do orçamento estimado de 500k).\n"
                        f"Estimativa de custo: **~${_estimate_cost_usd(s['tokens'], 'llama-3.1-8b-instant'):.3f} USD**\n\n"
                        f"Use `.groqcusto` para detalhes completos."
                    )
                    break
        except Exception as e:
            log.warning(f"[GroqMetrics] Erro no cost_check: {e}")

    @_cost_check.before_loop
    async def _before_cost_check(self):
        await self.bot.wait_until_ready()

    async def _alert_owner(self, message: str):
        if not OWNER_ID:
            return
        try:
            owner = self.bot.get_user(int(OWNER_ID)) or await self.bot.fetch_user(int(OWNER_ID))
            await owner.send(message)
        except Exception as e:
            log.warning(f"[GroqMetrics] Não foi possível alertar o dono: {e}")

    # ── Comandos ──────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="groqcusto", description="Dashboard de custo e tokens da API Groq (Apenas Dono).")
    async def groqcusto(self, ctx: commands.Context):
        if not self._is_owner(ctx):
            return await ctx.send("❌ Apenas o dono pode ver estas métricas.", ephemeral=True)

        await ctx.defer(ephemeral=True)

        try:
            pool   = get_pool()
            status = pool.status()
        except Exception as e:
            return await ctx.send(f"❌ Erro ao acessar o pool Groq: `{e}`", ephemeral=True)

        embed = discord.Embed(
            title="💰 Dashboard de Custo — Groq API",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow()
        )

        total_tokens_today = 0
        for s in status:
            tokens   = s.get("tokens", 0)
            pct      = s.get("budget_pct", 0)
            status_v = s.get("status", "?")
            total_tokens_today += tokens

            # Barra de progresso do orçamento
            filled = round(pct / 10)
            bar    = "█" * filled + "░" * (10 - filled)
            pct_icon = "🟢" if pct < 60 else ("🟡" if pct < 85 else "🔴")

            if "inválida" in status_v:
                val = "❌ Chave inválida (401/403)"
            elif "bloqueada" in status_v:
                val = f"⏳ Rate-limited | Tokens: **{tokens:,}** ({pct}%)"
            else:
                cost_est = _estimate_cost_usd(tokens, "llama-3.1-8b-instant")
                val = (
                    f"{pct_icon} `{bar}` **{pct}%**\n"
                    f"Tokens hoje: **{tokens:,}** | Est.: **~${cost_est:.4f} USD**"
                )
            embed.add_field(name=f"🔑 {s['key']}", value=val, inline=False)

        # Totais
        total_cost = _estimate_cost_usd(total_tokens_today, "llama-3.1-8b-instant")
        monthly_est = total_cost * 30
        embed.add_field(
            name="📊 Resumo Hoje",
            value=(
                f"Total de tokens: **{total_tokens_today:,}**\n"
                f"Custo estimado hoje: **~${total_cost:.4f} USD**\n"
                f"Projeção mensal: **~${monthly_est:.2f} USD**"
            ),
            inline=False
        )

        embed.set_footer(
            text="Estimativas baseadas em preços do tier free (llama-3.1-8b-instant). "
                 "Modelos Vision têm custo maior."
        )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="groqvolume", description="Volume de chamadas à IA por hora nas últimas 24h (Apenas Dono).")
    async def groqvolume(self, ctx: commands.Context):
        if not self._is_owner(ctx):
            return await ctx.send("❌ Apenas o dono pode ver estas métricas.", ephemeral=True)

        await ctx.defer(ephemeral=True)

        if not ctx.guild:
            return await ctx.send("❌ Use este comando dentro de um servidor.", ephemeral=True)

        data = get_ai_hourly_volume(ctx.guild.id, hours=24)

        embed = discord.Embed(
            title="📈 Volume de Chamadas de IA — Últimas 24h",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow()
        )

        if not data:
            embed.description = "_Nenhuma chamada registrada nas últimas 24h._"
        else:
            chart = _bar_chart(data, "hour", "count", width=10)
            total = sum(d["count"] for d in data)
            peak  = max(data, key=lambda d: d["count"])
            embed.description = chart
            embed.add_field(
                name="📊 Resumo",
                value=(
                    f"Total de interações: **{total}**\n"
                    f"Pico: **{peak['count']}** chamadas às **{peak['hour']}h UTC**\n"
                    f"Média: **{total / max(len(data), 1):.1f}** por hora"
                ),
                inline=False
            )

        embed.set_footer(text="Horários em UTC")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="groqcasos", description="Usuários com mais suportes não resolvidos (Apenas Dono).")
    async def groqcasos(self, ctx: commands.Context, dias: int = 7):
        if not self._is_owner(ctx):
            return await ctx.send("❌ Apenas o dono pode ver estas métricas.", ephemeral=True)
        if not 1 <= dias <= 30:
            return await ctx.send("⚠️ Use entre 1 e 30 dias.", ephemeral=True)

        await ctx.defer(ephemeral=True)

        if not ctx.guild:
            return await ctx.send("❌ Use este comando dentro de um servidor.", ephemeral=True)

        users = get_top_unresolved_users(ctx.guild.id, days=dias, limit=10)

        embed = discord.Embed(
            title=f"🔁 Casos Recorrentes — Últimos {dias} dias",
            description="Usuários que mais precisaram de suporte humano após a IA.",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )

        if not users:
            embed.description = f"_Nenhum caso não resolvido nos últimos {dias} dias. 🎉_"
        else:
            lines = []
            for i, u in enumerate(users, 1):
                member = ctx.guild.get_member(u["user_id"])
                name   = str(member) if member else f"ID:{u['user_id']}"
                bar    = "🔴" * min(u["count"], 5)
                lines.append(f"**{i}.** {name} — {bar} **{u['count']}x** não resolvido")

            embed.description = "\n".join(lines)
            embed.set_footer(
                text="Estes usuários podem se beneficiar de um tutorial personalizado ou ajuste no prompt."
            )

        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GroqMetrics(bot))
