import discord
from discord.ext import commands
import asyncio
import time
from utils.logger import log
from utils.cache import LOGS_CHANNEL_ID
from utils.database import log_member_event

class MemberLogs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Fix #12 — debounce para o contador: armazena última atualização por guild
        self._counter_last_update: dict[int, float] = {}
        self._counter_pending:     dict[int, asyncio.Task] = {}
    
    _COUNTER_DEBOUNCE = 30  # segundos — agrupa múltiplos joins/leaves

    def _get_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        # Fix #4 — LOGS_CHANNEL_ID já é int, sem int() em runtime
        if not LOGS_CHANNEL_ID:
            return None
        ch = guild.get_channel(LOGS_CHANNEL_ID)
        return ch if isinstance(ch, discord.TextChannel) else None

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        channel = self._get_log_channel(member.guild)
        if not channel:
            return

        embed = discord.Embed(
            title="📥 Membro Entrou",
            description=f"{member.mention} entrou no servidor!",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Conta criada em", value=member.created_at.strftime("%d/%m/%Y %H:%M"), inline=True)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.set_footer(text=f"Total de membros: {member.guild.member_count}")
        try:
            await channel.send(embed=embed)
        except Exception as e:
            log.error(f"Erro ao enviar log de entrada: {e}")

        await self.update_member_counter(member.guild)
        try:
            log_member_event(member.guild.id, member.id, 'join')
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        channel = self._get_log_channel(member.guild)
        if not channel:
            return

        embed = discord.Embed(
            title="📤 Membro Saiu",
            description=f"**{member.name}** saiu do servidor.",
            color=discord.Color.red()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(
            name="Cargos que tinha",
            value=", ".join(r.name for r in member.roles[1:]) or "Nenhum",
            inline=False
        )
        embed.set_footer(text=f"Total de membros: {member.guild.member_count}")
        try:
            await channel.send(embed=embed)
        except Exception as e:
            log.error(f"Erro ao enviar log de saída: {e}")

        await self.update_member_counter(member.guild)
        try:
            log_member_event(member.guild.id, member.id, 'leave')
        except Exception:
            pass

    async def update_member_counter(self, guild: discord.Guild):
        """Fix #12 — debounce de 30s para evitar rate-limit em eventos de massa."""
        gid = guild.id
        # Cancela tarefa pendente anterior (se houver) e agenda nova
        if gid in self._counter_pending:
            self._counter_pending[gid].cancel()
        self._counter_pending[gid] = asyncio.create_task(
            self._do_counter_update(guild)
        )

    async def _do_counter_update(self, guild: discord.Guild):
        """Executa a atualização real do contador após o debounce."""
        await asyncio.sleep(self._COUNTER_DEBOUNCE)
        for vc in guild.voice_channels:
            if vc.name.startswith("👥 Membros:"):
                try:
                    await vc.edit(name=f"👥 Membros: {guild.member_count}", reason="Auto-contador")
                    self._counter_last_update[guild.id] = time.time()
                except discord.Forbidden:
                    log.warning(f"Sem permissão para editar canal contador em {guild.name}")
                except discord.HTTPException as e:
                    if e.status == 429:
                        log.warning(f"[MemberLogs] Rate-limit ao atualizar contador em {guild.name}")
                    else:
                        log.error(f"Erro ao atualizar contador: {e}")
                break

    @commands.hybrid_command(name="setupcounter", description="Cria o canal de voz do contador de membros.")
    @commands.has_permissions(manage_channels=True)
    async def setupcounter(self, ctx: commands.Context):
        from utils.cache import OWNER_ID
        if str(ctx.author.id) != str(OWNER_ID):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)

        for vc in ctx.guild.voice_channels:
            if vc.name.startswith("👥 Membros:"):
                await vc.delete(reason="Recriando contador")

        overwrites = {ctx.guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True)}
        new_vc = await ctx.guild.create_voice_channel(
            name=f"👥 Membros: {ctx.guild.member_count}",
            overwrites=overwrites,
            reason="Canal contador de membros"
        )
        await ctx.send(f"✅ Canal criado: {new_vc.mention}. Atualiza automaticamente!")

async def setup(bot):
    await bot.add_cog(MemberLogs(bot))
