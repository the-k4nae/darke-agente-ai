"""
cogs/roles.py
─────────────
Sistema de Self-Roles com botões interativos.
- .selfrole criar "Título" "Descrição"  → cria um painel novo
- .selfrole add <message_id> @cargo "Label" [emoji]  → adiciona botão ao painel
- .selfrole publicar <message_id>        → envia o painel com os botões
- .selfrole remover <message_id>         → apaga o painel do DB e a mensagem
Os botões fazem toggle: clicou sem o cargo → adiciona; clicou com o cargo → remove.
"""
import discord
from discord.ext import commands, tasks
from utils.logger import log
from utils.database import (
    create_selfrole_panel, add_selfrole_button,
    get_selfrole_panels, get_selfrole_panel_by_message,
    delete_selfrole_panel
)

# Painéis temporários aguardando .selfrole publicar
# pending[guild_id][message_id] = {title, description, buttons: [...], created_at: float}
import time as _time
_pending: dict[int, dict] = {}
_PENDING_TTL = 3600  # Fix #6 — rascunhos expiram em 1h para evitar vazamento de memória


def _build_view(buttons: list[dict], panel_id: int = 0) -> discord.ui.View:
    """Constrói a View com os botões de self-role."""
    view = discord.ui.View(timeout=None)
    for btn in buttons:
        emoji = btn.get("emoji") or None
        button = SelfRoleButton(
            role_id=btn["role_id"],
            label=btn["label"],
            emoji=emoji,
            panel_id=panel_id,  # Fix #12 — evita colisão de custom_id
        )
        view.add_item(button)
    return view


class SelfRoleButton(discord.ui.Button):
    def __init__(self, role_id: int, label: str, emoji: str | None, panel_id: int = 0):
        # Fix #12 — custom_id inclui panel_id para evitar colisão entre painéis com mesmo cargo
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=label,
            emoji=emoji,
            custom_id=f"selfrole_{panel_id}_{role_id}",
        )
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if not role:
            return await interaction.response.send_message("❌ Cargo não encontrado. Contate um administrador.", ephemeral=True)

        member = interaction.user
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Self-role removido pelo usuário")
                await interaction.response.send_message(f"✅ Cargo **{role.name}** removido.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ Não tenho permissão para remover este cargo.", ephemeral=True)
        else:
            try:
                await member.add_roles(role, reason="Self-role adicionado pelo usuário")
                await interaction.response.send_message(f"✅ Cargo **{role.name}** adicionado!", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ Não tenho permissão para adicionar este cargo.", ephemeral=True)


class SelfRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._pending_cleanup.start()

    def cog_unload(self):
        self._pending_cleanup.cancel()

    @tasks.loop(minutes=30)
    async def _pending_cleanup(self):
        """Fix #6 — remove rascunhos expirados do _pending para evitar vazamento de memória."""
        now = _time.time()
        for guild_id in list(_pending.keys()):
            stale = [mid for mid, d in _pending[guild_id].items()
                     if now - d.get("created_at", 0) > _PENDING_TTL]
            for mid in stale:
                _pending[guild_id].pop(mid, None)
            if not _pending[guild_id]:
                _pending.pop(guild_id, None)

    @_pending_cleanup.before_loop
    async def _before_cleanup(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        """Restaura as Views dos painéis existentes para que os botões funcionem após reinicio."""
        for guild in self.bot.guilds:
            panels = get_selfrole_panels(guild.id)
            for entry in panels:
                panel   = entry["panel"]
                buttons = entry["buttons"]
                if buttons:
                    view = _build_view(buttons, panel_id=panel["message_id"])  # Fix #12
                    self.bot.add_view(view, message_id=panel["message_id"])

    @commands.group(name="selfrole", aliases=["sr"], invoke_without_command=True)
    @commands.has_permissions(manage_roles=True)
    async def selfrole(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @selfrole.command(name="criar", aliases=["create"])
    @commands.has_permissions(manage_roles=True)
    async def sr_criar(self, ctx: commands.Context, titulo: str, *, descricao: str = "Clique nos botões para adicionar ou remover cargos."):
        """
        Cria um rascunho de painel de self-roles.
        Exemplo: `.selfrole criar "Notificações" "Escolha suas notificações"`
        """
        embed = discord.Embed(
            title=titulo,
            description=descricao,
            color=discord.Color.blurple()
        )
        embed.set_footer(text="Rascunho — use .selfrole add para adicionar botões e .selfrole publicar para ativar.")
        msg = await ctx.send(embed=embed)

        # Armazena o rascunho em memória
        _pending.setdefault(ctx.guild.id, {})[msg.id] = {
            "title":       titulo,
            "description": descricao,
            "channel_id":  ctx.channel.id,
            "buttons":     [],
            "created_at":  _time.time(),  # Fix #6 — para TTL cleanup
        }

        await ctx.send(
            f"✅ Rascunho criado! ID: `{msg.id}`\n"
            f"Agora use: `.selfrole add {msg.id} @Cargo Label [emoji]`\n"
            f"Quando terminar: `.selfrole publicar {msg.id}`",
            ephemeral=True
        )

    @selfrole.command(name="add")
    @commands.has_permissions(manage_roles=True)
    async def sr_add(self, ctx: commands.Context, message_id: int, role: discord.Role, label: str, emoji: str = None):
        """
        Adiciona um botão ao rascunho do painel.
        Exemplo: `.selfrole add 123456789 @Promoções "🏷️ Promoções"`
        """
        pending = _pending.get(ctx.guild.id, {}).get(message_id)
        if not pending:
            return await ctx.send("❌ Rascunho não encontrado. Crie um com `.selfrole criar`.", ephemeral=True)

        if len(pending["buttons"]) >= 25:
            return await ctx.send("❌ Máximo de 25 botões por painel.", ephemeral=True)

        pending["buttons"].append({"role_id": role.id, "label": label, "emoji": emoji})

        # Atualiza a mensagem de rascunho com preview
        channel = ctx.guild.get_channel(pending["channel_id"])
        if channel:
            try:
                msg = await channel.fetch_message(message_id)
                view = discord.ui.View(timeout=None)
                for b in pending["buttons"]:
                    view.add_item(SelfRoleButton(b["role_id"], b["label"], b.get("emoji"), panel_id=msg.id))
                await msg.edit(view=view)
            except Exception:
                pass

        await ctx.send(f"✅ Botão `{label}` ({role.mention}) adicionado ao rascunho.", ephemeral=True)

    @selfrole.command(name="publicar", aliases=["publish"])
    @commands.has_permissions(manage_roles=True)
    async def sr_publicar(self, ctx: commands.Context, message_id: int):
        """Salva o painel no banco e ativa os botões permanentemente."""
        pending = _pending.get(ctx.guild.id, {}).get(message_id)
        if not pending:
            return await ctx.send("❌ Rascunho não encontrado.", ephemeral=True)
        if not pending["buttons"]:
            return await ctx.send("❌ Adicione pelo menos um botão antes de publicar.", ephemeral=True)

        # Salva no banco
        panel_id = create_selfrole_panel(
            ctx.guild.id,
            pending["channel_id"],
            message_id,
            pending["title"],
            pending["description"]
        )
        for b in pending["buttons"]:
            add_selfrole_button(panel_id, b["role_id"], b["label"], b.get("emoji"))

        # Registra a View permanente no bot
        view = _build_view(pending["buttons"], panel_id=message_id)  # Fix #12
        self.bot.add_view(view, message_id=message_id)

        # Atualiza a mensagem removendo o footer de rascunho
        channel = ctx.guild.get_channel(pending["channel_id"])
        if channel:
            try:
                msg = await channel.fetch_message(message_id)
                embed = msg.embeds[0] if msg.embeds else discord.Embed(title=pending["title"])
                embed.remove_footer()
                await msg.edit(embed=embed, view=view)
            except Exception:
                pass

        # Remove do rascunho
        _pending[ctx.guild.id].pop(message_id, None)
        await ctx.send(f"✅ Painel publicado e ativo! ID: `{message_id}`", ephemeral=True)

    @selfrole.command(name="remover", aliases=["delete"])
    @commands.has_permissions(manage_roles=True)
    async def sr_remover(self, ctx: commands.Context, message_id: int):
        """Remove um painel do banco e tenta apagar a mensagem."""
        # Fix #2 — busca dados ANTES de deletar (use-after-delete bug)
        panel_data = get_selfrole_panel_by_message(message_id)
        if not panel_data:
            return await ctx.send("❌ Painel não encontrado.", ephemeral=True)

        if delete_selfrole_panel(message_id):
            channel = ctx.guild.get_channel(panel_data["panel"]["channel_id"])
            if channel:
                try:
                    msg = await channel.fetch_message(message_id)
                    await msg.delete()
                except Exception:
                    pass
            await ctx.send(f"✅ Painel `{message_id}` removido.", ephemeral=True)
        else:
            await ctx.send("❌ Falha ao remover o painel. Tente novamente.", ephemeral=True)

    @selfrole.command(name="listar", aliases=["list"])
    @commands.has_permissions(manage_roles=True)
    async def sr_listar(self, ctx: commands.Context):
        """Lista todos os painéis de self-role ativos no servidor."""
        panels = get_selfrole_panels(ctx.guild.id)
        if not panels:
            return await ctx.send("ℹ️ Nenhum painel de self-role configurado.", ephemeral=True)

        embed = discord.Embed(title="🎭 Painéis de Self-Role", color=discord.Color.blurple())
        for entry in panels[:10]:
            p  = entry["panel"]
            bs = entry["buttons"]
            channel = ctx.guild.get_channel(p["channel_id"])
            ch_str  = channel.mention if channel else f"Canal {p['channel_id']}"
            roles_str = ", ".join(f"`{b['label']}`" for b in bs) or "Nenhum"
            embed.add_field(
                name=f"{p['title']} (ID: {p['message_id']})",
                value=f"Canal: {ch_str}\nBotões: {roles_str}",
                inline=False
            )
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(SelfRoles(bot))
