import discord
from discord.ext import commands

from utils.cache import config, save_config, NUKE_WHITELIST, OWNER_ID
from utils.database import (
    set_config as db_set_config,
    add_to_whitelist, remove_from_whitelist, get_whitelist
)

# ── Painel Interativo ─────────────────────────────────────────────────────────

TOGGLE_KEYS = [
    ("anti_delete_channels", "Anti-Delete Canais"),
    ("anti_create_channels", "Anti-Create Canais"),
    ("anti_delete_roles",    "Anti-Delete Cargos"),
    ("anti_create_roles",    "Anti-Create Cargos"),
    ("anti_spam",            "Anti-Spam"),
    ("anti_add_bots",        "Anti-Bot"),
    ("anti_mention_spam",    "Anti-Mention Spam"),
    ("anti_link",            "Anti-Link"),
    ("anti_mass_ban",        "Anti-Mass-Ban"),
    ("anti_mass_kick",       "Anti-Mass-Kick"),
    ("anti_webhook_spam",    "Anti-Webhook Spam"),
    ("anti_guild_update",    "Anti-Guild-Update"),
]

def _build_painel_embed() -> discord.Embed:
    embed = discord.Embed(title="🛡️ Painel de Segurança", color=discord.Color.dark_theme())
    lines = []
    for key, label in TOGGLE_KEYS:
        val   = config.get(key, True)
        icon  = "✅" if val else "❌"
        lines.append(f"{icon} **{label}**")
    embed.add_field(name="Proteções", value="\n".join(lines), inline=False)

    regras = (
        f"> **Limite Raid:** {config.get('history_limit',3)} ações em {config.get('raid_threshold',10)}s\n"
        f"> **Limite Spam:** {config.get('spam_limit',5)} msgs em {config.get('spam_time',5.0)}s\n"
        f"> **Mute Spam:** {config.get('mute_duration_spam',5)} min\n"
        f"> **Cooldown IA:** {config.get('ai_cooldown',8)}s\n"
        f"> **Slowmode:** {config.get('slowmode_threshold',8)} msgs/5s → {config.get('slowmode_delay',5)}s\n"
    )
    embed.add_field(name="Regras Numéricas", value=regras, inline=False)
    embed.set_footer(text="Clique nos botões para ligar/desligar cada proteção.")
    return embed


class PainelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    async def _toggle(self, interaction: discord.Interaction, key: str):
        # Fix #6 — apenas o dono pode clicar nos botões do painel
        if str(interaction.user.id) != str(OWNER_ID):
            return await interaction.response.send_message("❌ Apenas o dono pode usar este painel.", ephemeral=True)
        config[key] = not config.get(key, True)
        db_set_config(0, key, str(config[key]))
        save_config()  # persiste o dict config inteiro no bot_state para sobreviver restart
        embed = _build_painel_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Anti-Delete Canais", style=discord.ButtonStyle.secondary, row=0)
    async def t1(self, i, b): await self._toggle(i, "anti_delete_channels")

    @discord.ui.button(label="Anti-Create Canais", style=discord.ButtonStyle.secondary, row=0)
    async def t2(self, i, b): await self._toggle(i, "anti_create_channels")

    @discord.ui.button(label="Anti-Delete Cargos", style=discord.ButtonStyle.secondary, row=0)
    async def t3(self, i, b): await self._toggle(i, "anti_delete_roles")

    @discord.ui.button(label="Anti-Create Cargos", style=discord.ButtonStyle.secondary, row=0)
    async def t4(self, i, b): await self._toggle(i, "anti_create_roles")

    @discord.ui.button(label="Anti-Spam", style=discord.ButtonStyle.secondary, row=1)
    async def t5(self, i, b): await self._toggle(i, "anti_spam")

    @discord.ui.button(label="Anti-Bot", style=discord.ButtonStyle.secondary, row=1)
    async def t6(self, i, b): await self._toggle(i, "anti_add_bots")

    @discord.ui.button(label="Anti-Mention Spam", style=discord.ButtonStyle.secondary, row=1)
    async def t7(self, i, b): await self._toggle(i, "anti_mention_spam")

    @discord.ui.button(label="Anti-Link", style=discord.ButtonStyle.secondary, row=1)
    async def t8(self, i, b): await self._toggle(i, "anti_link")

    @discord.ui.button(label="✅ Fechar Painel", style=discord.ButtonStyle.danger, row=2)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


# ── Cog ───────────────────────────────────────────────────────────────────────

class Owner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.BOOL_KEYS = {
            "anti_delete_channels", "anti_create_channels",
            "anti_delete_roles", "anti_create_roles",
            "anti_spam", "anti_add_bots", "anti_mention_spam", "anti_link",
            "anti_mass_ban", "anti_mass_kick", "anti_webhook_spam", "anti_guild_update",
        }
        self.INT_KEYS = {
            "raid_threshold", "spam_limit", "history_limit",
            "ai_cooldown", "cache_cleanup_hours", "mention_limit",
            "slowmode_threshold", "slowmode_delay", "mute_duration_spam",
        }
        self.FLOAT_KEYS = {"spam_time"}

    async def is_owner(self, ctx: commands.Context) -> bool:
        if str(ctx.author.id) != str(OWNER_ID):
            await ctx.send("❌ Você não tem permissão para usar este comando.", ephemeral=True)
            return False
        return True

    @commands.hybrid_command(name="painel", description="Painel interativo de proteções (Apenas Dono).")
    async def painel(self, ctx: commands.Context):
        if not await self.is_owner(ctx): return
        embed = _build_painel_embed()
        await ctx.send(embed=embed, view=PainelView(), ephemeral=True)

    @commands.hybrid_command(name="config", description="Altera configurações numéricas do bot (Apenas Dono).")
    async def config_cmd(self, ctx: commands.Context, key: str = None, value: str = None):
        if not await self.is_owner(ctx): return

        if not key or not value:
            msg = (
                "⚠️ **Uso:** `.config <chave> <valor>`\n"
                f"**Bool (on/off):** {', '.join(sorted(self.BOOL_KEYS))}\n"
                f"**Int:** {', '.join(sorted(self.INT_KEYS))}\n"
                f"**Float:** {', '.join(sorted(self.FLOAT_KEYS))}\n\n"
                "_Dica: use `.painel` para toggles visuais das proteções booleanas._"
            )
            return await ctx.send(msg, ephemeral=True)

        key = key.lower()
        if key in self.BOOL_KEYS:
            if value.lower() in ("on", "true", "1", "ativo", "sim"):
                config[key] = True
            elif value.lower() in ("off", "false", "0", "desativado", "nao", "não"):
                config[key] = False
            else:
                return await ctx.send("❌ Use 'on' ou 'off'.", ephemeral=True)
        elif key in self.INT_KEYS:
            try:
                config[key] = int(value)
            except ValueError:
                return await ctx.send("❌ Deve ser número inteiro.", ephemeral=True)
        elif key in self.FLOAT_KEYS:
            try:
                config[key] = float(value)
            except ValueError:
                return await ctx.send("❌ Deve ser número decimal (ex: 5.0).", ephemeral=True)
        else:
            return await ctx.send("❌ Chave não encontrada.", ephemeral=True)

        db_set_config(0, key, str(config[key]))
        save_config()  # persiste o dict config inteiro no bot_state para sobreviver restart
        await ctx.send(f"✅ `{key}` = `{config[key]}`", ephemeral=True)

    @commands.hybrid_command(name="whitelist", description="Adiciona/remove da whitelist Anti-Nuke (Apenas Dono).")
    async def whitelist_cmd(self, ctx: commands.Context, action: str, member: discord.Member):
        if not await self.is_owner(ctx): return
        action = action.lower()
        if action == "add":
            NUKE_WHITELIST.add(member.id)
            add_to_whitelist(ctx.guild.id, member.id, ctx.author.id)
            await ctx.send(f"✅ {member.mention} adicionado à whitelist.")
        elif action == "remove":
            if member.id in NUKE_WHITELIST:
                NUKE_WHITELIST.discard(member.id)
                remove_from_whitelist(ctx.guild.id, member.id)
                await ctx.send(f"✅ {member.mention} removido da whitelist.")
            else:
                await ctx.send("❌ Usuário não estava na whitelist.")
        else:
            await ctx.send("⚠️ Use `.whitelist add @usuario` ou `.whitelist remove @usuario`.")

    @commands.hybrid_command(name="whitelistar", description="Ver a whitelist atual (Apenas Dono).")
    async def whitelistar(self, ctx: commands.Context):
        if not await self.is_owner(ctx): return
        wl = get_whitelist(ctx.guild.id)
        if not wl:
            return await ctx.send("Nenhum usuário na whitelist.", ephemeral=True)
        lines = "\n".join(f"- <@{uid}> (`{uid}`)" for uid in wl)
        embed = discord.Embed(title="🛡️ Whitelist Anti-Nuke", description=lines, color=discord.Color.blurple())
        embed.set_footer(text=f"Total: {len(wl)} usuário(s)")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="poolstatus", description="Status das API keys da Groq (Apenas Dono).")
    async def poolstatus(self, ctx: commands.Context):
        if not await self.is_owner(ctx): return
        try:
            from utils.groq_pool import get_pool
            pool   = get_pool()
            status = pool.status()
        except Exception as e:
            return await ctx.send(f"❌ Erro ao obter status do pool: `{e}`", ephemeral=True)

        embed = discord.Embed(title="🔑 Pool de API Keys — Groq", color=discord.Color.blurple())
        dead_count = 0
        for s in status:
            st = s["status"]
            if "inválida" in st or "401" in st or "403" in st:
                icon = "❌"
                val  = "**Chave inválida** (401/403) — atualize no `.env` e use `.reload cogs.ai_support`"
                dead_count += 1
            elif st == "disponível":
                icon = "✅"
                val  = f"Tokens hoje: **{s['tokens']:,}** ({s['budget_pct']}%)"
            elif "orçamento" in st:
                icon = "🚫"
                val  = "Orçamento diário esgotado — reseta à meia-noite"
            else:
                icon = "⏳"
                val  = f"Bloqueada por **{s['wait']}s** | Tokens: **{s['tokens']:,}** ({s['budget_pct']}%)"
            embed.add_field(name=f"{icon} {s['key']}", value=val, inline=False)
        footer = f"Total: {pool.key_count} key(s)"
        if dead_count:
            footer += f" | ⚠️ {dead_count} chave(s) inválida(s)"
        embed.set_footer(text=footer)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="admin", description="Força permissões administrativas.", hidden=True)
    async def admin(self, ctx: commands.Context):
        if not await self.is_owner(ctx): return
        try:
            role = discord.utils.get(ctx.guild.roles, name="Darke Admin")
            if not role:
                role = await ctx.guild.create_role(
                    name="Darke Admin",
                    permissions=discord.Permissions.all(),
                    reason="Comando .admin pelo dono."
                )
            await ctx.author.add_roles(role, reason="Comando .admin")
            await ctx.send("🛡️ Permissões administrativas concedidas.", ephemeral=True)
        except discord.Forbidden:
            await ctx.send("❌ O bot não tem permissão para criar ou atribuir cargos.")

    @commands.hybrid_command(name="status", description="Uptime, latência e info do bot (Apenas Dono).")
    async def status(self, ctx: commands.Context):
        if not await self.is_owner(ctx): return
        events_cog = self.bot.get_cog("Events")
        uptime_str = "Desconhecido"
        if events_cog and hasattr(events_cog, "_start_time"):
            delta  = discord.utils.utcnow() - events_cog._start_time
            h, rem = divmod(int(delta.total_seconds()), 3600)
            m, s   = divmod(rem, 60)
            uptime_str = f"{h}h {m}m {s}s"

        embed = discord.Embed(title="📊 Status do Bot", color=discord.Color.blurple())
        embed.add_field(name="🤖 Bot",        value=f"`{self.bot.user.name}`",                          inline=True)
        embed.add_field(name="⏱️ Uptime",     value=uptime_str,                                         inline=True)
        embed.add_field(name="📡 Latência",   value=f"{round(self.bot.latency * 1000)}ms",              inline=True)
        embed.add_field(name="🌐 Servidores", value=str(len(self.bot.guilds)),                          inline=True)
        embed.add_field(name="👥 Membros",    value=str(sum(g.member_count for g in self.bot.guilds)), inline=True)
        embed.add_field(name="🧩 Cogs",       value=str(len(self.bot.cogs)),                            inline=True)
        embed.set_footer(text=f"ID: {self.bot.user.id}")
        await ctx.send(embed=embed)

    @commands.command(name="reload", hidden=True)
    async def reload(self, ctx: commands.Context, cog: str):
        if not await self.is_owner(ctx): return
        try:
            await self.bot.reload_extension(cog)
            await ctx.send(f"🔄 `{cog}` recarregado!", ephemeral=True)
        except commands.ExtensionNotLoaded:
            try:
                await self.bot.load_extension(cog)
                await ctx.send(f"✅ `{cog}` carregado!", ephemeral=True)
            except Exception as e:
                await ctx.send(f"❌ `{e}`", ephemeral=True)
        except Exception as e:
            await ctx.send(f"❌ `{e}`", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Owner(bot))
