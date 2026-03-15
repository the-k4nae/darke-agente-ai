"""
cogs/ai_tools.py  ─  v9
─────────────────────────────────
Ferramentas de gestão da IA — Darke Store Bot

Fixes v9:
  ✅ maintenance_mode persistido no banco — sobrevive a restarts
  ✅ on_ready sincroniza estado e reposta aviso se sumiu
  ✅ Purge agendado de ai_history (todo dia, mensagens >30 dias)
  ✅ .reloadprompt / .manutencao on/off / .statusia
  ✅ .faqsugestao / .faqhistorico / .purgedir
"""

import discord
from discord.ext import commands, tasks
import asyncio
import os as _os
from datetime import datetime

from utils.logger import log, log_action
from utils.cache import OWNER_ID, SUPPORT_CHANNEL_ID, TICKET_URL
from utils.database import (
    get_recent_user_messages,
    save_faq_suggestion,
    get_faq_suggestions,
    set_state,
    get_state,
    purge_old_ai_history,
)

# ── Estado em memória (sincronizado com banco ao iniciar) ─────────────────────
maintenance_mode:       bool     = False
maintenance_message_id: int|None = None

MAINTENANCE_EMBED_TITLE = "🔧 Canal em Manutenção"

def _maintenance_desc() -> str:
    return (
        "A IA de suporte está temporariamente **indisponível** para manutenção.\n\n"
        "Para ajuda imediata, abre um ticket com a nossa equipe:\n"
        f"🎫 **[Abrir Ticket de Suporte]({TICKET_URL})**\n\n"
        "_Voltaremos em breve!_"
    )

_FAQ_ANALYSIS_PROMPT = """\
Você é especialista em base de conhecimento de suporte ao cliente.

Receberá perguntas reais de clientes da Darke Store (Minecraft, Game Pass, capas OptiFine).

Tarefa:
1. Identifique os TEMAS mais recorrentes
2. Para cada tema não bem coberto, sugira nova entrada para o prompt da IA

Formato obrigatório por sugestão:

---
**Tema:** [nome curto]
**Frequência estimada:** [Alta / Média / Baixa]
**Pergunta típica:** "[exemplo real ou parafrasado]"
**Sugestão para o prompt:**
[texto pronto para colar no prompt, no mesmo estilo das entradas existentes]
---

Regras:
- Máximo 5 sugestões
- NÃO sugira temas já cobertos (ativação OptiFine, recovery code, proteção de conta)
- Se não houver padrão novo: "Sem padrões novos detectados neste período."
- Responda em português
"""


class AITools(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_purge_task.start()
        # Restaura estado de manutenção ao carregar/recarregar o cog
        # (cobre reloads sem reconexão onde on_ready não dispara novamente)
        try:
            self._sync_maintenance_state()
        except Exception:
            pass  # banco pode não estar pronto no primeiro boot, on_ready cobre esse caso

    def cog_unload(self):
        self.db_purge_task.cancel()

    def _is_owner(self, ctx: commands.Context) -> bool:
        return str(ctx.author.id) == str(OWNER_ID)

    # ── on_ready: restaura estado de manutenção do banco ─────────────────────
    # Fix #11 — também chamado em __init__ para cobrir reloads de cog sem reconexão
    def _sync_maintenance_state(self):
        """Sincroniza o estado de manutenção com o banco (seguro para chamar a qualquer hora)."""
        global maintenance_mode, maintenance_message_id
        persisted = get_state("maintenance_mode", "off")
        msg_id    = get_state("maintenance_message_id", "")
        maintenance_mode       = (persisted == "on")
        maintenance_message_id = int(msg_id) if msg_id.isdigit() else None

    @commands.Cog.listener()
    async def on_ready(self):
        global maintenance_mode, maintenance_message_id
        self._sync_maintenance_state()

        if not maintenance_mode:
            return

        # Bot reiniciou em modo manutenção — verifica se a mensagem ainda existe
        log.warning("[AITools] Bot reiniciou em modo manutenção — restaurando estado.")

        if not (maintenance_message_id and SUPPORT_CHANNEL_ID):
            return

        for guild in self.bot.guilds:
            channel = guild.get_channel(SUPPORT_CHANNEL_ID)
            if not channel:
                continue
            try:
                await channel.fetch_message(maintenance_message_id)
                log.info("[AITools] Mensagem de manutenção ainda existe no canal.")
            except discord.NotFound:
                # Mensagem sumiu — repostar e fixar
                log.warning("[AITools] Mensagem de manutenção sumiu, repostando...")
                try:
                    embed = discord.Embed(
                        title=MAINTENANCE_EMBED_TITLE,
                        description=_maintenance_desc(),
                        color=discord.Color.orange()
                    )
                    msg = await channel.send(embed=embed)
                    maintenance_message_id = msg.id
                    set_state("maintenance_message_id", str(msg.id))
                    try:
                        await msg.pin(reason="Aviso restaurado após restart")
                    except discord.Forbidden:
                        pass
                except Exception as e:
                    log.error(f"[AITools] Falha ao repostar aviso: {e}")
            except Exception:
                pass

    # ── Purge diário de ai_history — executa todo dia à meia-noite UTC ─────
    @tasks.loop(hours=24)
    async def db_purge_task(self):
        try:
            deleted = purge_old_ai_history(days=30)
            if deleted > 0:
                log.info(f"[AITools] Purge: {deleted} linha(s) de ai_history removidas (>30 dias).")
        except Exception as e:
            log.error(f"[AITools] Erro no purge: {e}")

    @db_purge_task.before_loop
    async def before_purge(self):
        """
        Espera até à próxima meia-noite UTC antes do primeiro purge.
        Exceção: se o bot reiniciou dentro de 30min após meia-noite (00:00–00:30),
        o purge já rodou hoje — espera até amanhã para não purgar duas vezes.
        """
        await self.bot.wait_until_ready()
        from datetime import datetime, timezone, timedelta
        # Fix #16 — os dois branches faziam a mesma coisa; colapsado em uma linha
        now           = datetime.now(timezone.utc)
        next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        wait_secs     = (next_midnight - now).total_seconds()
        log.info(f"[AITools] Purge de ai_history agendado para meia-noite UTC ({wait_secs/3600:.1f}h).")
        await asyncio.sleep(wait_secs)

    # ─────────────────────────────────────────────────────────────────────────
    # HOT RELOAD DO PROMPT
    # ─────────────────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="reloadprompt", description="Recarrega prompt.txt sem reiniciar o bot (Apenas Dono).")
    async def reloadprompt(self, ctx: commands.Context):
        if not self._is_owner(ctx):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)

        await ctx.defer(ephemeral=True)

        from pathlib import Path
        prompt_path = Path(__file__).parent.parent / "prompt.txt"

        if not prompt_path.exists():
            return await ctx.send(f"❌ `prompt.txt` não encontrado em `{prompt_path}`.", ephemeral=True)

        try:
            new_prompt_raw = prompt_path.read_text(encoding="utf-8")
        except Exception as e:
            return await ctx.send(f"❌ Erro ao ler: `{e}`", ephemeral=True)

        if len(new_prompt_raw.strip()) < 20:
            return await ctx.send("❌ `prompt.txt` parece vazio ou corrompido. Operação cancelada.", ephemeral=True)

        reloaded, failed = [], []

        ai_cog = self.bot.get_cog("AISupport")
        if ai_cog:
            try:
                # Delega ao método reload_prompt() que já cuida da injeção de TICKET_URL
                ai_cog.reload_prompt()
                reloaded.append("`AISupport` (PT + EN, TICKET_URL injetado)")
            except Exception as e:
                failed.append(f"`AISupport`: {e}")
        else:
            failed.append("`AISupport` (cog não encontrado)")

        embed = discord.Embed(
            title="🔄 Prompt Recarregado",
            color=discord.Color.green() if not failed else discord.Color.orange()
        )
        embed.add_field(name="✅ Atualizado em", value="\n".join(reloaded) or "_nenhum_", inline=False)
        if failed:
            embed.add_field(name="❌ Falhas", value="\n".join(failed), inline=False)
        embed.add_field(name="📄 Primeira linha", value=f"`{new_prompt_raw.strip().splitlines()[0][:80]}`", inline=False)
        embed.add_field(name="📦 Tamanho", value=f"{len(new_prompt_raw):,} chars", inline=True)
        embed.add_field(name="🕐 Horário", value=datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC"), inline=True)
        embed.set_footer(text="Aplicado imediatamente — sem reiniciar o bot.")
        await ctx.send(embed=embed, ephemeral=True)
        await log_action(ctx.guild, f"🔄 **Prompt recarregado** por {ctx.author.mention} — {len(new_prompt_raw):,} chars")

    # ─────────────────────────────────────────────────────────────────────────
    # MODO MANUTENÇÃO
    # ─────────────────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="manutencao", description="Ativa/desativa modo manutenção da IA (Apenas Dono).")
    async def manutencao(self, ctx: commands.Context, acao: str):
        if not self._is_owner(ctx):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)

        global maintenance_mode, maintenance_message_id

        acao = acao.lower().strip()
        if acao not in ("on", "off"):
            return await ctx.send("⚠️ Use `.manutencao on` ou `.manutencao off`.", ephemeral=True)

        await ctx.defer(ephemeral=True)

        # ── ATIVAR ────────────────────────────────────────────────────────────
        if acao == "on":
            if maintenance_mode:
                return await ctx.send("⚠️ Modo manutenção já está **ativo**.", ephemeral=True)

            maintenance_mode = True
            set_state("maintenance_mode", "on")

            if SUPPORT_CHANNEL_ID:
                channel = ctx.guild.get_channel(SUPPORT_CHANNEL_ID)
                if channel:
                    try:
                        embed = discord.Embed(
                            title=MAINTENANCE_EMBED_TITLE,
                            description=_maintenance_desc(),
                            color=discord.Color.orange()
                        )
                        msg = await channel.send(embed=embed)
                        maintenance_message_id = msg.id
                        set_state("maintenance_message_id", str(msg.id))
                        try:
                            await msg.pin(reason="Aviso de manutenção")
                        except discord.Forbidden:
                            pass
                    except Exception as e:
                        log.warning(f"[Manutenção] Falha ao postar aviso: {e}")

            embed_r = discord.Embed(
                title="🔧 Modo Manutenção Ativado",
                description=(
                    "✅ IA **pausada**.\n\n"
                    "• Mensagens no canal não serão respondidas\n"
                    "• Aviso postado e fixado no canal de suporte\n"
                    "• **Estado salvo no banco** — sobrevive a restarts\n"
                    "• Use `.manutencao off` para reativar"
                ),
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed_r, ephemeral=True)
            await log_action(ctx.guild, f"🔧 **Modo Manutenção ATIVADO** por {ctx.author.mention}")

        # ── DESATIVAR ─────────────────────────────────────────────────────────
        else:
            if not maintenance_mode:
                return await ctx.send("⚠️ Modo manutenção já está **inativo**.", ephemeral=True)

            maintenance_mode = False
            set_state("maintenance_mode", "off")
            set_state("maintenance_message_id", "")

            if SUPPORT_CHANNEL_ID and maintenance_message_id:
                channel = ctx.guild.get_channel(SUPPORT_CHANNEL_ID)
                if channel:
                    try:
                        msg = await channel.fetch_message(maintenance_message_id)
                        await msg.unpin(reason="Manutenção encerrada")
                        await msg.delete()
                    except Exception as e:
                        log.warning(f"[Manutenção] Não removeu mensagem: {e}")
                maintenance_message_id = None

            # Mensagem de retorno (auto-apaga em 30s)
            if SUPPORT_CHANNEL_ID:
                channel = ctx.guild.get_channel(SUPPORT_CHANNEL_ID)
                if channel:
                    try:
                        back = await channel.send(embed=discord.Embed(
                            description="✅ A IA de suporte está de volta! Como posso te ajudar?",
                            color=discord.Color.green()
                        ))
                        await asyncio.sleep(30)
                        await back.delete()
                    except Exception:
                        pass

            embed_r = discord.Embed(
                title="✅ Modo Manutenção Desativado",
                description="IA **ativa** novamente.\n\n• Aviso removido do canal\n• Estado limpo no banco",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed_r, ephemeral=True)
            await log_action(ctx.guild, f"✅ **Modo Manutenção DESATIVADO** por {ctx.author.mention}")

    @commands.hybrid_command(name="statusia", description="Mostra se a IA está ativa ou em manutenção.")
    async def statusia(self, ctx: commands.Context):
        if maintenance_mode:
            embed = discord.Embed(
                title="🔧 IA em Manutenção",
                description="IA pausada. Use `.manutencao off` para reativar.",
                color=discord.Color.orange()
            )
        else:
            ai_cog = self.bot.get_cog("AISupport")
            ok     = ai_cog and hasattr(ai_cog, "system_prompt_pt") and len(ai_cog.system_prompt_pt) > 20
            embed  = discord.Embed(title="✅ IA Operacional", color=discord.Color.green())
            embed.add_field(name="📄 Prompt", value="✅ Carregado" if ok else "⚠️ Não carregado", inline=True)
            embed.add_field(name="🧩 Cog",    value="✅ Ativo"    if ai_cog else "❌ Não encontrado", inline=True)
        await ctx.send(embed=embed, ephemeral=True)

    # ─────────────────────────────────────────────────────────────────────────
    # FAQ DINÂMICO
    # ─────────────────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="faqsugestao", description="IA analisa perguntas e sugere entradas pro prompt (Apenas Dono).")
    async def faqsugestao(self, ctx: commands.Context, dias: int = 7):
        if not self._is_owner(ctx):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        if not 1 <= dias <= 30:
            return await ctx.send("⚠️ Use entre 1 e 30 dias.", ephemeral=True)

        await ctx.defer(ephemeral=True)
        messages = get_recent_user_messages(days=dias, limit=300)

        if len(messages) < 5:
            return await ctx.send(
                f"⚠️ Apenas **{len(messages)}** mensagens nos últimos {dias} dias. Necessário mínimo de 5.",
                ephemeral=True
            )

        sample = "\n".join(f"- {m[:120]}" for m in messages[:150])
        await ctx.send(f"🔍 Analisando **{len(messages)}** mensagens dos últimos {dias} dias...", ephemeral=True)

        try:
            from utils.groq_pool import get_pool
            result = await get_pool().complete(
                model=_os.getenv("GROQ_MODEL_TEXT", "llama-3.1-8b-instant"),
                messages=[
                    {"role": "system", "content": _FAQ_ANALYSIS_PROMPT},
                    {"role": "user",   "content": f"Perguntas (últimos {dias} dias):\n\n{sample}"},
                ],
                temperature=0.3,
                max_tokens=1500,
            )
        except Exception as e:
            return await ctx.send(f"❌ Erro ao chamar a IA: `{e}`", ephemeral=True)

        if not result or len(result.strip()) < 30:
            return await ctx.send("⚠️ IA não retornou análise válida. Tente novamente.", ephemeral=True)

        save_faq_suggestion(ctx.guild.id, result, dias)

        chunks, current = [], ""
        for line in result.splitlines():
            if len(current) + len(line) + 1 > 1000:
                chunks.append(current)
                current = line + "\n"
            else:
                current += line + "\n"
        if current:
            chunks.append(current)

        embed = discord.Embed(
            title=f"🧠 Sugestões de FAQ — Últimos {dias} dias",
            description=f"Baseado em **{len(messages)}** perguntas reais.",
            color=discord.Color.blurple()
        )
        for i, chunk in enumerate(chunks[:8], 1):
            embed.add_field(
                name=f"📋 {'Análise' if i == 1 else 'cont.'}",
                value=chunk.strip()[:1020] or "_vazio_",
                inline=False
            )
        embed.set_footer(text="Use .reloadprompt após atualizar o prompt.txt | .faqhistorico para rever")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="faqhistorico", description="Exibe últimas sugestões de FAQ (Apenas Dono).")
    async def faqhistorico(self, ctx: commands.Context):
        if not self._is_owner(ctx):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)

        suggestions = get_faq_suggestions(ctx.guild.id, limit=3)
        if not suggestions:
            return await ctx.send("ℹ️ Nenhuma sugestão gerada ainda. Use `.faqsugestao`.", ephemeral=True)

        embed = discord.Embed(title="📚 Histórico de Sugestões de FAQ", color=discord.Color.blurple())
        for i, s in enumerate(suggestions, 1):
            preview = s["suggestion"][:300].replace("\n", " ")
            embed.add_field(
                name=f"#{i} — {s['created_at'][:16]} ({s['period_days']}d)",
                value=f"{preview}{'...' if len(s['suggestion']) > 300 else ''}",
                inline=False
            )
        embed.set_footer(text="Use .faqsugestao [dias] para gerar nova análise.")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.command(name="purgedir", hidden=True)
    async def purgedb(self, ctx: commands.Context, dias: int = 30):
        if not self._is_owner(ctx):
            return
        deleted = purge_old_ai_history(days=dias)
        await ctx.send(f"🗑️ **{deleted}** linha(s) de `ai_history` mais antigas que {dias} dias removidas.", ephemeral=True)

    # ─────────────────────────────────────────────────────────────────────────
    # SIMULAR — testa IA sem usar o canal de suporte real
    # ─────────────────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="simular", description="Testa como a IA responderia a uma mensagem (Apenas Dono).")
    async def simular(self, ctx: commands.Context, *, mensagem: str):
        """Simula uma resposta da IA sem registrar histórico nem usar o canal de suporte.
        Ideal para testar mudanças no prompt.txt após .reloadprompt.
        Uso: .simular não consigo ativar minha capa
        """
        if not self._is_owner(ctx):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)

        await ctx.defer(ephemeral=True)

        ai_cog = self.bot.get_cog("AISupport")
        if not ai_cog:
            return await ctx.send("❌ Cog `AISupport` não está carregado.", ephemeral=True)

        try:
            from utils.groq_pool import get_pool
            import os as _os_sim

            model     = _os_sim.getenv("GROQ_MODEL_TEXT", "llama-3.1-8b-instant")
            prompt_pt = getattr(ai_cog, "system_prompt_pt", "Você é um assistente da Darke Store.")

            raw_reply = await get_pool().complete(
                model=model,
                messages=[
                    {"role": "system", "content": prompt_pt},
                    {"role": "user",   "content": mensagem},
                ],
                temperature=0.7,
                max_tokens=500,
            )

            reply = ai_cog.sanitize(raw_reply) if hasattr(ai_cog, "sanitize") else raw_reply

            embed = discord.Embed(
                title="🧪 Simulação de Resposta da IA",
                color=discord.Color.blurple()
            )
            embed.add_field(name="📩 Mensagem de entrada", value=f"_{mensagem[:500]}_", inline=False)
            # Divide resposta longa em múltiplos fields se necessário
            if len(reply) <= 1020:
                embed.add_field(name="🤖 Resposta da IA", value=reply, inline=False)
            else:
                for i, chunk in enumerate([reply[j:j+1020] for j in range(0, len(reply), 1020)][:4], 1):
                    embed.add_field(name=f"🤖 Resposta ({i})", value=chunk, inline=False)
            embed.set_footer(
                text=f"Modelo: {model} | Prompt: {len(prompt_pt):,} chars | "
                     "Simulação não registra histórico nem afeta cooldowns."
            )
            await ctx.send(embed=embed, ephemeral=True)

        except Exception as e:
            await ctx.send(f"❌ Erro na simulação: `{e}`", ephemeral=True)

    # ─────────────────────────────────────────────────────────────────────────
    # EXPORTAR HISTÓRICO — gera .txt com toda a conversa de um usuário
    # ─────────────────────────────────────────────────────────────────────────

    @commands.hybrid_command(name="exportarhistorico", description="Exporta o histórico de conversa de IA de um usuário como .txt (Apenas Dono).")
    async def exportarhistorico(self, ctx: commands.Context, member: discord.Member):
        """Gera um arquivo .txt com toda a conversa de IA do usuário.
        Útil para o suporte humano entender o contexto antes de atender um ticket.
        Uso: .exportarhistorico @usuario
        """
        if not self._is_owner(ctx):
            return await ctx.send("❌ Apenas o dono pode usar este comando.", ephemeral=True)

        await ctx.defer(ephemeral=True)

        from utils.database import get_ai_history
        history = get_ai_history(member.id)

        if not history:
            return await ctx.send(
                f"ℹ️ {member.mention} não tem histórico de conversa de IA no momento.",
                ephemeral=True
            )

        # Monta o conteúdo do arquivo
        lines = [
            f"Histórico de Suporte IA — Darke Store Bot",
            f"Usuário: {member} (ID: {member.id})",
            f"Exportado em: {datetime.utcnow().strftime('%d/%m/%Y %H:%M UTC')}",
            f"Total de mensagens: {len(history)}",
            "=" * 60,
            "",
        ]
        for i, msg in enumerate(history, 1):
            role  = "👤 USUÁRIO" if msg["role"] == "user" else "🤖 IA"
            lines.append(f"[{i}] {role}")
            lines.append(msg["content"])
            lines.append("")

        lines += [
            "=" * 60,
            "Fim do histórico.",
            "Use este arquivo como contexto ao abrir um ticket.",
        ]

        content = "\n".join(lines)

        # Envia como arquivo anexo
        import io
        file_bytes = content.encode("utf-8")
        file_obj   = discord.File(
            fp=io.BytesIO(file_bytes),
            filename=f"historico_{member.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt"
        )

        embed = discord.Embed(
            title="📄 Histórico Exportado",
            description=(
                f"**Usuário:** {member.mention}\n"
                f"**Mensagens:** {len(history)}\n\n"
                "Arquivo .txt pronto para anexar ao ticket de suporte."
            ),
            color=discord.Color.green()
        )
        await ctx.send(embed=embed, file=file_obj, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AITools(bot))
