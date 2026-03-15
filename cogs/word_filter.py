import discord
from discord.ext import commands
import re
import time
from utils.logger import log_action, log
from utils.database import get_word_filter, add_word_filter, remove_word_filter, get_config, set_config

# Fix #5 — cache de pattern por guild_id com TTL de 60s para refletir mudanças
_pattern_cache: dict[int, tuple[float, re.Pattern | None]] = {}
_PATTERN_TTL = 60  # segundos

def _get_pattern(guild_id: int) -> re.Pattern | None:
    now = time.time()
    cached = _pattern_cache.get(guild_id)
    if cached and (now - cached[0]) < _PATTERN_TTL:
        return cached[1]

    words = get_word_filter(guild_id)
    if not words:
        _pattern_cache[guild_id] = (now, None)
        return None

    escaped = [re.escape(w) for w in words]
    pattern = re.compile(r'\b(' + '|'.join(escaped) + r')\b', re.IGNORECASE)
    _pattern_cache[guild_id] = (now, pattern)
    return pattern

def invalidate_pattern_cache(guild_id: int):
    """Chamado quando a blacklist é alterada para forçar rebuild imediato."""
    _pattern_cache.pop(guild_id, None)


class WordFilter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _check_and_act(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if get_config(message.guild.id, "word_filter_enabled", "") not in ("true", "1", "on"):
            return
        # Deserializa a lista de cargos isentos do banco (armazenada como JSON string)
        import json as _json
        raw_exempt = get_config(message.guild.id, "word_filter_exempt_roles", "") or "[]"
        try:
            exempt_roles = _json.loads(raw_exempt) if isinstance(raw_exempt, str) else []
        except (ValueError, TypeError):
            exempt_roles = []
        if any(r.id in exempt_roles for r in message.author.roles):
            return

        pattern = _get_pattern(message.guild.id)
        if not pattern:
            return

        if pattern.search(message.content):
            try:
                await message.delete()
                await message.channel.send(
                    f"🚫 {message.author.mention}, sua mensagem contém conteúdo não permitido.",
                    delete_after=6
                )
                await log_action(
                    message.guild,
                    f"🚫 **Filtro:** {message.author.mention} tentou enviar mensagem proibida em {message.channel.mention}.",
                    color=discord.Color.orange()
                )
            except discord.Forbidden:
                log.warning("Sem permissão para deletar mensagem filtrada.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self._check_and_act(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # Fix #7 — ignora edições onde o conteúdo não mudou (pins, embeds, etc.)
        if before.content == after.content:
            return
        await self._check_and_act(after)

    # ── Comandos ──────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="filtro", description="Liga/desliga o filtro de palavras.")
    @commands.has_permissions(administrator=True)
    async def filtro(self, ctx: commands.Context, acao: str):
        acao = acao.lower()
        if acao in ("on", "ativo"):
            set_config(ctx.guild.id, "word_filter_enabled", "true")
            await ctx.send("✅ Filtro de palavras **ativado**.")
        elif acao in ("off", "desativado"):
            set_config(ctx.guild.id, "word_filter_enabled", "false")
            await ctx.send("✅ Filtro de palavras **desativado**.")
        else:
            await ctx.send("⚠️ Use `.filtro on` ou `.filtro off`.")

    @commands.hybrid_command(name="addfiltro", description="Adiciona uma palavra ao filtro.")
    @commands.has_permissions(administrator=True)
    async def addfiltro(self, ctx: commands.Context, *, palavra: str):
        word = palavra.lower().strip()
        if add_word_filter(ctx.guild.id, word):
            invalidate_pattern_cache(ctx.guild.id)  # Fix #5 — força rebuild imediato
            await ctx.send(f"✅ Palavra `{word}` adicionada ao filtro.")
        else:
            await ctx.send(f"⚠️ A palavra `{word}` já está no filtro.")

    @commands.hybrid_command(name="removerfiltro", description="Remove uma palavra do filtro.")
    @commands.has_permissions(administrator=True)
    async def removerfiltro(self, ctx: commands.Context, *, palavra: str):
        word = palavra.lower().strip()
        if remove_word_filter(ctx.guild.id, word):
            invalidate_pattern_cache(ctx.guild.id)  # Fix #5
            await ctx.send(f"✅ Palavra `{word}` removida do filtro.")
        else:
            await ctx.send(f"❌ Palavra `{word}` não encontrada no filtro.")

    @commands.hybrid_command(name="listarfiltro", description="Lista todas as palavras no filtro.")
    @commands.has_permissions(administrator=True)
    async def listarfiltro(self, ctx: commands.Context):
        words = get_word_filter(ctx.guild.id)
        if not words:
            return await ctx.send("ℹ️ Nenhuma palavra no filtro.", ephemeral=True)
        embed = discord.Embed(
            title="🚫 Filtro de Palavras",
            description="\n".join(f"• `{w}`" for w in words),
            color=discord.Color.dark_red()
        )
        embed.set_footer(text=f"Total: {len(words)} palavra(s)")
        await ctx.send(embed=embed, ephemeral=True)

    # ── AutoMod API (Para a Insígnia) ──────────────────────────────────────────

    @commands.hybrid_group(name="automod", description="Gerencia as regras de AutoMod oficial do Discord.")
    @commands.has_permissions(administrator=True)
    async def automod(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send("⚠️ Use `.automod setup` ou `.automod status`.")

    @automod.command(name="setup", description="Configura a regra de AutoMod para ganhar a insígnia.")
    async def automod_setup(self, ctx: commands.Context):
        """Cria uma regra de AutoMod oficial no servidor."""
        await ctx.defer()
        
        # Palavras iniciais seguras para a regra
        initial_words = ["*invites*", "*links*", "scam", "hack"]
        
        try:
            # Tenta encontrar regra existente criada pelo bot
            existing_rules = await ctx.guild.fetch_automod_rules()
            for rule in existing_rules:
                if rule.name == "Darke Store - Filtro Automático":
                    return await ctx.send("✅ A regra de AutoMod já existe neste servidor!")

            # Cria a regra oficial
            # Trigger: KEYWORD (palavras-chave)
            # Action: BlockMessage (bloqueia a mensagem)
            rule = await ctx.guild.create_automod_rule(
                name="Darke Store - Filtro Automático",
                event_type=discord.AutoModRuleEventType.message_send,
                
                trigger=discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.keyword, keyword_filter=initial_words),
                actions=[discord.AutoModRuleAction(type=discord.AutoModRuleActionType.block_message)],
                enabled=True,
                reason=f"Configuração de AutoMod solicitada por {ctx.author}"
            )
            
            embed = discord.Embed(
                title="🛡️ AutoMod Configurado!",
                description=(
                    "A regra oficial do Discord foi criada com sucesso.\n\n"
                    "✨ **O que isso significa?**\n"
                    "1. O bot agora usa a API nativa do Discord para moderação.\n"
                    "2. Você se tornou elegível para a insígnia de **'Usa AutoMod'** no perfil do bot.\n"
                    "3. Mensagens perigosas são bloqueadas instantaneamente pelo Discord."
                ),
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
            log.info(f"[AutoMod] Regra criada no servidor {ctx.guild.name} ({ctx.guild.id})")
            
        except discord.Forbidden:
            await ctx.send("❌ Eu não tenho permissão de `Gerenciar Servidor` para configurar o AutoMod.")
        except Exception as e:
            log.error(f"[AutoMod] Erro ao configurar: {e}")
            await ctx.send(f"❌ Ocorreu um erro ao configurar o AutoMod: `{e}`")

    @automod.command(name="status", description="Mostra o status das regras de AutoMod.")
    async def automod_status(self, ctx: commands.Context):
        try:
            rules = await ctx.guild.fetch_automod_rules()
            if not rules:
                return await ctx.send("ℹ️ Nenhuma regra de AutoMod ativa neste servidor.")
            
            embed = discord.Embed(title="🛡️ Regras de AutoMod Ativas", color=discord.Color.blurple())
            for rule in rules:
                status = "✅ Ativa" if rule.enabled else "❌ Desativada"
                embed.add_field(name=rule.name, value=f"Status: {status}\nTipo: {rule.trigger.type.name}", inline=False)
            
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"❌ Erro ao buscar regras: `{e}`")

async def setup(bot):
    await bot.add_cog(WordFilter(bot))
