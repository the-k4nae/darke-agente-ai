"""
cogs/modlog.py
──────────────
Log de moderação unificado.
- Registra automaticamente: ban, kick, mute, unmute, warn, unban
- .modlog @user   → histórico completo de punições do membro
- .modlog recente → últimas 20 ações de moderação no servidor
Integrado com moderation.py e warns.py via add_mod_log().
"""
import discord
from discord.ext import commands
from utils.database import get_mod_log, get_mod_log_recent

# Fix #8 — import no topo do módulo em vez de dentro do método
try:
    from cogs.ux import paginate_fields, PaginatedEmbed
    _UX_AVAILABLE = True
except ImportError:
    _UX_AVAILABLE = False

ACTION_ICONS = {
    "ban":    "🔨",
    "kick":   "👢",
    "mute":   "🔇",
    "unmute": "🔊",
    "warn":   "⚠️",
    "unban":  "✅",
    "auto_ban":  "🤖🔨",
    "auto_mute": "🤖🔇",
}

def _format_entry(entry: dict, guild: discord.Guild) -> str:
    icon   = ACTION_ICONS.get(entry["action"], "📋")
    mod    = guild.get_member(entry["mod_id"])
    mod_str = mod.mention if mod else f"`{entry['mod_id']}`"
    reason  = entry.get("reason") or "—"
    dur_str = f" ({entry['duration']})" if entry.get("duration") else ""
    date    = entry["created_at"][:16]
    return f"{icon} **{entry['action'].upper()}**{dur_str} por {mod_str}\n> Motivo: {reason} | {date}"


class ModLog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="modlog", description="Mostra o histórico de moderação de um membro.")
    @commands.has_permissions(moderate_members=True)
    async def modlog(self, ctx: commands.Context, member: discord.Member = None):
        """
        .modlog @user    → histórico do usuário
        .modlog recente  → últimas 20 ações do servidor
        """
        if member is None:
            # Sem argumento → mostra log recente do servidor
            return await self._show_recent(ctx)

        entries = get_mod_log(ctx.guild.id, member.id, limit=15)
        if not entries:
            return await ctx.send(f"✅ Nenhuma entrada de moderação para {member.mention}.", ephemeral=True)

        embed = discord.Embed(
            title=f"📋 Mod Log — {member.display_name}",
            color=discord.Color.orange()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Usuário", value=f"{member.mention} (`{member.id}`)", inline=False)

        # Paginação: 5 entries por página
        items = [
            (f"#{e['id']} — {e['created_at'][:16]}", _format_entry(e, ctx.guild), False)
            for e in entries
        ]
        if len(items) <= 5 or not _UX_AVAILABLE:
            for name, value, inline in items[:15]:
                embed.add_field(name=name, value=value, inline=inline)
            embed.set_footer(text=f"Total: {len(items)} entrada(s).")
            await ctx.send(embed=embed, ephemeral=True)
        else:
            embeds = paginate_fields(
                title=f"📋 Mod Log — {member.display_name}",
                color=discord.Color.orange(),
                items=items,
                per_page=5,
                footer_prefix=f"Usuário: {member.id}"
            )
            for e in embeds:
                e.set_thumbnail(url=member.display_avatar.url)
            view = PaginatedEmbed(embeds, ctx.author.id)
            await ctx.send(embed=embeds[0], view=view, ephemeral=True)

    async def _show_recent(self, ctx: commands.Context):
        entries = get_mod_log_recent(ctx.guild.id, limit=20)
        if not entries:
            return await ctx.send("ℹ️ Nenhuma ação de moderação registrada.", ephemeral=True)

        embed = discord.Embed(
            title=f"📋 Mod Log Recente — {ctx.guild.name}",
            color=discord.Color.blurple()
        )
        for entry in entries[:15]:
            target = ctx.guild.get_member(entry["target_id"])
            target_str = target.mention if target else f"`{entry['target_id']}`"
            icon   = ACTION_ICONS.get(entry["action"], "📋")
            mod    = ctx.guild.get_member(entry["mod_id"])
            mod_str = mod.display_name if mod else f"ID {entry['mod_id']}"
            reason  = (entry.get("reason") or "—")[:60]
            embed.add_field(
                name=f"{icon} {entry['action'].upper()} — {entry['created_at'][:16]}",
                value=f"**Alvo:** {target_str} | **Mod:** {mod_str}\n**Motivo:** {reason}",
                inline=False
            )
        embed.set_footer(text=f"Últimas {len(entries)} ações.")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(ModLog(bot))
