import discord
from discord.ext import commands
from discord import app_commands
import asyncio

from utils.logger import log
from utils.cache import spam_cache, ai_cooldown_cache, config, now_ts

class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._ready_fired = False
        self.cleanup_task = None  # started in cog_load

    async def cleanup_cache_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                self.cleanup_cache()
            except Exception as e:
                log.error(f"Erro na limpeza de cache: {e}")
            await asyncio.sleep(config["cache_cleanup_hours"] * 3600)

    def cleanup_cache(self):
        """Limpa caches em memória.
        Nota: actions_cache é gerenciado pelo anti_nuke (TTL 30s).
        Aqui apenas limpamos o ai_cooldown_cache."""
        now = now_ts()

        # Limpar AI Cooldown (entradas expiradas há mais de 2x o cooldown configurado)
        to_del_ai = [k for k, timestamp in ai_cooldown_cache.items() if (now - timestamp) > config["ai_cooldown"] * 2]
        for k in to_del_ai:
            del ai_cooldown_cache[k]

        if to_del_ai:
            log.debug(f"[Events] Cache AI cooldown limpo: {len(to_del_ai)} entradas removidas.")

    async def cog_load(self):
        self.cleanup_task = self.bot.loop.create_task(self.cleanup_cache_loop())

    def cog_unload(self):
        if self.cleanup_task is not None:
            self.cleanup_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._ready_fired:
            self._ready_fired = True

            log.info(f"Logado como {self.bot.user.name} ({self.bot.user.id})")
            log.info(f"Conectado a {len(self.bot.guilds)} servidores.")

            # Carrega whitelist do banco para memória (por guild)
            try:
                from utils.database import load_whitelist_to_cache
                for guild in self.bot.guilds:
                    load_whitelist_to_cache(guild.id)
                log.info("Whitelist Anti-Nuke carregada do banco.")
            except Exception as e:
                log.warning(f"Falha ao carregar whitelist: {e}")

            # ── Testes de inicialização ─────────────────────────────────────
            from utils.cache import LOGS_CHANNEL_ID, SUPPORT_CHANNEL_ID
            for guild in self.bot.guilds:
                warnings = []
                if LOGS_CHANNEL_ID:
                    ch = guild.get_channel(LOGS_CHANNEL_ID)  # int|None from cache
                    if not ch:
                        warnings.append(f"⚠️ LOGS_CHANNEL_ID `{LOGS_CHANNEL_ID}` não encontrado em `{guild.name}`")
                if SUPPORT_CHANNEL_ID:
                    ch = guild.get_channel(int(SUPPORT_CHANNEL_ID))
                    if not ch:
                        warnings.append(f"⚠️ SUPPORT_CHANNEL_ID `{SUPPORT_CHANNEL_ID}` não encontrado em `{guild.name}`")
                for w in warnings:
                    log.warning(w)

            # Sincroniza slash commands
            try:
                synced = await self.bot.tree.sync()
                log.info(f"Slash commands sincronizados ({len(synced)} comandos).")
            except Exception as e:
                log.error(f"Erro ao sincronizar slash commands: {e}")

            activity = discord.Activity(type=discord.ActivityType.watching, name="Darke Store | .help")
            await self.bot.change_presence(activity=activity)
            self._start_time = discord.utils.utcnow()

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.send("❌ Não tenho permissão para executar esta ação no servidor.", ephemeral=True)
        elif isinstance(error, commands.CommandNotFound):
            pass
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"⚠️ Faltam argumentos. Verifique o uso do comando.", ephemeral=True)
        else:
            log.error(f"Erro em comando de prefixo: {error}")

    async def slash_error_handler(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
        elif isinstance(error, app_commands.BotMissingPermissions):
            await interaction.response.send_message("❌ Não tenho permissão para executar esta ação no servidor.", ephemeral=True)
        elif isinstance(error, app_commands.CommandOnCooldown):
             await interaction.response.send_message(f"⌛ Aguarde {error.retry_after:.1f}s.", ephemeral=True)
        else:
            log.error(f"Erro em slash command: {error}")
            try:
                 await interaction.response.send_message("⚠️ Ocorreu um erro ao processar o comando.", ephemeral=True)
            except discord.InteractionResponded:
                 pass

async def setup(bot):
    events_cog = Events(bot)
    await bot.add_cog(events_cog)
    bot.tree.on_error = events_cog.slash_error_handler
