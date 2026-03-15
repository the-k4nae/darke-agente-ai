import discord
from discord.ext import commands
import asyncio
from utils.logger import log_action, log
from utils.database import get_raid_mode, set_raid_mode, set_config as db_set_config, get_config as db_get_config
import json
from utils.cache import OWNER_ID

class AntiRaid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Armazena permissões originais dos canais durante o modo pânico
        self._original_overrides: dict[int, dict] = {}

    @commands.hybrid_command(name="raid", description="Ativa ou desativa o Modo Pânico (bloqueia todos os canais).")
    @commands.has_permissions(administrator=True)
    async def raid(self, ctx: commands.Context, acao: str):
        """
        .raid on   → Bloqueia TODOS os canais de texto do servidor instantaneamente.
        .raid off  → Restaura as permissões de envio de mensagens.
        """
        acao = acao.lower()

        if acao not in ("on", "off"):
            return await ctx.send("⚠️ Use `.raid on` para ativar ou `.raid off` para desativar.")

        await ctx.defer()

        if acao == "on":
            await self._activate_panic(ctx)
        else:
            await self._deactivate_panic(ctx)

    async def _activate_panic(self, ctx: commands.Context):
        guild = ctx.guild
        if get_raid_mode(guild.id):
            return await ctx.send("⚠️ O Modo Pânico já está **ativo**.")

        blocked = 0
        self._original_overrides[guild.id] = {}

        await ctx.send(
            "🚨 **MODO PÂNICO ATIVADO!** Bloqueando todos os canais...",
        )

        for channel in guild.text_channels:
            try:
                # Salva as permissões originais do @everyone neste canal
                everyone = guild.default_role
                original_perm = channel.overwrites_for(everyone)
                self._original_overrides[guild.id][channel.id] = original_perm

                # Bloqueia envio de mensagens
                overwrite = discord.PermissionOverwrite(send_messages=False)
                await channel.set_permissions(
                    everyone,
                    overwrite=overwrite,
                    reason="🚨 Modo Pânico Ativado"
                )
                blocked += 1
                await asyncio.sleep(0.3)  # Evita rate limit
            except discord.Forbidden:
                log.warning(f"Sem permissão para bloquear #{channel.name}")
            except Exception as e:
                log.error(f"Erro ao bloquear #{channel.name}: {e}")

        set_raid_mode(guild.id, True)
        # Fix #9 — serializa TODOS os campos da PermissionOverwrite, não só 2
        serialized = {}
        _pw_fields = [
            "add_reactions","administrator","attach_files","ban_members","change_nickname",
            "connect","create_instant_invite","deafen_members","embed_links","external_emojis",
            "kick_members","manage_channels","manage_emojis","manage_guild","manage_messages",
            "manage_nicknames","manage_permissions","manage_roles","manage_webhooks",
            "mention_everyone","move_members","mute_members","priority_speaker","read_message_history",
            "read_messages","request_to_speak","send_messages","send_tts_messages","speak",
            "stream","use_application_commands","use_slash_commands","use_voice_activation",
            "view_audit_log","view_channel","view_guild_insights",
        ]
        for ch_id, ow in self._original_overrides.get(guild.id, {}).items():
            serialized[str(ch_id)] = {f: getattr(ow, f, None) for f in _pw_fields}
        db_set_config(guild.id, f"raid_overrides_{guild.id}", json.dumps(serialized))

        embed = discord.Embed(
            title="🚨 MODO PÂNICO ATIVO",
            description=(
                f"**{blocked}** canais foram **bloqueados**.\n\n"
                "Todos os membros foram impedidos de enviar mensagens.\n"
                f"Use `.raid off` quando o servidor estiver seguro."
            ),
            color=discord.Color.dark_red()
        )
        embed.set_footer(text=f"Ativado por {ctx.author}")
        await ctx.send(embed=embed)
        await log_action(
            guild,
            f"🚨 **Modo Pânico ATIVADO** por {ctx.author.mention}. {blocked} canais bloqueados.",
            color=discord.Color.dark_red()
        )

    async def _deactivate_panic(self, ctx: commands.Context):
        guild = ctx.guild
        if not get_raid_mode(guild.id):
            return await ctx.send("✅ O Modo Pânico já está **desativado**.")

        restored = 0
        saved_overrides = self._original_overrides.get(guild.id, {})
        # Fix #7 — se não tiver em memória (bot reiniciou), tenta carregar do DB
        if not saved_overrides:
            raw = db_get_config(guild.id, f"raid_overrides_{guild.id}")
            if raw:
                try:
                    data = json.loads(raw)
                    for ch_id_str, perms in data.items():
                        # Fix #9 — restaura todos os campos serializados
                        ow = discord.PermissionOverwrite(**{
                            k: v for k, v in perms.items() if v is not None
                        })
                        saved_overrides[int(ch_id_str)] = ow
                except Exception:
                    pass

        await ctx.send("🔓 Desativando Modo Pânico e restaurando canais...")

        for channel in guild.text_channels:
            try:
                everyone = guild.default_role
                # Restaura permissão original se tiver salvo, senão reseta para herdado
                original = saved_overrides.get(channel.id)
                if original is not None:
                    await channel.set_permissions(
                        everyone,
                        overwrite=original if original.is_empty() is False else None,
                        reason="🔓 Modo Pânico Desativado"
                    )
                else:
                    # Sem registro: apenas remove o lock de send_messages
                    ow = channel.overwrites_for(everyone)
                    ow.send_messages = None
                    if ow.is_empty():
                        await channel.set_permissions(everyone, overwrite=None, reason="🔓 Modo Pânico Desativado")
                    else:
                        await channel.set_permissions(everyone, overwrite=ow, reason="🔓 Modo Pânico Desativado")
                restored += 1
                await asyncio.sleep(0.3)
            except discord.Forbidden:
                log.warning(f"Sem permissão para restaurar #{channel.name}")
            except Exception as e:
                log.error(f"Erro ao restaurar #{channel.name}: {e}")

        set_raid_mode(guild.id, False)
        self._original_overrides.pop(guild.id, None)

        embed = discord.Embed(
            title="✅ Modo Pânico Desativado",
            description=f"**{restored}** canais foram **restaurados**. O servidor voltou ao normal.",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Desativado por {ctx.author}")
        await ctx.send(embed=embed)
        await log_action(
            guild,
            f"✅ **Modo Pânico DESATIVADO** por {ctx.author.mention}. {restored} canais restaurados.",
            color=discord.Color.green()
        )

    @commands.hybrid_command(name="raidstatus", description="Verifica se o Modo Pânico está ativo.")
    @commands.has_permissions(administrator=True)
    async def raidstatus(self, ctx: commands.Context):
        """Fix #14 — mostra status atual do Modo Pânico."""
        active = get_raid_mode(ctx.guild.id)
        if active:
            embed = discord.Embed(
                title="🚨 Modo Pânico ATIVO",
                description="O servidor está em modo pânico. Todos os canais estão bloqueados.\nUse `.raid off` para desativar.",
                color=discord.Color.dark_red()
            )
        else:
            embed = discord.Embed(
                title="✅ Modo Pânico Inativo",
                description="O servidor está operando normalmente.",
                color=discord.Color.green()
            )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        """Sincroniza estado do modo pânico na reinicialização."""
        for guild in self.bot.guilds:
            if get_raid_mode(guild.id):
                log.warning(f"[{guild.name}] Modo Pânico estava ATIVO ao reiniciar! Use .raid off para desativar.")

async def setup(bot):
    await bot.add_cog(AntiRaid(bot))
