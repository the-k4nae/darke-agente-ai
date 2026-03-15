"""
cogs/giveaway.py
────────────────
Sistema de sorteios nativos com contagem regressiva.
- .giveaway <tempo> [vencedores] <prêmio>
  Tempo: 30s / 5m / 2h / 1d
  Exemplo: .giveaway 24h 2 Minecraft Java Edition
- .gsorteio <message_id>   → sorteia novamente
- .gcancelar <message_id>  → cancela o sorteio
- .gstatus                 → lista sorteios ativos
"""
import discord
from discord.ext import commands
import asyncio
import random
import re
from datetime import datetime, timezone, timedelta
from utils.logger import log_action, log
from utils.database import (
    create_giveaway, get_active_giveaways,
    get_giveaway_by_message, end_giveaway,
    add_giveaway_entry, remove_giveaway_entry,
    get_giveaway_entry_count,
)

GIVEAWAY_EMOJI = "🎉"

def parse_duration(text: str) -> int | None:
    """Converte string de duração para segundos. Ex: '30s'→30, '5m'→300, '2h'→7200, '1d'→86400"""
    m = re.fullmatch(r'(\d+)(s|m|h|d)', text.strip().lower())
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2)
    return value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]

def format_duration(seconds: int) -> str:
    if seconds < 60:    return f"{seconds}s"
    if seconds < 3600:  return f"{seconds//60}m"
    if seconds < 86400: return f"{seconds//3600}h"
    return f"{seconds//86400}d"

def giveaway_embed(prize: str, host: discord.Member, ends_at: datetime,
                   winners_count: int, participants: int = 0, ended: bool = False,
                   winners: list = None) -> discord.Embed:
    if ended and winners:
        color = discord.Color.gold()
        desc  = f"**Prêmio:** {prize}\n**Vencedor(es):** {', '.join(w.mention for w in winners)}\n**Organizado por:** {host.mention}"
        title = f"🎊 SORTEIO ENCERRADO — {prize}"
    elif ended:
        color = discord.Color.dark_gray()
        desc  = f"**Prêmio:** {prize}\n**Sem participantes suficientes.**\n**Organizado por:** {host.mention}"
        title = f"❌ SORTEIO CANCELADO — {prize}"
    else:
        color = discord.Color.blurple()
        ts    = int(ends_at.timestamp())
        desc  = (
            f"Clique em **{GIVEAWAY_EMOJI}** para participar!\n\n"
            f"**Prêmio:** {prize}\n"
            f"**Encerra:** <t:{ts}:R> (<t:{ts}:f>)\n"
            f"**Vencedores:** {winners_count}\n"
            f"**Organizado por:** {host.mention}\n"
            f"**Participantes:** {participants}"
        )
        title = f"{GIVEAWAY_EMOJI} SORTEIO — {prize}"

    return discord.Embed(title=title, description=desc, color=color)


class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Participar", emoji=GIVEAWAY_EMOJI, style=discord.ButtonStyle.primary, custom_id="giveaway_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        gw = get_giveaway_by_message(interaction.message.id)
        if not gw or gw["ended"]:
            return await interaction.response.send_message("❌ Este sorteio já foi encerrado.", ephemeral=True)

        # Fix #1 — participação via DB (PRIMARY KEY impede duplicatas) com toggle
        already = not add_giveaway_entry(interaction.message.id, interaction.user.id)

        if already:
            # Usuário já participava — remove (toggle out)
            remove_giveaway_entry(interaction.message.id, interaction.user.id)
            count = get_giveaway_entry_count(interaction.message.id)
            # Fix #15 — atualiza contador no embed em tempo real
            try:
                gw_data = get_giveaway_by_message(interaction.message.id)
                if gw_data:
                    host = interaction.guild.get_member(gw_data["host_id"])
                    ends_at = datetime.fromisoformat(gw_data["ends_at"]).replace(tzinfo=timezone.utc)
                    embed = giveaway_embed(gw_data["prize"], host, ends_at,
                                          gw_data["winners_count"], participants=count)
                    await interaction.message.edit(embed=embed)
            except Exception:
                pass
            return await interaction.response.send_message("↩️ Você saiu do sorteio.", ephemeral=True)

        count = get_giveaway_entry_count(interaction.message.id)
        # Fix #15 — atualiza contador no embed em tempo real
        try:
            gw_data = get_giveaway_by_message(interaction.message.id)
            if gw_data:
                host = interaction.guild.get_member(gw_data["host_id"])
                ends_at = datetime.fromisoformat(gw_data["ends_at"]).replace(tzinfo=timezone.utc)
                embed = giveaway_embed(gw_data["prize"], host, ends_at,
                                       gw_data["winners_count"], participants=count)
                await interaction.message.edit(embed=embed)
        except Exception:
            pass

        await interaction.response.send_message(f"✅ Você está participando do sorteio **{gw['prize']}**!", ephemeral=True)


class Giveaway(commands.Cog):
    def __init__(self, bot):
        self.bot  = bot
        self._tasks: dict[int, asyncio.Task] = {}   # message_id → task

    @commands.Cog.listener()
    async def on_ready(self):
        """Retoma sorteios ativos após reinicialização."""
        # Cancela tasks anteriores (reconexão) para evitar duplicatas
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()

        # Fix #2 — chama get_active_giveaways() apenas uma vez
        active = get_active_giveaways()
        for gw in active:
            ends_at = datetime.fromisoformat(gw["ends_at"]).replace(tzinfo=timezone.utc)
            now     = datetime.now(timezone.utc)
            if ends_at <= now:
                await self._finish_giveaway(gw["message_id"], gw["channel_id"], gw["guild_id"])
            else:
                delay = (ends_at - now).total_seconds()
                task  = asyncio.create_task(
                    self._wait_and_finish(gw["message_id"], gw["channel_id"], gw["guild_id"], delay)
                )
                self._tasks[gw["message_id"]] = task
        if active:
            log.info(f"Retomados {len(active)} sorteio(s) ativos.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _wait_and_finish(self, message_id: int, channel_id: int, guild_id: int, delay: float):
        await asyncio.sleep(delay)
        await self._finish_giveaway(message_id, channel_id, guild_id)

    async def _finish_giveaway(self, message_id: int, channel_id: int, guild_id: int):
        guild   = self.bot.get_guild(guild_id)
        channel = self.bot.get_channel(channel_id)
        if not guild or not channel:
            return

        gw = get_giveaway_by_message(message_id)
        if not gw or gw["ended"]:
            return

        try:
            msg = await channel.fetch_message(message_id)
        except Exception:
            return

        # Fix #10 — coleta participantes do banco (robusto para grandes sorteios)
        participant_ids = end_giveaway(message_id)
        entrants = []
        for uid in participant_ids:
            member = guild.get_member(uid)
            if member:
                entrants.append(member)

        host = guild.get_member(gw["host_id"]) or guild.me
        winners_count = gw["winners_count"]

        if not entrants:
            winners = []
        else:
            winners = random.sample(entrants, min(winners_count, len(entrants)))

        embed = giveaway_embed(gw["prize"], host, datetime.fromisoformat(gw["ends_at"]),
                               winners_count, ended=True, winners=winners if winners else None)
        try:
            await msg.edit(embed=embed, view=None)
        except Exception:
            pass

        if winners:
            winner_mentions = " ".join(w.mention for w in winners)
            await channel.send(
                f"🎊 Parabéns {winner_mentions}! Você(s) ganharam **{gw['prize']}**!\n"
                f"Entre em contato com {host.mention} para resgatar o prêmio."
            )
        else:
            await channel.send(f"❌ Sorteio de **{gw['prize']}** encerrado sem participantes suficientes.")

        self._tasks.pop(message_id, None)
        await log_action(guild, f"🎊 **Sorteio encerrado:** `{gw['prize']}`. Vencedores: {len(winners)}/{winners_count}",
                         color=discord.Color.gold())

    # ── Comandos ──────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="giveaway", aliases=["sorteio"], description="Inicia um sorteio.")
    @commands.has_permissions(manage_guild=True)
    async def giveaway_cmd(self, ctx: commands.Context, duracao: str, vencedores_ou_premio: str, *, premio_resto: str = ""):
        """
        .giveaway <tempo> [qtd_vencedores] <prêmio>
        Exemplos:
          .giveaway 1h Nitro Classic
          .giveaway 30m 3 Steam Key
        """
        seconds = parse_duration(duracao)
        if not seconds:
            return await ctx.send("❌ Formato de tempo inválido. Use: `30s`, `5m`, `2h`, `1d`")
        if seconds < 10:
            return await ctx.send("❌ O sorteio deve durar pelo menos 10 segundos.")
        if seconds > 86400 * 30:
            return await ctx.send("❌ O sorteio não pode durar mais de 30 dias.")

        if vencedores_ou_premio.isdigit() and premio_resto:
            winners_count = int(vencedores_ou_premio)
            prize = premio_resto
        else:
            winners_count = 1
            prize = vencedores_ou_premio + (" " + premio_resto if premio_resto else "")

        winners_count = max(1, min(winners_count, 20))
        prize = prize.strip()
        if not prize:
            return await ctx.send("❌ Informe o prêmio do sorteio.")

        ends_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        embed   = giveaway_embed(prize, ctx.author, ends_at, winners_count, participants=0)
        view    = GiveawayView()

        await ctx.defer()
        msg = await ctx.channel.send(embed=embed, view=view)
        await msg.add_reaction(GIVEAWAY_EMOJI)

        create_giveaway(ctx.guild.id, ctx.channel.id, msg.id, prize, ctx.author.id, ends_at.isoformat(), winners_count)

        task = asyncio.create_task(
            self._wait_and_finish(msg.id, ctx.channel.id, ctx.guild.id, seconds)
        )
        self._tasks[msg.id] = task

        await log_action(ctx.guild,
                         f"🎉 **Sorteio iniciado** por {ctx.author.mention}: `{prize}` por {format_duration(seconds)} ({winners_count} vencedor(es)).",
                         color=discord.Color.blurple())

    @commands.hybrid_command(name="gsorteio", description="Sorteia novamente em um sorteio encerrado.")
    @commands.has_permissions(manage_guild=True)
    async def reroll(self, ctx: commands.Context, message_id: int):
        gw = get_giveaway_by_message(message_id)
        if not gw:
            return await ctx.send("❌ Sorteio não encontrado.")

        # Fix #10 — usa entradas do banco para reroll também
        participant_ids = end_giveaway(message_id) if not gw["ended"] else []
        if not participant_ids:
            # Tenta buscar do banco diretamente se já encerrado
            from utils.database import get_giveaway_entries
            participant_ids = get_giveaway_entries(message_id)

        entrants = [ctx.guild.get_member(uid) for uid in participant_ids if ctx.guild.get_member(uid)]
        if not entrants:
            return await ctx.send("❌ Sem participantes para sortear.")

        winners = random.sample(entrants, min(gw["winners_count"], len(entrants)))
        await ctx.send(f"🎊 Novo sorteio! Vencedor(es): {' '.join(w.mention for w in winners)} — **{gw['prize']}**!")

    @commands.hybrid_command(name="gcancelar", description="Cancela um sorteio ativo.")
    @commands.has_permissions(manage_guild=True)
    async def gcancelar(self, ctx: commands.Context, message_id: int):
        gw = get_giveaway_by_message(message_id)
        if not gw or gw["ended"]:
            return await ctx.send("❌ Sorteio não encontrado ou já encerrado.")

        task = self._tasks.pop(message_id, None)
        if task:
            task.cancel()

        end_giveaway(message_id)

        try:
            gw_channel = self.bot.get_channel(gw["channel_id"])
            if gw_channel:
                msg = await gw_channel.fetch_message(message_id)
                host = ctx.guild.get_member(gw["host_id"]) or ctx.author
                embed = giveaway_embed(gw["prize"], host, datetime.fromisoformat(gw["ends_at"]),
                                       gw["winners_count"], ended=True, winners=None)
                await msg.edit(embed=embed, view=None)
        except Exception:
            pass

        await ctx.send(f"❌ Sorteio **{gw['prize']}** cancelado.")
        await log_action(ctx.guild, f"❌ **Sorteio cancelado** por {ctx.author.mention}: `{gw['prize']}`",
                         color=discord.Color.red())

    @commands.hybrid_command(name="gstatus", description="Lista sorteios ativos.")
    @commands.has_permissions(manage_guild=True)
    async def gstatus(self, ctx: commands.Context):
        active = get_active_giveaways(guild_id=ctx.guild.id)
        if not active:
            return await ctx.send("ℹ️ Nenhum sorteio ativo no momento.", ephemeral=True)

        embed = discord.Embed(title=f"🎉 Sorteios Ativos ({len(active)})", color=discord.Color.blurple())
        for gw in active[:10]:
            ts = int(datetime.fromisoformat(gw["ends_at"]).timestamp())
            count = get_giveaway_entry_count(gw["message_id"])
            embed.add_field(
                name=gw["prize"],
                value=f"Encerra: <t:{ts}:R> | {gw['winners_count']} vencedor(es) | 👥 {count} participantes | ID: `{gw['message_id']}`",
                inline=False
            )
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Giveaway(bot))
