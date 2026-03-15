import discord
from discord.ext import commands, tasks
import re
import time
import json as _json
from collections import defaultdict, deque
from datetime import timedelta
from utils.logger import log_action, log
from utils.cache import is_whitelisted
from utils.database import get_config, set_config

# Cache para o rate de mensagens por canal (auto-slowmode)
# slowmode_channel_cache[channel_id] = deque de timestamps
slowmode_channel_cache: dict[int, deque] = defaultdict(lambda: deque(maxlen=20))

# Expressão regular para detectar qualquer URL/link
URL_REGEX = re.compile(
    r'(https?://[^\s]+|www\.[^\s]+|discord\.gg/[^\s]+)',
    re.IGNORECASE
)

# Fix #9 — mention_cache com TTL: armazena (timestamp, count) para expirar automaticamente
mention_cache: dict[int, list] = defaultdict(list)   # [(timestamp, mention_count), ...]
_MENTION_TTL = 30  # segundos — janela de acumulação de menções

# Canal e cargos whitelist para Anti-Link (persistidos em memória, configuráveis)
# NOTA: Salvo em config como "anti_link_channels": [id1, id2] e "anti_link_whitelist_roles": [role_id1]
ALLOWED_LINK_DOMAINS = [
    "discord.com", "discord.gg",
    "youtube.com", "youtu.be",
    "darke.shop",
    "optifine.net",
    "login.live.com",
    "account.live.com",
    "account.microsoft.com",
    "microsoft.com",
    "notletters.com",
    "firstmail.ltd",
    "instagram.com",
    "whatsapp.com",
    "github.com",
]

def is_allowed_link(url: str) -> bool:
    """Retorna True se o domínio do link está na whitelist."""
    for domain in ALLOWED_LINK_DOMAINS:
        if domain in url.lower():
            return True
    return False

def has_whitelisted_role(member: discord.Member) -> bool:
    """Verifica se o membro tem algum cargo que está na whitelist do Anti-Link."""
    raw = get_config(member.guild.id, "anti_link_whitelist_roles", "")
    # Fix #4 — deserializa lista do banco (armazenada como JSON string)
    try:
        whitelist_roles = _json.loads(raw) if raw else []
    except (ValueError, TypeError):
        whitelist_roles = []
    return any(role.id in whitelist_roles for role in member.roles)

class Safety(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._slowmode_active: set[int] = set()  # Canais com slowmode ativo pelo bot
        self._cache_cleanup.start()

    def cog_unload(self):
        self._cache_cleanup.cancel()

    @tasks.loop(minutes=30)
    async def _cache_cleanup(self):
        """Fix #3 — remove entradas de canais inexistentes do slowmode_channel_cache."""
        valid_ids = {ch.id for guild in self.bot.guilds for ch in guild.text_channels}
        stale = [cid for cid in list(slowmode_channel_cache.keys()) if cid not in valid_ids]
        for cid in stale:
            slowmode_channel_cache.pop(cid, None)
            self._slowmode_active.discard(cid)
        if stale:
            log.debug(f"[Safety] Cache limpo: {len(stale)} canal(is) inválido(s) removido(s).")

    @_cache_cleanup.before_loop
    async def _before_cleanup(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if is_whitelisted(message.author.id):
            return

        author = message.author
        guild_id = message.guild.id

        # ─── Auto Slowmode ──────────────────────────────────────────────────
        # Rastreia quantidade de msgs no canal nos úiltimos 5s
        slowmode_threshold = int(get_config(guild_id, "slowmode_threshold", "8") or 8)
        slowmode_delay = int(get_config(guild_id, "slowmode_delay", "5") or 5)
        now = time.time()

        ch_history = slowmode_channel_cache[message.channel.id]
        # Descarta timestamps mais antigos que 5 segundos
        while ch_history and (now - ch_history[0]) > 5:
            ch_history.popleft()
        ch_history.append(now)

        channel_is_active = message.channel.id in self._slowmode_active

        if len(ch_history) >= slowmode_threshold and not channel_is_active:
            # Ativa slowmode no canal
            try:
                await message.channel.edit(slowmode_delay=slowmode_delay, reason="Auto-Slowmode: flood detectado")
                self._slowmode_active.add(message.channel.id)
                await log_action(
                    message.guild,
                    f"🐢 **Auto-Slowmode:** {message.channel.mention} entrou em slowmode de {slowmode_delay}s (flood detectado: {len(ch_history)} msgs/5s).",
                    color=discord.Color.orange()
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                log.error(f"Erro ao ativar slowmode: {e}")
        elif len(ch_history) < (slowmode_threshold // 2) and channel_is_active:
            # Desativa slowmode quando o fluxo normalizar
            try:
                await message.channel.edit(slowmode_delay=0, reason="Auto-Slowmode: fluxo normalizado")
                self._slowmode_active.discard(message.channel.id)
                await log_action(
                    message.guild,
                    f"✅ **Auto-Slowmode:** {message.channel.mention} voltou ao normal.",
                    color=discord.Color.green()
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                log.error(f"Erro ao desativar slowmode: {e}")

        # ─── Anti-Mention Spam ───────────────────────────────────────────────
        if get_config(guild_id, "anti_mention_spam", "") in ("true", "1", "on"):
            mention_count = len(message.mentions) + len(message.role_mentions)
            if mention_count > 0:
                # Fix #9 — expirar entradas antigas antes de somar
                history = mention_cache[author.id]
                cutoff = now - _MENTION_TTL
                mention_cache[author.id] = [(ts, c) for ts, c in history if ts > cutoff]
                mention_cache[author.id].append((now, mention_count))
                total_mentions = sum(c for _, c in mention_cache[author.id])

                mention_limit = int(get_config(guild_id, "mention_limit", "5") or 5)
                if total_mentions >= mention_limit:
                    try:
                        await message.delete()
                        await author.timeout(timedelta(minutes=10), reason="Anti-Mention Spam")
                        await log_action(
                            message.guild,
                            f"🛡️ **Anti-Mention Spam:** {author.mention} silenciado por 10 min ({total_mentions} menções em {_MENTION_TTL}s).",
                            color=discord.Color.orange()
                        )
                        mention_cache[author.id].clear()
                        return
                    except (discord.Forbidden, discord.HTTPException) as e:
                        log.error(f"Erro Anti-Mention Spam: {e}")

        # ─── Anti-Link ────────────────────────────────────────────────────────
        if get_config(guild_id, "anti_link", "") in ("true", "1", "on"):
            _raw_channels = get_config(guild_id, "anti_link_channels", "")
            try:
                anti_link_channels = _json.loads(_raw_channels) if _raw_channels else []
            except (ValueError, TypeError):
                anti_link_channels = []
            if anti_link_channels and message.channel.id not in anti_link_channels:
                return

            # Libera quem tem cargos na whitelist
            if has_whitelisted_role(author):
                return

            urls = URL_REGEX.findall(message.content)
            blocked_urls = [u for u in urls if not is_allowed_link(u)]

            if blocked_urls:
                try:
                    await message.delete()
                    warn_msg = await message.channel.send(
                        f"🚫 {author.mention}, links externos não são permitidos neste canal.",
                        delete_after=8
                    )
                    await log_action(
                        message.guild,
                        f"🛡️ **Anti-Link:** {author.mention} tentou enviar link(s) bloqueado(s): `{', '.join(blocked_urls[:3])}`",
                        color=discord.Color.orange()
                    )
                except (discord.Forbidden, discord.HTTPException) as e:
                    log.error(f"Erro Anti-Link: {e}")

    # ─── Comandos de configuração do Anti-Link & Slowmode ─────────────────────

    @commands.hybrid_command(name="antilink", description="Ativa ou desativa o Anti-Link no servidor.")
    @commands.has_permissions(administrator=True)
    async def antilink(self, ctx: commands.Context, acao: str):
        """Liga/desliga o Anti-Link. Uso: .antilink on | off"""
        acao = acao.lower()
        if acao in ("on", "ativo", "true"):
            set_config(ctx.guild.id, "anti_link", "true")
            await ctx.send("✅ Anti-Link **ativado**. Links externos serão bloqueados.")
        elif acao in ("off", "desativado", "false"):
            set_config(ctx.guild.id, "anti_link", "false")
            await ctx.send("✅ Anti-Link **desativado**.")
        else:
            await ctx.send("⚠️ Use `.antilink on` ou `.antilink off`.")

    @commands.hybrid_command(name="antilinkcanal", description="Define quais canais o Anti-Link monitora. Sem argumentos = todos.")
    @commands.has_permissions(administrator=True)
    async def antilinkcanal(self, ctx: commands.Context, canal: discord.TextChannel = None):
        if canal is None:
            set_config(ctx.guild.id, "anti_link_channels", "[]")
            return await ctx.send("✅ Anti-Link agora monitora **todos os canais**.")
        raw = get_config(ctx.guild.id, "anti_link_channels", "")
        try:
            channels: list = _json.loads(raw) if raw else []
        except (ValueError, TypeError):
            channels = []
        if canal.id in channels:
            channels.remove(canal.id)
            await ctx.send(f"✅ {canal.mention} **removido** do monitoramento do Anti-Link.")
        else:
            channels.append(canal.id)
            await ctx.send(f"✅ {canal.mention} **adicionado** ao monitoramento do Anti-Link.")
        set_config(ctx.guild.id, "anti_link_channels", _json.dumps(channels))

    @commands.hybrid_command(name="slowmode", description="Configura o Auto-Slowmode do servidor.")
    @commands.has_permissions(administrator=True)
    async def slowmode_config(self, ctx: commands.Context, threshold: int = None, delay: int = None):
        """
        Configura o Auto-Slowmode.
        `threshold` = número de msgs em 5s para ativar (padrão: 8)
        `delay` = segundos de slowmode aplicados (padrão: 5)
        Sem argumentos: mostra a configuração atual.
        """
        guild_id = ctx.guild.id
        if threshold is None and delay is None:
            current_t = int(get_config(guild_id, "slowmode_threshold", "8") or 8)
            current_d = int(get_config(guild_id, "slowmode_delay", "5") or 5)
            return await ctx.send(
                f"🐢 **Auto-Slowmode:** ativa com **{current_t}** msgs/5s → aplica **{current_d}s** de slowmode."
            )
        if threshold:
            set_config(guild_id, "slowmode_threshold", str(threshold))
        if delay:
            set_config(guild_id, "slowmode_delay", str(delay))
        await ctx.send(
            f"✅ Auto-Slowmode atualizado: **{threshold or get_config(guild_id, 'slowmode_threshold', '8')}** msgs/5s → **{delay or get_config(guild_id, 'slowmode_delay', '5')}s**."
        )

async def setup(bot):
    await bot.add_cog(Safety(bot))
