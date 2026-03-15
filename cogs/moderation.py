import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from utils.database import add_mod_log, log_command
from utils.logger import log

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="clear", description="Limpa mensagens do chat.")
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx: commands.Context, amount: int):
        if amount < 1 or amount > 100:
            await ctx.send("❌ Forneça um valor entre 1 e 100.", ephemeral=True)
            return

        # Só faz defer se for uma interação slash (hybrid via slash); prefixo não precisa e quebra o purge
        if ctx.interaction:
            await ctx.defer(ephemeral=True)

        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=14)

        # Para comandos de prefixo, deleta a mensagem de invocação antes do purge
        if not ctx.interaction:
            try:
                await ctx.message.delete()
            except Exception:
                pass

        # IMPORTANTE: nunca usar after=cutoff em purge — isso inverte a ordem e apaga as mensagens
        # mais antigas ao invés das mais recentes. Usar check= mantém a ordem correta (mais recente primeiro)
        # e apenas pula mensagens com mais de 14 dias (que o Discord não permite apagar em bulk de qualquer forma).
        deleted = await ctx.channel.purge(
            limit=amount,
            check=lambda m: m.created_at.replace(tzinfo=timezone.utc) > cutoff
                            if m.created_at.tzinfo is None
                            else m.created_at > cutoff
        )
        skipped = amount - len(deleted)
        note = f" _(+{skipped} mensagem(ns) ignorada(s) por ter mais de 14 dias)_" if skipped > 0 else ""

        log_command(ctx.guild.id, ctx.author.id, "clear")

        if ctx.interaction:
            await ctx.send(f"✅ {len(deleted)} mensagem(ns) apagada(s) por {ctx.author.mention}.{note}", ephemeral=True)
        else:
            # Para prefixo, envia uma mensagem temporária que se auto-deleta
            msg = await ctx.channel.send(f"✅ {len(deleted)} mensagem(ns) apagada(s) por {ctx.author.mention}.{note}")
            await asyncio.sleep(5)
            try:
                await msg.delete()
            except Exception:
                pass

    @commands.hybrid_command(name="lock", description="Bloqueia o canal atual.")
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context):
        try:
            everyone = ctx.guild.default_role
            # Preserva as permissões existentes do @everyone e apenas sobrescreve send_messages
            overwrite = ctx.channel.overwrites_for(everyone)
            overwrite.send_messages = False
            await ctx.channel.set_permissions(everyone, overwrite=overwrite)
            add_mod_log(ctx.guild.id, ctx.channel.id, ctx.author.id, "lock", f"Canal #{ctx.channel.name} bloqueado")
            log_command(ctx.guild.id, ctx.author.id, "lock")
            await ctx.send(f"🔒 Canal bloqueado por {ctx.author.mention}.")
            from utils.logger import log_action
            await log_action(ctx.guild, f"🔒 **Lock:** {ctx.channel.mention} bloqueado por {ctx.author.mention}.")
        except discord.Forbidden:
            await ctx.send("❌ Não tenho permissão para bloquear este canal.", ephemeral=True)
        except Exception as e:
            log.error(f"[lock] Erro: {e}")
            await ctx.send("❌ Erro ao bloquear canal.", ephemeral=True)

    @commands.hybrid_command(name="unlock", description="Desbloqueia o canal atual.")
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context):
        try:
            everyone = ctx.guild.default_role
            # Preserva as permissões existentes do @everyone e apenas restaura send_messages para None (herdar)
            overwrite = ctx.channel.overwrites_for(everyone)
            overwrite.send_messages = None
            # Se o overwrite ficou completamente vazio após remover o lock, remove ele por completo
            if overwrite.is_empty():
                await ctx.channel.set_permissions(everyone, overwrite=None)
            else:
                await ctx.channel.set_permissions(everyone, overwrite=overwrite)
            add_mod_log(ctx.guild.id, ctx.channel.id, ctx.author.id, "unlock", f"Canal #{ctx.channel.name} desbloqueado")
            log_command(ctx.guild.id, ctx.author.id, "unlock")
            await ctx.send(f"🔓 Canal desbloqueado por {ctx.author.mention}.")
            from utils.logger import log_action
            await log_action(ctx.guild, f"🔓 **Unlock:** {ctx.channel.mention} desbloqueado por {ctx.author.mention}.")
        except discord.Forbidden:
            await ctx.send("❌ Não tenho permissão para desbloquear este canal.", ephemeral=True)
        except Exception as e:
            log.error(f"[unlock] Erro: {e}")
            await ctx.send("❌ Erro ao desbloquear canal.", ephemeral=True)

    @commands.hybrid_command(name="ban", description="Bane um membro do servidor.")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Sem motivo informado"):
        if ctx.guild.me.top_role <= member.top_role:
            await ctx.send("❌ Não tenho permissão para banir este usuário (cargo mais alto que o meu).", ephemeral=True)
            return
        if ctx.author.top_role <= member.top_role and ctx.author.id != ctx.guild.owner_id:
            await ctx.send("❌ Você não pode banir alguém com cargo igual ou superior ao seu.", ephemeral=True)
            return

        try:
            await member.ban(reason=reason)
            add_mod_log(ctx.guild.id, member.id, ctx.author.id, "ban", reason)
            log_command(ctx.guild.id, ctx.author.id, "ban")
            await ctx.send(f"🔨 {member.mention} foi banido por {ctx.author.mention}. Motivo: `{reason}`")
        except discord.Forbidden:
            await ctx.send("❌ Não tenho permissão para banir este usuário.", ephemeral=True)
        except Exception as e:
            log.error(f"[ban] Erro inesperado: {e}")
            await ctx.send("❌ Ocorreu um erro ao tentar banir.", ephemeral=True)

    @commands.hybrid_command(name="kick", description="Expulsa um membro do servidor.")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Sem motivo informado"):
        if ctx.guild.me.top_role <= member.top_role:
            await ctx.send("❌ Não tenho permissão para expulsar este usuário.", ephemeral=True)
            return
        if ctx.author.top_role <= member.top_role and ctx.author.id != ctx.guild.owner_id:
            await ctx.send("❌ Você não pode expulsar alguém com cargo igual ou superior ao seu.", ephemeral=True)
            return

        try:
            await member.kick(reason=reason)
            add_mod_log(ctx.guild.id, member.id, ctx.author.id, "kick", reason)
            log_command(ctx.guild.id, ctx.author.id, "kick")
            await ctx.send(f"👢 {member.mention} foi expulso por {ctx.author.mention}. Motivo: `{reason}`")
        except discord.Forbidden:
            await ctx.send("❌ Não tenho permissão para expulsar este usuário.", ephemeral=True)
        except Exception as e:
            log.error(f"[kick] Erro inesperado: {e}")
            await ctx.send("❌ Ocorreu um erro ao tentar expulsar.", ephemeral=True)

    @commands.hybrid_command(name="mute", description="Muta um membro no servidor.")
    @commands.has_permissions(moderate_members=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, minutos: int = 10, *, reason: str = "Sem motivo informado"):
        # Fix #18 — valida limites: Discord aceita 1s–40320min (28 dias)
        if minutos < 1:
            return await ctx.send("❌ Duração mínima: **1 minuto**.", ephemeral=True)
        if minutos > 40320:
            return await ctx.send("❌ Duração máxima: **40320 minutos** (28 dias).", ephemeral=True)

        if ctx.guild.me.top_role <= member.top_role:
            await ctx.send("❌ Não tenho permissão para mutar este usuário.", ephemeral=True)
            return

        from datetime import timedelta
        duration = timedelta(minutes=minutos)
        try:
            await member.timeout(duration, reason=reason)
            add_mod_log(ctx.guild.id, member.id, ctx.author.id, "mute", reason, duration=f"{minutos}m")
            await ctx.send(f"🔇 {member.mention} foi silenciado por {minutos} minutos. Motivo: `{reason}`")
            log_command(ctx.guild.id, ctx.author.id, "mute")
        except discord.Forbidden:
            await ctx.send("❌ Não tenho permissão para silenciar (timeout) este usuário.", ephemeral=True)

    @commands.hybrid_command(name="unmute", description="Remove o mute de um membro.")
    @commands.has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        if ctx.guild.me.top_role <= member.top_role:
            await ctx.send("❌ Não tenho permissão para desmutar este usuário.", ephemeral=True)
            return

        try:
            await member.timeout(None, reason="Unmute manual")
            add_mod_log(ctx.guild.id, member.id, ctx.author.id, "unmute")
            await ctx.send(f"🔊 O silenciamento de {member.mention} foi removido por {ctx.author.mention}.")
        except discord.Forbidden:
            await ctx.send("❌ Não tenho permissão para remover o timeout deste usuário.", ephemeral=True)

    @commands.hybrid_command(name="unban", description="Desbane um usuário por ID ou nome.")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, *, user_input: str):
        await ctx.defer(ephemeral=True)

        # Tenta por ID primeiro (O(1) — não itera todos os bans)
        target_user = None
        if user_input.strip().isdigit():
            try:
                target_user = await self.bot.fetch_user(int(user_input.strip()))
                await ctx.guild.fetch_ban(target_user)  # verifica se está banido (raises NotFound se não estiver)
            except discord.NotFound:
                return await ctx.send("❌ Usuário não encontrado na lista de bans.", ephemeral=True)
            except discord.HTTPException:
                target_user = None  # fallback para busca por nome

        # Fallback: busca por nome (itera bans — só usado quando não há ID)
        if target_user is None:
            async for ban_entry in ctx.guild.bans(limit=1000):
                u = ban_entry.user
                if (
                    f"{u.name}#{u.discriminator}" == user_input
                    or u.name == user_input
                    or u.display_name == user_input
                ):
                    target_user = u
                    break
            if target_user is None:
                return await ctx.send(
                    "❌ Usuário não encontrado na lista de bans.\n"
                    "💡 Dica: use o **ID numérico** do usuário para uma busca exata e mais rápida.",
                    ephemeral=True
                )

        try:
            await ctx.guild.unban(target_user, reason=f"Unban por {ctx.author}")
            add_mod_log(ctx.guild.id, target_user.id, ctx.author.id, "unban")
            await ctx.send(f"✅ {target_user.mention} (`{target_user.id}`) foi desbanido com sucesso.")
        except discord.Forbidden:
            await ctx.send("❌ Não tenho permissão para desbanir usuários.", ephemeral=True)
        except discord.NotFound:
            await ctx.send("❌ Este usuário não está banido.", ephemeral=True)

    @commands.hybrid_command(name="userinfo", description="Mostra informações de um usuário.")
    async def userinfo(self, ctx: commands.Context, member: discord.Member = None):
        member = member or ctx.author
        
        roles = [role.mention for role in member.roles[1:]] 
        roles_str = " ".join(roles) if roles else "Nenhum"

        embed = discord.Embed(title=f"Informações de {member.display_name}", color=member.color)
        embed.set_thumbnail(url=member.display_avatar.url if member.display_avatar else member.default_avatar.url)
        embed.add_field(name="ID", value=member.id, inline=False)
        embed.add_field(name="Conta Criada", value=member.created_at.strftime("%d/%m/%Y %H:%M:%S"), inline=False)
        # Fix #15 — joined_at pode ser None para membros antigos / cache incompleto
        joined_str = member.joined_at.strftime("%d/%m/%Y %H:%M:%S") if member.joined_at else "Desconhecido"
        embed.add_field(name="Entrou no Servidor", value=joined_str, inline=False)
        embed.add_field(name="Cargos", value=roles_str, inline=False)
        
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Moderation(bot))
