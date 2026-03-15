import discord
from discord.ext import commands
from utils.logger import log
# Fix #11 — importa LOGS_CHANNEL_ID já como int, não como variável estática congelada
from utils.cache import LOGS_CHANNEL_ID

class MessageLogs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _get_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        # Fix #4/#11 — LOGS_CHANNEL_ID já é int, comparação direta
        if not LOGS_CHANNEL_ID:
            return None
        ch = guild.get_channel(LOGS_CHANNEL_ID)
        return ch if isinstance(ch, discord.TextChannel) else None

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or before.guild is None: return
        # Ignora edições onde o conteúdo de texto não mudou.
        # O Discord faz "edições" automáticas quando carrega o preview de links
        # (embeds são adicionados mas o content permanece idêntico) — isso
        # polui o canal de logs com dezenas de eventos falsos por hora.
        if before.content == after.content: return
        # Ignora também edições de conteúdo vazio para vazio (raro mas acontece)
        if not before.content and not after.content: return

        channel = self._get_log_channel(before.guild)
        if not channel: return

        embed = discord.Embed(
            title="✏️ Mensagem Editada",
            color=discord.Color.yellow(),
            timestamp=after.edited_at or discord.utils.utcnow()
        )
        embed.set_author(name=str(before.author), icon_url=before.author.display_avatar.url)
        embed.add_field(name="Canal",  value=before.channel.mention,                   inline=True)
        embed.add_field(name="Autor",  value=before.author.mention,                    inline=True)
        embed.add_field(name="Link",   value=f"[Ir para mensagem]({after.jump_url})",  inline=True)
        embed.add_field(name="📝 Antes",  value=(before.content[:1020] or "*vazio*"), inline=False)
        embed.add_field(name="📝 Depois", value=(after.content[:1020]  or "*vazio*"), inline=False)
        embed.set_footer(text=f"ID usuário: {before.author.id} | ID msg: {before.id}")
        try:
            await channel.send(embed=embed)
        except Exception as e:
            log.error(f"Erro ao registrar edição: {e}")

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or message.guild is None: return

        channel = self._get_log_channel(message.guild)
        if not channel: return

        embed = discord.Embed(
            title="🗑️ Mensagem Deletada",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name="Canal", value=message.channel.mention, inline=True)
        embed.add_field(name="Autor", value=message.author.mention,  inline=True)
        embed.add_field(name="📝 Conteúdo", value=(message.content[:1020] or "*sem texto*"), inline=False)

        if message.attachments:
            attach_list = "\n".join(f"• [{a.filename}]({a.url})" for a in message.attachments[:5])
            embed.add_field(name="📎 Anexos", value=attach_list, inline=False)

        embed.set_footer(text=f"ID usuário: {message.author.id} | ID msg: {message.id}")
        try:
            await channel.send(embed=embed)
        except Exception as e:
            log.error(f"Erro ao registrar deleção: {e}")

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        if not messages: return
        guild = messages[0].guild
        if not guild: return

        channel = self._get_log_channel(guild)
        if not channel: return

        embed = discord.Embed(
            title="🗑️ Limpeza em Massa",
            description=f"**{len(messages)} mensagens** deletadas em {messages[0].channel.mention}.",
            color=discord.Color.dark_red(),
            timestamp=discord.utils.utcnow()
        )
        preview = []
        for m in messages[:8]:
            preview_text = (m.content[:60] + "…") if m.content and len(m.content) > 60 else (m.content or "*media*")
            preview.append(f"**{m.author.name}:** {preview_text}")
        if preview:
            embed.add_field(name="Prévia", value="\n".join(preview), inline=False)
        try:
            await channel.send(embed=embed)
        except Exception as e:
            log.error(f"Erro ao registrar limpeza em massa: {e}")

async def setup(bot):
    await bot.add_cog(MessageLogs(bot))
