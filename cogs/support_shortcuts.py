"""
cogs/support_shortcuts.py  ─  v1
──────────────────────────────────
Atalhos de Resposta Rápida para Suporte — Darke Store Bot

Permite que admins/staff enviem respostas padronizadas e formatadas
rapidamente, sem digitar tudo manualmente.

Comandos:
  .resp optifine   → tutorial completo de ativação de capa
  .resp recovery   → tutorial do recovery code
  .resp email4     → explicação do formato email:senha:email:senha
  .resp proteção   → checklist de proteção da conta
  .resp ticket     → redireciona para ticket
  .resp limpar     → limpa histórico de IA do usuário mencionado (admin)
  .respostas       → lista todos os atalhos disponíveis
"""

import discord
from discord.ext import commands

from utils.cache import OWNER_ID, TICKET_URL
from utils.logger import log

# ── Templates de resposta ─────────────────────────────────────────────────────
# Cada template é um dict com: title, description, color, fields (opcional)
_TEMPLATES: dict[str, dict] = {

    "optifine": {
        "title": "🎨 Como Ativar sua Capa OptiFine",
        "color": discord.Color.green(),
        "description": (
            "Siga os passos abaixo para ativar sua capa corretamente:"
        ),
        "fields": [
            ("Passo 1 — Login", "Acesse **[optifine.net/login](https://optifine.net/login)** e entre com as credenciais fornecidas.", False),
            ("Passo 2 — Nickname", "Vá até a seção **Cape** e altere o nickname para o **seu nick no Minecraft** (exatamente como aparece no jogo).", False),
            ("Passo 3 — Lock", "Escolha a capa desejada e clique em **Lock** para fixar.", False),
            ("⚠️ Importante", "A capa só funciona com **Minecraft Java Edition original**. Não aparece em contas piratas ou na edição Bedrock.", False),
        ],
        "footer": "Ainda com problema? Clique em ❌ Ainda preciso de ajuda ou abra um ticket.",
    },

    "recovery": {
        "title": "🔑 Como Usar o Recovery Code",
        "color": discord.Color.blurple(),
        "description": "Para acessar com o formato `email:recoverycode`:",
        "fields": [
            ("Passo 1", "Acesse **[login.live.com](https://login.live.com)** e insira o e-mail fornecido.", False),
            ("Passo 2", "Clique em **\"Esqueci minha senha\"**.", False),
            ("Passo 3 — IMPORTANTE", "Selecione **\"Não tenho nenhuma dessas\"** — este clique faz o campo do código aparecer.", False),
            ("Passo 4", "Insira o Recovery Code no formato `XXXX-XXXX-XXXX-XXXX`.", False),
            ("Passo 5", "Adicione um e-mail de segurança pessoal e crie uma nova senha.\n⚠️ **Salve o novo código de recuperação gerado!**", False),
        ],
        "footer": "Dúvida no passo 3? Esse é o passo mais comum de erro.",
    },

    "email4": {
        "title": "📧 Entendendo o Formato email:senha:email:senha",
        "color": discord.Color.blurple(),
        "description": (
            "Você recebeu 4 informações separadas por `:` — veja o que cada uma significa:"
        ),
        "fields": [
            ("1º par — Login Principal", "`email_principal:senha_principal`\nUse este para entrar em **login.live.com** normalmente.", False),
            ("2º par — E-mail Vinculado", "`email_vinculado:senha_vinculado`\nUsado quando o sistema pede verificação adicional. Acesse pelo e-mail ou via **[notletters.com](https://notletters.com/email/login)** / **[firstmail.ltd](https://firstmail.ltd/en-US/webmail/login)**.", False),
            ("🎬 Tutorial em vídeo", "[Clique aqui para assistir](https://www.youtube.com/watch?v=Sa7oURQ-odc)", False),
        ],
        "footer": "Em dúvida sobre qual usar? Sempre comece pelo 1º par.",
    },

    "proteção": {
        "title": "🔐 Checklist de Proteção da Conta",
        "color": discord.Color.orange(),
        "description": "**Faça isso agora, antes de mais nada:**",
        "fields": [
            ("1. Guia Anônima", "Use **guia anônima** no navegador para todo o processo.", False),
            ("2. Troque a Senha", "Troque a senha imediatamente após o primeiro acesso.", False),
            ("3. Ative o 2FA", "Acesse [account.live.com/proofs/manage/additional](https://account.live.com/proofs/manage/additional) e ative via **Microsoft Authenticator** ou e-mail pessoal.", False),
            ("4. Sair de Todos os Locais", "Clique em **\"Sair de todos os locais\"** para desconectar sessões antigas.", False),
            ("5. Remova Dispositivos", "Acesse [account.microsoft.com/devices](https://account.microsoft.com/devices) e remova dispositivos desconhecidos.", False),
            ("6. Salve o Código", "⚠️ Salve o **novo código de recuperação** em local seguro e offline.", False),
        ],
        "footer": "A Darke Store não se responsabiliza por contas perdidas por descuido após a entrega.",
    },

    "ticket": {
        "title": "🎫 Abrir um Ticket de Suporte",
        "color": discord.Color.red(),
        "description": (
            "Para resolver seu problema com a nossa equipe:\n\n"
            f"**👉 [Clique aqui para abrir um Ticket]({TICKET_URL})**\n\n"
            "Descreva no ticket:\n"
            "• O produto comprado\n"
            "• O problema que está tendo\n"
            "• O que já tentou fazer\n"
            "• Um print da tela de erro (se houver)"
        ),
        "footer": "Nossa equipe responde em até algumas horas.",
    },
}


def _build_embed(key: str, mention: str = "") -> discord.Embed | None:
    t = _TEMPLATES.get(key)
    if not t:
        return None
    embed = discord.Embed(
        title=t["title"],
        description=(f"{mention}\n\n" if mention else "") + t.get("description", ""),
        color=t["color"]
    )
    for name, value, inline in t.get("fields", []):
        embed.add_field(name=name, value=value, inline=inline)
    if "footer" in t:
        embed.set_footer(text=t["footer"])
    return embed


class SupportShortcuts(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _is_staff(self, ctx: commands.Context) -> bool:
        """Staff = admin ou dono."""
        if str(ctx.author.id) == str(OWNER_ID):
            return True
        if ctx.guild and ctx.author.guild_permissions.manage_messages:
            return True
        return False

    @commands.command(name="resp", aliases=["r"])
    async def resp(self, ctx: commands.Context, template: str, member: discord.Member = None):
        """Envia uma resposta padronizada no canal.
        Uso: .resp <template> [@usuario]
        Templates: optifine | recovery | email4 | proteção | ticket
        """
        if not self._is_staff(ctx):
            return await ctx.message.delete()

        mention = member.mention if member else ""
        embed   = _build_embed(template.lower(), mention)

        if not embed:
            keys = " | ".join(f"`{k}`" for k in _TEMPLATES)
            return await ctx.send(
                f"❌ Template `{template}` não encontrado.\nDisponíveis: {keys}",
                ephemeral=True, delete_after=10
            )

        # Apaga o comando para manter o canal limpo
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

        await ctx.send(embed=embed)
        log.info(f"[SupportShortcuts] {ctx.author} enviou template '{template}' (menção: {mention or 'nenhuma'})")

    @commands.command(name="respostas")
    async def respostas(self, ctx: commands.Context):
        """Lista todos os templates de resposta rápida disponíveis."""
        if not self._is_staff(ctx):
            return

        embed = discord.Embed(
            title="⚡ Respostas Rápidas Disponíveis",
            description="Use `.resp <template>` para enviar. Adicione `@usuario` para mencionar.\nEx: `.resp optifine @João`",
            color=discord.Color.blurple()
        )
        for key, t in _TEMPLATES.items():
            embed.add_field(
                name=f"`.resp {key}`",
                value=t["title"],
                inline=True
            )
        embed.set_footer(text="Apenas admins e moderadores podem usar estes comandos.")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.command(name="limparusuario", hidden=True)
    async def limparusuario(self, ctx: commands.Context, member: discord.Member):
        """Admin limpa o histórico de IA de outro usuário. Útil após resolver um caso manualmente."""
        if not self._is_staff(ctx):
            return await ctx.send("❌ Sem permissão.", ephemeral=True)

        from utils.database import clear_ai_history
        clear_ai_history(member.id)

        # Limpa também o estado em memória se o cog AISupport estiver ativo
        ai_cog = self.bot.get_cog("AISupport")
        if ai_cog:
            ai_cog.ai_last_activity.pop(member.id, None)
            ai_cog.ai_session_category.pop(member.id, None)

        await ctx.send(
            f"🧹 Histórico de IA de {member.mention} limpo com sucesso.",
            ephemeral=True
        )
        log.info(f"[SupportShortcuts] {ctx.author} limpou histórico de {member} ({member.id})")


async def setup(bot: commands.Bot):
    await bot.add_cog(SupportShortcuts(bot))
