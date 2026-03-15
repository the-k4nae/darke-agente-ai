"""
cogs/backup.py  ─  v4
──────────────────────
Sistema de Backup e Recuperação — Darke Store Bot

Melhorias v4:
  ✅ Registro de backups no banco de dados (backup_registry)
  ✅ Backup automático diário via task agendada
  ✅ Metadados: tamanho, quem criou, quem restaurou e quando
  ✅ Comando .backupinfo  — histórico completo via banco
  ✅ Confirmação interativa antes de restaurar
  ✅ Restauração incremental (preserva o que já existe)
  ✅ Rotação automática de N backups por servidor
  ✅ Distinção visual entre backup manual e automático
"""

import discord
from discord.ext import commands, tasks
import json, os
from datetime import datetime
from utils.logger import log_action, log
from utils.cache import OWNER_ID, config
from utils.database import register_backup, mark_backup_restored, get_backup_history

BACKUPS_DIR = "backups"


class ConfirmRestoreView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.confirmed = False

    @discord.ui.button(label="✅ Confirmar Restauração", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Apenas quem executou pode confirmar.", ephemeral=True)
        self.confirmed = True
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Apenas quem executou pode cancelar.", ephemeral=True)
        await interaction.response.send_message("❌ Restauração cancelada.", ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        self.stop()


class Backup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        self.auto_backup_task.start()

    def cog_unload(self):
        self.auto_backup_task.cancel()

    @tasks.loop(hours=24)
    async def auto_backup_task(self):
        for guild in self.bot.guilds:
            try:
                result = await self._do_backup(guild, created_by=None)
                if result:
                    log.info(f"Backup automático: {guild.name}")
                else:
                    log.warning(f"Backup automático retornou None para {guild.name}")
            except Exception as e:
                log.error(f"Backup automático falhou em {guild.name}: {e}")

    @auto_backup_task.before_loop
    async def before_auto_backup(self):
        await self.bot.wait_until_ready()
        # Fix #6 — aguarda 1h antes do primeiro backup automático para não rodar
        # imediatamente após cada restart, independente do horário
        import asyncio
        await asyncio.sleep(3600)

    async def _do_backup(self, guild: discord.Guild, created_by=None):
        data = {
            "guild_name": guild.name,
            "guild_id":   guild.id,
            "created_at": datetime.utcnow().isoformat(),
            "created_by": created_by,
            "roles": [], "categories": [], "channels": [],
        }
        for role in guild.roles:
            if role.is_default() or role.managed:
                continue
            data["roles"].append({
                "name": role.name, "color": role.color.value,
                "hoist": role.hoist, "mentionable": role.mentionable,
                "permissions": role.permissions.value, "position": role.position,
            })
        for cat in guild.categories:
            data["categories"].append({"name": cat.name, "position": cat.position, "id": cat.id})
        for ch in guild.channels:
            if isinstance(ch, (discord.TextChannel, discord.VoiceChannel)):
                data["channels"].append({
                    "name": ch.name,
                    "type": "text" if isinstance(ch, discord.TextChannel) else "voice",
                    "position": ch.position,
                    "topic": getattr(ch, "topic", None),
                    "nsfw": getattr(ch, "nsfw", False),
                    "category_id": ch.category_id,
                    "slowmode_delay": getattr(ch, "slowmode_delay", 0),
                })

        ts     = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        prefix = "auto" if not created_by else "manual"
        fname  = f"{BACKUPS_DIR}/backup_{guild.id}_{prefix}_{ts}.json"

        # Verifica espaço em disco antes de salvar (mínimo 50MB livres)
        MIN_FREE_MB = 50
        try:
            disk  = os.statvfs(BACKUPS_DIR)
            free_mb = (disk.f_bavail * disk.f_frsize) / (1024 * 1024)
            if free_mb < MIN_FREE_MB:
                from utils.logger import log_action
                await log_action(guild,
                    f"⚠️ **Backup cancelado:** Espaço em disco insuficiente "
                    f"(**{free_mb:.0f}MB** livre, mínimo {MIN_FREE_MB}MB).",
                    color=discord.Color.red()
                )
                from utils.cache import OWNER_ID
                try:
                    owner = await self.bot.fetch_user(int(OWNER_ID))
                    await owner.send(
                        "🚨 **Backup falhou — Disco quase cheio!**\n"
                        f"Espaço livre: **{free_mb:.0f}MB** (mínimo: {MIN_FREE_MB}MB)\n"
                        f"Servidor: **{guild.name}** | Libere espaço urgentemente."
                    )
                except Exception:
                    pass
                return None
        except (AttributeError, OSError):
            pass  # os.statvfs não disponível no Windows — ignora

        # Escreve o JSON em thread separada para não bloquear o event loop
        import asyncio as _asyncio
        def _write_file():
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            return os.path.getsize(fname)

        raw_size = await _asyncio.to_thread(_write_file)
        size_kb = raw_size / 1024

        register_backup(guild.id, fname, len(data["roles"]), len(data["channels"]),
                        len(data["categories"]), round(size_kb, 2), created_by or 0)

        # Rotação de backups antigos também em thread para não bloquear
        def _rotate():
            max_bkp = config.get("max_backups_per_guild", 10)
            all_bkps = sorted(
                [f for f in os.listdir(BACKUPS_DIR)
                 if f.startswith(f"backup_{guild.id}_") and f.endswith(".json")],
                reverse=True
            )
            for old in all_bkps[max_bkp:]:
                try:
                    os.remove(os.path.join(BACKUPS_DIR, old))
                except Exception:
                    pass

        await _asyncio.to_thread(_rotate)
        return fname, data

    @commands.hybrid_command(name="backup", description="Salva a estrutura atual de canais e cargos (Apenas Dono).")
    async def backup(self, ctx: commands.Context):
        if str(ctx.author.id) != str(OWNER_ID):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        # Fix #1 — _do_backup pode retornar None (disco cheio, erro de I/O)
        result = await self._do_backup(ctx.guild, created_by=ctx.author.id)
        if result is None:
            return await ctx.send("❌ Backup falhou — verifique o espaço em disco e os logs.", ephemeral=True)
        fname, data = result
        import asyncio as _asyncio
        size_kb = await _asyncio.to_thread(lambda: os.path.getsize(fname) / 1024)
        embed = discord.Embed(title="💾 Backup Criado!", color=discord.Color.green())
        embed.add_field(name="📁 Arquivo",    value=f"`{os.path.basename(fname)}`", inline=False)
        embed.add_field(name="🔑 Cargos",     value=str(len(data["roles"])),        inline=True)
        embed.add_field(name="📂 Categorias", value=str(len(data["categories"])),   inline=True)
        embed.add_field(name="📺 Canais",     value=str(len(data["channels"])),     inline=True)
        embed.add_field(name="📦 Tamanho",    value=f"{size_kb:.1f} KB",            inline=True)
        embed.set_footer(text="Backup automático diário também está ativo. ✅")
        await ctx.send(embed=embed, ephemeral=True)
        await log_action(ctx.guild, f"💾 **Backup manual** por {ctx.author.mention}: `{os.path.basename(fname)}`", color=discord.Color.blue())

    @commands.hybrid_command(name="listarbackups", description="Lista os backups salvos (Apenas Dono).")
    async def listarbackups(self, ctx: commands.Context):
        if str(ctx.author.id) != str(OWNER_ID):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        import asyncio as _asyncio
        guild_id = str(ctx.guild.id)

        def _list_files():
            if not os.path.isdir(BACKUPS_DIR):
                return []
            return sorted(
                [f for f in os.listdir(BACKUPS_DIR)
                 if f.startswith(f"backup_{guild_id}_") and f.endswith(".json")],
                reverse=True
            )

        files = await _asyncio.to_thread(_list_files)
        if not files:
            return await ctx.send("❌ Nenhum backup encontrado.", ephemeral=True)
        embed = discord.Embed(title="💾 Backups Disponíveis", color=discord.Color.blue())
        for i, fname in enumerate(files[:10], 1):
            parts = fname.replace(f"backup_{guild_id}_", "").replace(".json", "").split("_", 1)
            btype = parts[0] if parts[0] in ("auto", "manual") else "?"
            icon  = "🤖" if btype == "auto" else "👤"
            size  = os.path.getsize(os.path.join(BACKUPS_DIR, fname)) / 1024
            embed.add_field(name=f"#{i} {icon} {btype.upper()}", value=f"`{parts[1] if len(parts)>1 else fname}` — {size:.1f} KB", inline=True)
        embed.set_footer(text="Use .restaurarbackup <número> para restaurar. 🤖=auto 👤=manual")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="backupinfo", description="Histórico detalhado de backups (Apenas Dono).")
    async def backupinfo(self, ctx: commands.Context):
        if str(ctx.author.id) != str(OWNER_ID):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        history = get_backup_history(ctx.guild.id, limit=8)
        if not history:
            return await ctx.send("❌ Nenhum backup registrado no banco ainda.", ephemeral=True)
        embed = discord.Embed(title="🗄️ Histórico de Backups — Banco de Dados", color=discord.Color.blurple())
        for entry in history:
            restored_str = f"\n🔄 Restaurado: `{entry['restored_at'][:16]}`" if entry.get("restored_at") else ""
            embed.add_field(
                name=f"📁 {os.path.basename(entry['filename'])}",
                value=(f"📅 `{entry['created_at'][:16]}` | 📦 {entry['file_size_kb']:.1f} KB\n"
                       f"🔑 {entry['roles_count']} cargos | 📺 {entry['channels_count']} canais{restored_str}"),
                inline=False
            )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="restaurarbackup", description="Restaura canais e cargos de um backup (Apenas Dono).")
    async def restaurarbackup(self, ctx: commands.Context, numero: int = 1):
        if str(ctx.author.id) != str(OWNER_ID):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        await ctx.defer(ephemeral=True)

        me = ctx.guild.me
        missing = []
        if not me.guild_permissions.manage_roles:    missing.append("`Gerenciar Cargos`")
        if not me.guild_permissions.manage_channels: missing.append("`Gerenciar Canais`")
        if missing:
            return await ctx.send(f"❌ Permissões faltando: {', '.join(missing)}", ephemeral=True)

        guild_id = str(ctx.guild.id)

        # I/O em thread para não bloquear o event loop
        import asyncio as _asyncio

        def _load_backup_file():
            fs = sorted(
                [f for f in os.listdir(BACKUPS_DIR)
                 if f.startswith(f"backup_{guild_id}_") and f.endswith(".json")],
                reverse=True
            )
            if not fs or numero > len(fs):
                return None, None, 0
            fn = os.path.join(BACKUPS_DIR, fs[numero - 1])
            with open(fn, "r", encoding="utf-8") as fp:
                d = json.load(fp)
            return fn, d, os.path.getsize(fn) / 1024

        fname, data, size_kb = await _asyncio.to_thread(_load_backup_file)
        if fname is None:
            return await ctx.send("❌ Backup não encontrado.", ephemeral=True)

        preview = discord.Embed(
            title="⚠️ Confirmar Restauração de Backup",
            description=(
                f"Arquivo: **`{os.path.basename(fname)}`**\n\n"
                f"🔑 {len(data.get('roles',[]))} cargos | 📺 {len(data.get('channels',[]))} canais | 📂 {len(data.get('categories',[]))} categorias\n"
                f"📦 {size_kb:.1f} KB\n\n_Itens já existentes não serão duplicados._\n⏳ Expira em 60 segundos."
            ),
            color=discord.Color.orange()
        )
        view = ConfirmRestoreView(ctx.author.id)
        await ctx.send(embed=preview, view=view, ephemeral=True)
        await view.wait()
        if not view.confirmed:
            return

        guild = ctx.guild
        restored = {"roles": 0, "channels": 0}
        # Fix #11 — feedback de progresso durante restauração (pode demorar 30-60s)
        progress_msg = await ctx.channel.send("⏳ Restaurando backup... aguarde.")
        existing_roles = {r.name for r in guild.roles}
        for rd in sorted(data.get("roles", []), key=lambda r: r["position"]):
            if rd["name"] not in existing_roles:
                try:
                    await guild.create_role(
                        name=rd["name"], color=discord.Color(rd["color"]),
                        hoist=rd["hoist"], mentionable=rd["mentionable"],
                        permissions=discord.Permissions(rd["permissions"]), reason="Restauração Backup v4"
                    )
                    restored["roles"] += 1
                except Exception as e:
                    log.warning(f"Cargo {rd['name']}: {e}")

        existing_ch = {c.name for c in guild.channels}
        for cd in data.get("categories", []):
            if cd["name"] not in existing_ch:
                try: await guild.create_category(name=cd["name"], reason="Restauração Backup v4")
                except Exception as e: log.warning(f"Categoria {cd['name']}: {e}")

        for cd in data.get("channels", []):
            if cd["name"] not in existing_ch:
                try:
                    if cd["type"] == "text":
                        kw = {"name": cd["name"], "nsfw": cd["nsfw"], "reason": "Restauração Backup v4"}
                        if cd.get("topic"): kw["topic"] = cd["topic"]
                        await guild.create_text_channel(**kw)
                    else:
                        await guild.create_voice_channel(name=cd["name"], reason="Restauração Backup v4")
                    restored["channels"] += 1
                except Exception as e: log.warning(f"Canal {cd['name']}: {e}")

        mark_backup_restored(fname, ctx.author.id)

        try:
            await progress_msg.delete()
        except Exception:
            pass
        result = discord.Embed(title="✅ Restauração Concluída!", color=discord.Color.green())
        result.add_field(name="📁 Arquivo",        value=f"`{os.path.basename(fname)}`", inline=False)
        result.add_field(name="🔑 Cargos criados", value=str(restored["roles"]),         inline=True)
        result.add_field(name="📺 Canais criados", value=str(restored["channels"]),      inline=True)
        result.set_footer(text="Itens já existentes foram preservados.")
        await ctx.send(embed=result, ephemeral=True)
        await log_action(ctx.guild, f"🔄 **Backup restaurado** por {ctx.author.mention}: `{os.path.basename(fname)}`", color=discord.Color.blue())


async def setup(bot):
    await bot.add_cog(Backup(bot))
