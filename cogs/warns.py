import discord
from discord.ext import commands
from datetime import timedelta
from utils.logger import log_action, log
from utils.database import get_warns, add_warn, clear_warns, add_mod_log, remove_warn_by_id


# ── Fix #13: View de confirmação antes do ban automático ─────────────────────
class BanConfirmView(discord.ui.View):
    def __init__(self, member: discord.Member, mod: discord.Member, guild: discord.Guild):
        super().__init__(timeout=30)
        self.member  = member
        self.mod     = mod
        self.guild   = guild
        self.decided = False

    @discord.ui.button(label="✅ Confirmar Ban", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.mod.id:
            return await interaction.response.send_message("❌ Apenas o moderador que aplicou o warn pode confirmar.", ephemeral=True)
        self.decided = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            await self.member.ban(reason="3 avisos — ban automático confirmado")
            add_mod_log(self.guild.id, self.member.id, self.mod.id, "auto_ban", "3 avisos acumulados")
            clear_warns(self.guild.id, self.member.id)
            await interaction.followup.send(f"🔨 {self.member.mention} foi **banido automaticamente** (3 avisos).")
            await log_action(self.guild, f"🔨 **Auto-Ban:** {self.member.mention} banido (3 avisos confirmado por {self.mod.mention}).")
        except discord.Forbidden:
            await interaction.followup.send("⚠️ Sem permissão para banir este usuário.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.mod.id:
            return await interaction.response.send_message("❌ Apenas o moderador pode cancelar.", ephemeral=True)
        self.decided = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="⚠️ Ban automático **cancelado**. O aviso foi registrado.", view=self)
        self.stop()

    async def on_timeout(self):
        if not self.decided:
            for item in self.children:
                item.disabled = True
            # Precisa de uma referência à mensagem para editar — guardamos via message attribute
            if hasattr(self, "_message") and self._message:
                try:
                    await self._message.edit(
                        content="⚠️ Tempo esgotado — ban automático **não confirmado**.",
                        view=self
                    )
                except Exception:
                    pass

class Warns(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="warn", description="Adiciona um aviso a um membro.")
    @commands.has_permissions(moderate_members=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Sem motivo informado"):
        total = add_warn(ctx.guild.id, member.id, reason, str(ctx.author))
        # Fix #5 — guard against unexpected None return from DB
        if total is None:
            log.error(f"[Warns] add_warn retornou None para {member.id} em {ctx.guild.id}")
            return await ctx.send("❌ Erro ao registrar aviso. Tente novamente.", ephemeral=True)
        add_mod_log(ctx.guild.id, member.id, ctx.author.id, "warn", reason)

        embed = discord.Embed(title="⚠️ Aviso Registrado", color=discord.Color.orange())
        embed.add_field(name="Usuário",   value=member.mention,    inline=True)
        embed.add_field(name="Moderador", value=ctx.author.mention, inline=True)
        embed.add_field(name="Motivo",    value=reason,             inline=False)
        embed.set_footer(text=f"Total de avisos: {total}/3")
        await ctx.send(embed=embed)

        await log_action(
            ctx.guild,
            f"⚠️ **Warn:** {member.mention} recebeu aviso de {ctx.author.mention}. Motivo: `{reason}`. Total: **{total}/3**",
            color=discord.Color.orange()
        )

        # Fix #10 — notifica o membro por DM antes de punir
        try:
            if total == 1:
                await member.send(
                    f"⚠️ Você recebeu um **aviso** no servidor **{ctx.guild.name}**.\n"
                    f"**Motivo:** {reason}\n"
                    f"Total de avisos: **1/3**. Ao atingir 3, você será banido automaticamente."
                )
            elif total == 2:
                await member.send(
                    f"⚠️ Você recebeu seu **2º aviso** no servidor **{ctx.guild.name}**.\n"
                    f"**Motivo:** {reason}\n"
                    f"Você será silenciado por **30 minutos**. Mais 1 aviso resulta em ban automático."
                )
            elif total >= 3:
                await member.send(
                    f"🔨 Você atingiu **3 avisos** no servidor **{ctx.guild.name}** e foi **banido automaticamente**.\n"
                    f"**Último motivo:** {reason}"
                )
        except discord.Forbidden:
            pass  # DM bloqueada — não é crítico

        try:
            if total == 2:
                await member.timeout(timedelta(minutes=30), reason="2 avisos acumulados")
                add_mod_log(ctx.guild.id, member.id, ctx.author.id, "auto_mute", "2 avisos acumulados", "30m")
                await ctx.send(f"⚠️ {member.mention} acumulou **2 avisos** → silenciado por **30 min**.")
                await log_action(ctx.guild, f"🔇 **Auto-Mute:** {member.mention} silenciado (2 avisos).")
            elif total >= 3:
                # Fix #13 — pede confirmação antes de banir para evitar erros acidentais
                view = BanConfirmView(member, ctx.author, ctx.guild)
                sent = await ctx.send(
                    f"⚠️ {member.mention} atingiu **3 avisos**. Confirma o **ban automático**?",
                    view=view,
                    ephemeral=True
                )
                view._message = sent
        except discord.Forbidden:
            await ctx.send("⚠️ Sem permissão para aplicar a punição automática.", ephemeral=True)

    @commands.hybrid_command(name="warns", description="Mostra os avisos de um membro.")
    @commands.has_permissions(moderate_members=True)
    async def warns(self, ctx: commands.Context, member: discord.Member):
        user_warns = get_warns(ctx.guild.id, member.id)
        if not user_warns:
            return await ctx.send(f"✅ {member.mention} não possui nenhum aviso.")

        embed = discord.Embed(title=f"⚠️ Avisos de {member.display_name}", color=discord.Color.orange())
        for i, w in enumerate(user_warns, 1):
            embed.add_field(
                name=f"Aviso #{i}",
                value=f"**Motivo:** {w['reason']}\n**Mod:** {w['moderator']}\n**Em:** {w['created_at']}",
                inline=False
            )
        embed.set_footer(text=f"Total: {len(user_warns)}/3")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="removerwarn", description="Remove um aviso específico de um membro (pelo número).")
    @commands.has_permissions(moderate_members=True)
    async def removerwarn(self, ctx: commands.Context, member: discord.Member, numero: int):
        """
        Fix #14 — Remove um warn individual.
        Uso: .removerwarn @user 2  (remove o 2º aviso)
        """
        removed = remove_warn_by_id(ctx.guild.id, member.id, numero)
        if not removed:
            current = len(get_warns(ctx.guild.id, member.id))
            return await ctx.send(
                f"❌ Aviso #{numero} não encontrado. {member.mention} tem **{current}** aviso(s).",
                ephemeral=True
            )
        remaining = len(get_warns(ctx.guild.id, member.id))
        await ctx.send(f"✅ Aviso #{numero} de {member.mention} removido. Avisos restantes: **{remaining}**.")
        await log_action(
            ctx.guild,
            f"🗑️ Warn #{numero} de {member.mention} removido por {ctx.author.mention}. Restantes: {remaining}.",
            color=discord.Color.green()
        )

    @commands.hybrid_command(name="clearwarns", description="Remove todos os avisos de um membro.")
    @commands.has_permissions(moderate_members=True)
    async def clearwarns(self, ctx: commands.Context, member: discord.Member):
        clear_warns(ctx.guild.id, member.id)
        await ctx.send(f"✅ Todos os avisos de {member.mention} foram removidos.")
        await log_action(ctx.guild, f"🗑️ Avisos de {member.mention} zerados por {ctx.author.mention}.", color=discord.Color.green())

async def setup(bot):
    await bot.add_cog(Warns(bot))
