"""
cogs/ux.py  ─  v5  (NOVO)
───────────────────────────
UX & Interface Avançada — Darke Store Bot

Funcionalidades:
  ✅ .help interativo com botões de categoria (substitui o help estático)
  ✅ Paginação automática para modlog, warns e listas longas
  ✅ .about — informações e créditos do bot com visual premium
  ✅ .invite — link de convite do bot
  ✅ Confirm/Cancel view reutilizável para outras cogs
"""

import discord
from discord.ext import commands
import time

from utils.cache import OWNER_ID

# ─────────────────────────────────────────────────────────────────────────────
# DADOS DAS PÁGINAS DO HELP
# ─────────────────────────────────────────────────────────────────────────────

HELP_PAGES = [
    {
        "emoji": "🏠",
        "label": "Início",
        "title": "🤖 Darke Store Bot — v4",
        "color": discord.Color.blurple(),
        "fields": [
            ("📌 Prefixo", "`.` (ponto)  |  Slash `/`", False),
            ("📖 Navegação", "Use os botões abaixo para explorar cada categoria.", False),
            ("🧩 Módulos ativos", (
                "🧠 IA de Suporte  •  🛠️ Moderação\n"
                "🛡️ Segurança  •  💾 Backup\n"
                "📊 Analytics  •  🔔 Alertas\n"
                "🎭 Self-Roles  •  🎉 Sorteios\n"
                "⚡ Respostas Rápidas  •  💰 Métricas Groq\n"
                "📋 Mod Log  •  🏥 Health Check"
            ), False),
        ],
        "footer": "Selecione uma categoria abaixo ↓"
    },
    {
        "emoji": "🧠",
        "label": "IA Suporte",
        "title": "🧠 Inteligência Artificial",
        "color": discord.Color.purple(),
        "fields": [
            ("Como funciona", "Envie sua dúvida no canal de suporte configurado — a IA responde automaticamente.", False),
            ("`.limparhistorico`", "Reinicia o seu contexto de conversa com a IA.", False),
            ("`.sessoes`", "Mostra sessões de IA ativas em tempo real — usuário, categoria, inatividade (dono).", False),
            ("`.iaqualidade`", "Estatísticas de qualidade das respostas com taxa de resolução (dono).", False),
            ("`.simular <mensagem>`", "Testa como a IA responderia sem usar o canal real. Ideal após `.reloadprompt` (dono).", False),
            ("`.exportarhistorico @user`", "Exporta a conversa de IA de um usuário como `.txt` para o ticket (dono).", False),
            ("`.reloadprompt`", "Recarrega o `prompt.txt` imediatamente sem reiniciar o bot (dono).", False),
            ("`.manutencao on/off`", "Pausa a IA e posta aviso no canal. `.manutencao off` retoma (dono).", False),
            ("`.statusia`", "Mostra se a IA está ativa ou em manutenção.", False),
            ("`.faqsugestao [dias]`", "IA analisa perguntas e sugere entradas pro `prompt.txt` (dono).", False),
            ("`.faqhistorico`", "Exibe as últimas sugestões de FAQ geradas (dono).", False),
        ],
        "footer": "A IA usa histórico de até 8 mensagens por sessão. Sessões expiram em 2h."
    },
    {
        "emoji": "🛠️",
        "label": "Moderação",
        "title": "🛠️ Moderação",
        "color": discord.Color.orange(),
        "fields": [
            ("`.clear [1-100]`", "Apaga mensagens do canal.", False),
            ("`.lock` / `.unlock`", "Bloqueia/desbloqueia envio de mensagens.", False),
            ("`.ban @user [motivo]`", "Bane permanentemente.", False),
            ("`.kick @user [motivo]`", "Expulsa do servidor.", False),
            ("`.mute @user [min]` / `.unmute`", "Silencia temporariamente.", False),
            ("`.unban <id/nome>`", "Remove o ban de um usuário.", False),
            ("`.warn @user [motivo]`", "Adiciona aviso. 2 warns = mute 30min. 3 warns = ban.", False),
            ("`.warns @user`", "Ver avisos de um membro.", False),
            ("`.clearwarns @user`", "Zera os avisos.", False),
            ("`.userinfo [@user]`", "Informações detalhadas do perfil.", False),
            ("`.modlog @user`", "Histórico completo de punições.", False),
            ("`.modlog`", "Últimas 20 ações de moderação.", False),
        ],
        "footer": "Requer permissões adequadas para cada comando."
    },
    {
        "emoji": "🛡️",
        "label": "Segurança",
        "title": "🛡️ Segurança & Proteção",
        "color": discord.Color.red(),
        "fields": [
            ("Anti-Nuke", "Detecta e bane automaticamente quem deletar/criar canais/cargos em massa.", False),
            ("Anti-Spam", "Muta usuários que enviam mensagens rápidas demais.", False),
            ("Anti-Bot", "Remove bots adicionados sem autorização do dono.", False),
            ("Anti-Mention Spam", "Pune quem menciona múltiplos usuários de uma vez.", False),
            ("`.antilink on/off`", "Liga/desliga bloqueio de links externos.", False),
            ("`.antilinkcanal #canal`", "Define canais monitorados pelo Anti-Link.", False),
            ("`.filtro on/off`", "Liga/desliga o filtro de palavras.", False),
            ("`.addfiltro <palavra>`", "Adiciona palavra ao filtro.", False),
            ("`.removerfiltro <palavra>`", "Remove palavra do filtro.", False),
            ("`.listarfiltro`", "Lista palavras bloqueadas.", False),
            ("`.slowmode [threshold] [delay]`", "Configura o Auto-Slowmode.", False),
            ("`.raid on/off`", "Modo Pânico: bloqueia/desbloqueia TODOS os canais.", False),
        ],
        "footer": "Use .painel para um painel visual de toggle das proteções."
    },
    {
        "emoji": "💾",
        "label": "Backup",
        "title": "💾 Backup & Recuperação",
        "color": discord.Color.blue(),
        "fields": [
            ("`.backup`", "Cria backup manual de canais, categorias e cargos.", False),
            ("`.listarbackups`", "Lista backups disponíveis (🤖 auto | 👤 manual).", False),
            ("`.backupinfo`", "Histórico completo de backups com metadados no banco.", False),
            ("`.restaurarbackup [nº]`", "Restaura um backup com confirmação interativa.", False),
            ("🤖 Backup Automático", "Ocorre a cada 24 horas automaticamente para todos os servidores.", False),
            ("📦 Retenção", f"Os últimos 10 backups por servidor são mantidos automaticamente.", False),
        ],
        "footer": "A restauração é incremental — não duplica canais/cargos existentes."
    },
    {
        "emoji": "📊",
        "label": "Analytics",
        "title": "📊 Estatísticas & Analytics",
        "color": discord.Color.green(),
        "fields": [
            ("`.stats`", "Dashboard geral: membros, IA, moderação e comandos.", False),
            ("`.statsmod`", "Ações de moderação com barras de progresso (7 dias).", False),
            ("`.statsai`", "Taxa de resolução, categorias e perguntas da IA.", False),
            ("`.statsmembros`", "Crescimento, entradas, saídas e retenção (30 dias).", False),
            ("`.statscomandos`", "Ranking de comandos mais usados.", False),
        ],
        "footer": "Requer permissão 'Gerenciar Servidor' ou ser o dono."
    },
    {
        "emoji": "🔔",
        "label": "Alertas",
        "title": "🔔 Notificações & Alertas",
        "color": discord.Color.gold(),
        "fields": [
            ("Alertas automáticos via DM", "O bot envia notificações diretamente para o dono em eventos críticos.", False),
            ("🤖 IA Qualidade baixa", f"Taxa de resolução < {40}% por 7 dias.", False),
            ("💾 Backup desatualizado", "Último backup com mais de 25 horas.", False),
            ("👥 Pico de saída", "Saídas superam entradas em 30 dias.", False),
            ("🚨 Segurança", "Nuke/raid detectado — aviso imediato.", False),
            ("🏥 Latência crítica", "Latência ≥ 800ms.", False),
            ("`.alertas`", "Ver status e configurações dos alertas.", False),
            ("`.alertas on/off`", "Liga ou desliga todos os alertas.", False),
            ("`.alertateste`", "Envia um alerta de teste para a sua DM.", False),
        ],
        "footer": "Alertas têm cooldown para evitar spam de DMs."
    },
    {
        "emoji": "🏥",
        "label": "Health",
        "title": "🏥 Health Check & Sistema",
        "color": discord.Color.teal(),
        "fields": [
            ("`.ping`", "Latência WebSocket + API round-trip com indicador colorido.", False),
            ("`.healthcheck`", "Status completo: RAM, banco, latência, uptime, cogs (dono).", False),
            ("`.cogstatus`", "Status detalhado de cada cog carregado (dono).", False),
            ("`.reloadall`", "Recarrega todos os cogs sem reiniciar o bot (dono).", False),
            ("`.reload <cog>`", "Recarrega um cog específico (dono).", False),
            ("`.status`", "Uptime, latência, servidores e membros.", False),
            ("🤖 Watchdog", "O bot monitora cogs críticos a cada 5 min e recarrega automaticamente.", False),
            ("🗄️ DB Monitor", "Verifica integridade do banco a cada hora.", False),
        ],
        "footer": "Cogs críticos são recarregados automaticamente pelo Watchdog."
    },
    {
        "emoji": "⚙️",
        "label": "Admin",
        "title": "⚙️ Admin & Configurações",
        "color": discord.Color.dark_gray(),
        "fields": [
            ("`.painel`", "Painel interativo de toggles das proteções (dono).", False),
            ("`.config <chave> <valor>`", "Altera configurações em tempo real (dono).", False),
            ("`.whitelist add/remove @user`", "Gerencia a whitelist Anti-Nuke (dono).", False),
            ("`.whitelistar`", "Lista a whitelist atual (dono).", False),
            ("`.poolstatus`", "Status e saúde das API keys da Groq (dono).", False),
            ("`.groqcusto`", "Dashboard de custo e tokens Groq por chave, com estimativa em USD (dono).", False),
            ("`.groqvolume`", "Gráfico de volume de chamadas à IA por hora — últimas 24h (dono).", False),
            ("`.groqcasos`", "Usuários com mais suportes não resolvidos — identifica casos difíceis (dono).", False),
            ("`.admin`", "Recupera permissões administrativas (dono, oculto).", False),
            ("`.setupcounter`", "Cria canal de voz com contador de membros.", False),
        ],
        "footer": "Configurações alteradas via .config são salvas no banco de dados."
    },
    {
        "emoji": "🎭",
        "label": "Extras",
        "title": "🎭 Self-Roles, Sorteios & Mais",
        "color": discord.Color.magenta(),
        "fields": [
            ("`.selfrole criar <Título>`", "Cria painel de cargos por botão.", False),
            ("`.selfrole add <id> @cargo Label`", "Adiciona botão ao painel.", False),
            ("`.selfrole publicar <id>`", "Ativa o painel.", False),
            ("`.giveaway <tempo> [vencedores] <prêmio>`", "Inicia um sorteio.", False),
            ("`.gsorteio <id>`", "Re-sorteia um giveaway.", False),
            ("`.gcancelar <id>`", "Cancela um giveaway.", False),
            ("`.gstatus`", "Lista sorteios ativos.", False),
            ("`.about`", "Informações e créditos do bot.", False),
        ],
        "footer": "Darke Store Bot v4 — Desenvolvido por thek4nae"
    },
    {
        "emoji": "⚡",
        "label": "Suporte Rápido",
        "title": "⚡ Respostas Rápidas (Staff)",
        "color": discord.Color.teal(),
        "fields": [
            ("Como usar", "Staff e admins podem enviar respostas padronizadas com `.resp <template> [@usuario]`.", False),
            ("`.resp optifine`", "Tutorial completo de ativação de capa OptiFine.", False),
            ("`.resp recovery`", "Tutorial do recovery code passo a passo.", False),
            ("`.resp email4`", "Explicação do formato `email:senha:email:senha`.", False),
            ("`.resp proteção`", "Checklist de proteção da conta.", False),
            ("`.resp ticket`", "Redireciona para abrir um ticket de suporte.", False),
            ("`.respostas`", "Lista todos os templates disponíveis.", False),
            ("`.limparusuario @user`", "Limpa o histórico de IA de um usuário (staff).", False),
        ],
        "footer": "O comando de chamada é deletado automaticamente para manter o canal limpo."
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# VIEW DE PAGINAÇÃO DO HELP
# ─────────────────────────────────────────────────────────────────────────────

class HelpView(discord.ui.View):
    def __init__(self, author_id: int, bot=None):
        super().__init__(timeout=180)
        self.author_id   = author_id
        self.current     = 0
        self.bot         = bot
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()

        # ← Anterior
        prev_btn = discord.ui.Button(
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            custom_id="help_prev",
            disabled=(self.current == 0),
            row=0
        )
        prev_btn.callback = self._prev
        self.add_item(prev_btn)

        # Página atual
        page_btn = discord.ui.Button(
            label=f"{self.current + 1}/{len(HELP_PAGES)}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=0
        )
        self.add_item(page_btn)

        # → Próxima
        next_btn = discord.ui.Button(
            emoji="▶️",
            style=discord.ButtonStyle.secondary,
            custom_id="help_next",
            disabled=(self.current == len(HELP_PAGES) - 1),
            row=0
        )
        next_btn.callback = self._next
        self.add_item(next_btn)

        # Botões de atalho por categoria (row 1 e 2)
        for i, page in enumerate(HELP_PAGES):
            if i == 0:
                continue  # Pula "Início" nos atalhos
            btn = discord.ui.Button(
                emoji=page["emoji"],
                label=page["label"],
                style=discord.ButtonStyle.primary if i == self.current else discord.ButtonStyle.secondary,
                custom_id=f"help_page_{i}",
                row=1 if i <= 5 else 2
            )
            # Closure para capturar i corretamente
            btn.callback = self._make_jump(i)
            self.add_item(btn)

    def _make_jump(self, index: int):
        async def jump(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                return await interaction.response.send_message("❌ Apenas quem abriu o help pode navegar.", ephemeral=True)
            self.current = index
            self._build_buttons()
            await interaction.response.edit_message(embed=self._build_embed(), view=self)
        return jump

    async def _prev(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Apenas quem abriu o help pode navegar.", ephemeral=True)
        self.current = max(0, self.current - 1)
        self._build_buttons()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    async def _next(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌ Apenas quem abriu o help pode navegar.", ephemeral=True)
        self.current = min(len(HELP_PAGES) - 1, self.current + 1)
        self._build_buttons()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    def _build_embed(self) -> discord.Embed:
        page  = HELP_PAGES[self.current]
        embed = discord.Embed(
            title=page["title"],
            color=page["color"]
        )
        for name, value, inline in page["fields"]:
            embed.add_field(name=name, value=value, inline=inline)
        # Fix #17 — aviso se algum cog crítico não estiver carregado
        cog_warning = ""
        if self.bot:
            CRIT = ["cogs.ai_support", "cogs.moderation", "cogs.anti_nuke", "cogs.backup"]
            missing = [e.split(".")[-1] for e in CRIT if e not in self.bot.extensions]
            if missing:
                cog_warning = f"  ⚠️ Cogs offline: {', '.join(missing)}"
        embed.set_footer(text=f"{page.get('footer', '')}  |  Página {self.current + 1}/{len(HELP_PAGES)}{cog_warning}")
        return embed


# ─────────────────────────────────────────────────────────────────────────────
# PAGINAÇÃO GENÉRICA
# ─────────────────────────────────────────────────────────────────────────────

class PaginatedEmbed(discord.ui.View):
    """View reutilizável para paginar qualquer lista de embeds."""

    def __init__(self, embeds: list[discord.Embed], author_id: int):
        super().__init__(timeout=120)
        self.embeds    = embeds
        self.author_id = author_id
        self.page      = 0
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page == len(self.embeds) - 1
        self.page_btn.label    = f"{self.page + 1}/{len(self.embeds)}"

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌", ephemeral=True)
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("❌", ephemeral=True)
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)


def paginate_fields(
    title: str,
    color: discord.Color,
    items: list[tuple],   # (name, value, inline)
    per_page: int = 8,
    footer_prefix: str = ""
) -> list[discord.Embed]:
    """Divide uma lista de fields em múltiplos embeds paginados."""
    pages  = []
    chunks = [items[i:i + per_page] for i in range(0, len(items), per_page)]
    total  = len(chunks)
    for idx, chunk in enumerate(chunks, 1):
        embed = discord.Embed(title=title, color=color)
        for name, value, inline in chunk:
            embed.add_field(name=name, value=value, inline=inline)
        footer = f"{footer_prefix}  |  " if footer_prefix else ""
        embed.set_footer(text=f"{footer}Página {idx}/{total}")
        pages.append(embed)
    return pages


# ─────────────────────────────────────────────────────────────────────────────
# COG
# ─────────────────────────────────────────────────────────────────────────────

class UX(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="help", description="Menu de ajuda interativo com navegação por categoria.")
    async def help_command(self, ctx: commands.Context):
        view  = HelpView(ctx.author.id, bot=self.bot)
        embed = view._build_embed()
        await ctx.send(embed=embed, view=view, ephemeral=True)

    @commands.hybrid_command(name="about", description="Informações e créditos do Darke Store Bot.")
    async def about(self, ctx: commands.Context):
        bot = self.bot

        # Conta membros únicos em todos os servidores
        total_members = sum(g.member_count for g in bot.guilds)

        embed = discord.Embed(
            title="🤖 Darke Store Bot — v4",
            description=(
                "Bot de suporte, segurança e moderação desenvolvido exclusivamente para a **Darke Store**.\n\n"
                "Combina IA generativa (Groq) com sistemas avançados de proteção anti-nuke, anti-raid, "
                "moderação automatizada e analytics em tempo real."
            ),
            color=discord.Color.blurple()
        )

        if bot.user and bot.user.display_avatar:
            embed.set_thumbnail(url=bot.user.display_avatar.url)

        embed.add_field(name="📡 Latência",    value=f"{round(bot.latency*1000)}ms", inline=True)
        embed.add_field(name="🌐 Servidores",  value=str(len(bot.guilds)),           inline=True)
        embed.add_field(name="👥 Membros",     value=f"{total_members:,}",           inline=True)
        embed.add_field(name="🧩 Módulos",     value=str(len(bot.cogs)),             inline=True)
        embed.add_field(name="⚡ Comandos",    value=str(len(bot.commands)),         inline=True)
        embed.add_field(name="🐍 discord.py",  value="2.3+",                         inline=True)

        embed.add_field(
            name="🛠️ Tecnologias",
            value=(
                "• **IA:** Groq (Llama 3.1)\n"
                "• **Banco:** SQLite (WAL mode)\n"
                "• **Framework:** discord.py 2.x\n"
                "• **Hospedagem:** Configurável"
            ),
            inline=False
        )

        embed.set_footer(text="Darke Store Bot v5 • Desenvolvido por Manus AI • Use .help para ver todos os comandos")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="serverinfo", description="Informações detalhadas do servidor.")
    async def serverinfo(self, ctx: commands.Context):
        guild = ctx.guild
        if not guild:
            return

        text_ch  = len(guild.text_channels)
        voice_ch = len(guild.voice_channels)
        cats     = len(guild.categories)
        roles    = len(guild.roles) - 1  # sem @everyone
        bots     = sum(1 for m in guild.members if m.bot)
        humans   = guild.member_count - bots

        embed = discord.Embed(
            title=f"🌐 {guild.name}",
            color=discord.Color.blurple()
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.add_field(name="👑 Dono",        value=f"<@{guild.owner_id}>",    inline=True)
        embed.add_field(name="🆔 ID",          value=str(guild.id),             inline=True)
        embed.add_field(name="📅 Criado em",   value=guild.created_at.strftime("%d/%m/%Y"), inline=True)
        embed.add_field(name="👥 Membros",     value=f"👤 {humans} | 🤖 {bots}",  inline=True)
        embed.add_field(name="💬 Canais",      value=f"📝 {text_ch} | 🔊 {voice_ch} | 📁 {cats}", inline=True)
        embed.add_field(name="🏷️ Cargos",     value=str(roles),                inline=True)
        embed.add_field(name="🚀 Boost",       value=f"Nível {guild.premium_tier} ({guild.premium_subscription_count} boosts)", inline=True)

        if guild.banner:
            embed.set_image(url=guild.banner.url)

        embed.set_footer(text=f"Região: {str(guild.preferred_locale)}")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(UX(bot))
