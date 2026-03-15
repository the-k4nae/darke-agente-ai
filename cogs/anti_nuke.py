import discord
from discord.ext import commands
import time
from collections import defaultdict, deque
import asyncio

from utils.logger import log_action, log
from utils.cache import is_whitelisted, config, actions_cache, spam_cache, now_ts

# TTL para entradas do actions_cache (segundos) — evita crescimento ilimitado em memória
_CACHE_TTL = 30

class AntiNuke(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._action_counts: dict[str, dict] = {
            "channel_delete": defaultdict(list),
            "role_delete":    defaultdict(list),
            "member_ban":     defaultdict(list),
            "member_kick":    defaultdict(list),
            "webhook_create": defaultdict(list),
        }
        self._cache_cleanup_task = None

    async def cog_load(self):
        self._cache_cleanup_task = asyncio.create_task(self._actions_cache_cleaner())

    def cog_unload(self):
        if self._cache_cleanup_task:
            self._cache_cleanup_task.cancel()

    async def _actions_cache_cleaner(self):
        """Remove entradas antigas do actions_cache a cada 60s para evitar vazamento de memória."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await asyncio.sleep(60)
            now = now_ts()
            expired = [k for k, ts in list(actions_cache.items()) if (now - ts) > _CACHE_TTL]
            for k in expired:
                actions_cache.pop(k, None)
            if expired:
                log.debug(f"[AntiNuke] Cache limpo: {len(expired)} entradas expiradas removidas.")

    def _get_history(self, action_type: str, user_id: int) -> list:
        """Retorna o histórico de timestamps e aplica o limit atual do config."""
        if action_type not in self._action_counts:
            self._action_counts[action_type] = defaultdict(list)
        hist = self._action_counts[action_type][user_id]
        limit = config.get("history_limit", 3)
        if len(hist) > limit:
            self._action_counts[action_type][user_id] = hist[-limit:]
        return self._action_counts[action_type][user_id]

    async def handle_anti_nuke(self, executor, guild: discord.Guild, action_type: str):
        if not executor or is_whitelisted(executor.id):
            return
        if executor.id == guild.me.id:
            return

        now      = now_ts()
        history  = self._get_history(action_type, executor.id)
        history.append(now)

        limit     = config.get("history_limit", 3)
        threshold = config.get("raid_threshold", 10)

        if len(history) >= limit:
            time_diff = history[-1] - history[0]
            if time_diff <= threshold:
                try:
                    try:
                        await executor.send(
                            "🔨 Você foi **banido automaticamente** do servidor "
                            f"**{guild.name}**.\n"
                            f"**Motivo:** Atividade suspeita detectada pelo sistema Anti-Nuke (`{action_type}`).\n"
                            "Se acredita que foi um engano, entre em contato com a administração."
                        )
                    except Exception:
                        pass
                    await guild.ban(executor, reason=f"Anti-Nuke: Abuso detectado ({action_type})")
                    await log_action(guild, f"🔨 **Anti-Nuke:** {executor.mention} (`{executor.id}`) foi **BANIDO** por nuke/raid (`{action_type}`).")
                except discord.Forbidden:
                    await log_action(guild, f"⚠️ **Falha:** Tentei banir {executor.mention} por nuke, mas não tenho permissão.")
                except Exception as e:
                    log.error(f"Erro ao aplicar Anti-Nuke em {executor}: {e}")
                finally:
                    self._action_counts[action_type][executor.id].clear()

    def _bot_action(self, cache_key: str) -> bool:
        """Verifica e registra no cache se esta ação foi disparada pelo próprio bot (evita loops)."""
        if cache_key in actions_cache:
            return True
        actions_cache[cache_key] = now_ts()
        return False

    # ── Canal deletado ────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if not config.get("anti_delete_channels", True): return
        if self._bot_action(f"channel_delete_{channel.id}"): return

        guild = channel.guild
        try:
            logs = [e async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete)]
            if not logs: return
            executor = logs[0].user
            if is_whitelisted(executor.id) or executor.id == guild.me.id: return

            new_ch = await channel.clone(reason="Anti-Nuke: Restauração de canal deletado.")
            actions_cache[f"channel_create_{new_ch.id}"] = now_ts()
            await new_ch.edit(position=channel.position)
            await log_action(guild, f"🛡️ **Anti-Delete:** Canal `#{channel.name}` deletado por {executor.mention} foi **recriado**.")
            await self.handle_anti_nuke(executor, guild, "channel_delete")
        except Exception as e:
            log.error(f"Erro no Anti-Delete (Canal): {e}")

    # ── Canal criado ──────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        if not config.get("anti_create_channels", True): return
        if self._bot_action(f"channel_create_{channel.id}"): return

        guild = channel.guild
        try:
            logs = [e async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_create)]
            if not logs: return
            executor = logs[0].user
            if is_whitelisted(executor.id) or executor.id == guild.me.id: return

            actions_cache[f"channel_delete_{channel.id}"] = now_ts()
            await channel.delete(reason="Anti-Nuke: Canal criado sem autorização.")
            await log_action(guild, f"🛡️ **Anti-Create:** Canal `#{channel.name}` criado por {executor.mention} foi **deletado**.")
        except Exception as e:
            log.error(f"Erro no Anti-Create (Canal): {e}")

    # ── Cargo deletado ────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        if not config.get("anti_delete_roles", True): return
        if self._bot_action(f"role_delete_{role.id}"): return

        guild = role.guild
        try:
            await asyncio.sleep(2)
            logs = [e async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete)]
            if not logs: return
            executor = logs[0].user
            if is_whitelisted(executor.id) or executor.id == guild.me.id: return

            new_role = await guild.create_role(
                name=role.name,
                permissions=role.permissions,
                color=role.color,
                hoist=role.hoist,
                mentionable=role.mentionable,
                reason="Anti-Nuke: Restauração de cargo deletado."
            )
            actions_cache[f"role_create_{new_role.id}"] = now_ts()
            await new_role.edit(position=role.position)
            await log_action(guild, f"🛡️ **Anti-Delete:** Cargo `{role.name}` deletado por {executor.mention} foi **recriado**.")
            await self.handle_anti_nuke(executor, guild, "role_delete")
        except Exception as e:
            log.error(f"Erro no Anti-Delete (Cargo): {e}")

    # ── Cargo criado ──────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        if not config.get("anti_create_roles", True): return
        if self._bot_action(f"role_create_{role.id}"): return

        guild = role.guild
        try:
            await asyncio.sleep(2)
            logs = [e async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.role_create)]
            if not logs: return
            executor = logs[0].user
            if is_whitelisted(executor.id) or executor.id == guild.me.id: return

            actions_cache[f"role_delete_{role.id}"] = now_ts()
            await role.delete(reason="Anti-Nuke: Cargo criado sem autorização.")
            await log_action(guild, f"🛡️ **Anti-Create:** Cargo `{role.name}` criado por {executor.mention} foi **deletado**.")
        except Exception as e:
            log.error(f"Erro no Anti-Create (Cargo): {e}")

    # ── Mass-ban ──────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        if not config.get("anti_mass_ban", True): return
        await asyncio.sleep(1)
        try:
            logs = [e async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban)]
            if not logs: return
            executor = logs[0].user
            if is_whitelisted(executor.id) or executor.id == guild.me.id: return

            await log_action(guild, f"🚨 **Mass-Ban detectado:** {executor.mention} baniu {user.mention}.")
            await self.handle_anti_nuke(executor, guild, "member_ban")
        except Exception as e:
            log.error(f"Erro no Anti-Mass-Ban: {e}")

    # ── Mass-kick ─────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not config.get("anti_mass_kick", True): return
        guild = member.guild
        await asyncio.sleep(1)
        try:
            logs = [e async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.kick)]
            if not logs: return
            entry = logs[0]
            if not entry.target or entry.target.id != member.id: return
            executor = entry.user
            if is_whitelisted(executor.id) or executor.id == guild.me.id: return

            await log_action(guild, f"🚨 **Mass-Kick detectado:** {executor.mention} expulsou {member.mention}.")
            await self.handle_anti_nuke(executor, guild, "member_kick")
        except Exception as e:
            log.error(f"Erro no Anti-Mass-Kick: {e}")

    # ── Webhook criado em massa ───────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        if not config.get("anti_webhook_spam", True): return
        guild = channel.guild
        await asyncio.sleep(1)
        try:
            logs = [e async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.webhook_create)]
            if not logs: return
            executor = logs[0].user
            if is_whitelisted(executor.id) or executor.id == guild.me.id: return

            # Deleta o webhook recém-criado
            webhooks = await channel.webhooks()
            for wh in webhooks:
                if wh.user and wh.user.id == executor.id:
                    await wh.delete(reason="Anti-Nuke: Webhook não autorizado removido.")

            await log_action(guild, f"🛡️ **Anti-Webhook:** Webhook criado por {executor.mention} em {channel.mention} foi **removido**.")
            await self.handle_anti_nuke(executor, guild, "webhook_create")
        except Exception as e:
            log.error(f"Erro no Anti-Webhook: {e}")

    # ── Alteração de nome/ícone do servidor ───────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        if not config.get("anti_guild_update", True): return
        # Só age se nome ou ícone mudaram
        if before.name == after.name and before.icon == after.icon: return

        guild = after
        await asyncio.sleep(1)
        try:
            logs = [e async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.guild_update)]
            if not logs: return
            executor = logs[0].user
            if is_whitelisted(executor.id) or executor.id == guild.me.id: return

            changes = []
            if before.name != after.name:
                changes.append(f"nome: `{before.name}` → `{after.name}`")
            if before.icon != after.icon:
                changes.append("ícone alterado")

            await log_action(
                guild,
                f"⚠️ **Anti-Guild-Update:** {executor.mention} modificou o servidor ({', '.join(changes)}). Revise se necessário."
            )
        except Exception as e:
            log.error(f"Erro no Anti-Guild-Update: {e}")

    # ── Bot adicionado ────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not config.get("anti_add_bots", True): return
        if not member.bot: return

        guild = member.guild
        await asyncio.sleep(2)
        try:
            logs = [e async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.bot_add)]
            if not logs: return
            entry    = logs[0]
            executor = entry.user
            if entry.target and entry.target.id == member.id:
                if is_whitelisted(executor.id): return
                await member.kick(reason=f"Anti-Bot: Bot não autorizado adicionado por {executor}.")
                await guild.ban(executor, reason="Anti-Bot: Adição de bot não autorizada.")
                await log_action(guild, f"🛡️ **Anti-Bot:** Bot `{member.name}` removido. {executor.mention} foi **BANIDO**.")
        except Exception as e:
            log.error(f"Erro no Anti-Bot: {e}")

    # ── Anti-Spam ─────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None: return
        if not config.get("anti_spam", True): return
        if is_whitelisted(message.author.id): return

        u_id    = message.author.id
        now     = now_ts()
        history = spam_cache[u_id]

        while history and (now - history[0]) > config.get("spam_time", 5.0):
            history.popleft()
        history.append(now)

        if len(history) >= config.get("spam_limit", 5):
            try:
                from datetime import timedelta
                mute_min = config.get("mute_duration_spam", 5)
                await message.author.timeout(timedelta(minutes=mute_min), reason="Anti-Spam")
                await log_action(
                    message.guild,
                    f"🛡️ **Anti-Spam:** {message.author.mention} mutado por {mute_min} min (excesso de mensagens)."
                )
                history.clear()
                try:
                    await message.channel.purge(
                        limit=config.get("spam_limit", 5),
                        check=lambda m: m.author == message.author
                    )
                except discord.Forbidden:
                    pass
            except discord.Forbidden:
                pass


async def setup(bot):
    await bot.add_cog(AntiNuke(bot))
