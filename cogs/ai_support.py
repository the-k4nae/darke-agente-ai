import discord
from discord.ext import commands
import time
import re
import asyncio
import aiohttp
from utils.groq_pool import get_pool
from utils.cache import config, ai_cooldown_cache, SUPPORT_CHANNEL_ID, TICKET_URL
from utils.database import (
    get_ai_history, append_ai_history, clear_ai_history,
    log_ai_quality, get_ai_quality_stats, log_command
)
from utils.logger import log


# Fix #16 — modelos configuráveis via .env para facilitar troca sem editar código
# GROQ_MODEL_TEXT=llama-3.1-8b-instant
# GROQ_MODEL_VISION=meta-llama/llama-4-scout-17b-16e-instruct
import os as _os
MODEL_TEXT   = _os.getenv("GROQ_MODEL_TEXT",   "llama-3.1-8b-instant")
MODEL_VISION = _os.getenv("GROQ_MODEL_VISION", "meta-llama/llama-4-scout-17b-16e-instruct")

# ── Categorias de triagem visual ──────────────────────────────────────────────
# Retornadas pela etapa de triagem antes da resposta principal
TRIAGE_CATEGORIES = {
    "erro_ativacao":    ("🔑 Erro de Ativação",      discord.Color.red()),
    "conta_errada":     ("👤 Conta Errada",           discord.Color.orange()),
    "produto_diferente":("📦 Produto Diferente",      discord.Color.gold()),
    "erro_login":       ("🔐 Erro de Login",          discord.Color.red()),
    "erro_download":    ("⬇️ Erro de Download",       discord.Color.orange()),
    "compra_pendente":  ("⏳ Compra Pendente",         discord.Color.yellow()),
    "screenshot_geral": ("🖼️ Screenshot Enviado",     discord.Color.blurple()),
    "outro":            ("❓ Problema Identificado",  discord.Color.blurple()),
}

# Prompt de triagem — chamado ANTES da resposta principal
# Retorna apenas JSON com categoria e resumo do problema
_TRIAGE_PROMPT = """Você é um Especialista de Suporte Técnico de Nível 3 da Darke Store.
Sua missão é extrair dados técnicos CRÍTICOS de screenshots (Minecraft, Game Pass, OptiFine).

Analise a imagem e responda SOMENTE com JSON válido:
{
  "categoria": "erro_ativacao | conta_errada | produto_diferente | erro_login | erro_download | compra_pendente | screenshot_geral | outro",
  "problema_resumido": "Descrição técnica curta do que vê",
  "codigo_erro": "Ex: 0x800..., 'Código já usado', 'Região Inválida', ou null se não houver",
  "detalhes_visuais": ["lista de elementos-chave vistos"],
  "urgencia": "alta | media | baixa"
}

Prioridade máxima: Identificar códigos hexadecimais (0x...) ou mensagens de erro entre aspas.
Responda APENAS com o JSON."""

_VISION_RESPONSE_PROMPT = """Você é o Especialista Técnico da Darke Store atendendo um cliente que enviou um screenshot.

DIRETRIZES DE RESPOSTA:
1. **Diagnóstico Técnico:** Se houver um código de erro (ex: 0x803F8001), explique EXATAMENTE o que ele significa no ecossistema Minecraft/Microsoft.
2. **Análise Visual:** "Notei na sua imagem que [detalhe específico da UI ou erro]".
3. **Plano de Ação:**
   - Se for erro de região: "Sua conta Microsoft está em uma região diferente do código."
   - Se for erro de login: "Verifique se você está na guia anônima conforme o tutorial."
   - Se for erro de download: "Limpe o cache da Microsoft Store."

ESTRUTURA:
**🔍 Diagnóstico:** [O que é o erro]
**💡 Causa:** [Por que aconteceu]
**🛠️ Solução Passo a Passo:**
1. [Ação 1]
2. [Ação 2]
3. [Ação 3]

Seja ultra-específico. Se vir o nickname do jogador no print, use-o para personalizar.
Se o erro for fatal ou sem solução automática, encaminhe para o Ticket imediatamente."""

# ── Sessão em memória ─────────────────────────────────────────────────────────
# FIX #7 — estado movido para instância do cog (veja AISupport.__init__)
# Mantido aqui SOMENTE para compatibilidade com imports externos que possam existir.
# Não use diretamente — acesse via cog.ai_last_activity / cog.ai_session_category.
AI_SESSION_TIMEOUT_HOURS = 2

# ─────────────────────────────────────────────────────────────────────────────
# PADRÕES COMPILADOS
# ─────────────────────────────────────────────────────────────────────────────

# Ruído puro — nem responde
NONSENSE_PATTERN = re.compile(
    r'^[\W_\s]{1,5}$'       # só pontuação/espaço
    r'|^(.)\1{3,}$'         # mesmo char repetido 4+ vezes
    r'|^[\d\s]{1,6}$',      # só números curtos
    re.IGNORECASE
)

# Saudação PURA — texto inteiro é só saudação
# Usa search() com âncoras para não falhar em frases mistas
GREETING_PATTERN = re.compile(
    r'^\s*(oi+|ol[aá]|e\s*a[íi]|eae|opa|hey+|hi+|hello+|'
    r'good\s*(morning|afternoon|evening|night)|'
    r'bom\s*di[ao]|boa\s*tarde|boa\s*noite|'
    r'tudo\s*(bem|bom|certo|ok)|blz|beleza|salve|fala\s*a[íi]?|'
    r'oie|oii+|heey+|heyy+|oi\s*sumid[ao])\s*[!?.]*\s*$',
    re.IGNORECASE
)

# Agradecimento PURO
THANKS_PATTERN = re.compile(
    r'^\s*(obrigad[ao]s?|vlw+|valeu+|mt\s*obrigad[ao]|muito\s*obrigad[ao]|'
    r'thanks?+|thank\s*you|thx+|ty+|grat[ao]|'
    r'ok\s*obg|ok\s*vlw|ok\s*valeu|consegui|deu\s*certo|'
    r'funcionou|resolveu|perfeito|ótimo|top|show|massa|bacana)\s*[!?.]*\s*$',
    re.IGNORECASE
)

# Confirmação/resposta curta pura
CONFIRM_PATTERN = re.compile(
    r'^\s*(sim|não|nao|ok+|okay|certo|claro|já|ja|talvez|'
    r'pode\s*ser|yes+|no+|yep|nope|sure|'
    r'aham+|hmm+|hm+|ahh*|ohh*|uhh*)\s*[!?.]*\s*$',
    re.IGNORECASE
)

# Frustração — SEMPRE mostra botões
FRUSTRATION_PATTERNS = re.compile(
    r'\b(irritado|frustrado|raiva|absurdo|inaceit[aá]vel|lixo|'
    r'p[eu]ta|ridículo|p[eu]ssimo|horrível|'
    r'não funciona|nada funciona|que merda|'
    r'annoyed|frustrated|angry|ridiculous|terrible|useless|garbage|wtf)\b',
    re.IGNORECASE
)

# Keywords de produto/suporte — mostra botões direto
SUPPORT_KEYWORDS = re.compile(
    r'\b(optifine|capa|cape|skin|lock|minecraft|game\s*pass|xbox|microsoft|'
    r'recovery\s*code|recovery|recoverycode|senha|password|login|conta|email|'
    r'notletters|firstmail|webmail|ativar|ativa[çc][aã]o|acesso|acessar|'
    r'comprei|paguei|pagamento|reembolso|troca|n[aã]o\s*recebi|n[aã]o\s*chegou|'
    r'n[aã]o\s*consigo|n[aã]o\s*abre|n[aã]o\s*funciona|deu\s*erro|erro|bug|problema|'
    r'ajuda|help|suporte|support|como\s+fa[çc]o|como\s+ativo|'
    r'tutorial|passo|credencial|credenciais|produto|compra|pedido)\b',
    re.IGNORECASE
)

# Tipos de imagem suportados pelo Groq Vision
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp"}

# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFICADOR SEMÂNTICO (Camada 3 — zona cinzenta)
# ─────────────────────────────────────────────────────────────────────────────

_CLASSIFIER_PROMPT = """\
Você é um classificador binário ESTRITO para canal de suporte de loja virtual.

Responda APENAS "sim" se a mensagem envolve CLARAMENTE:
- Problema técnico com produto (Minecraft, Game Pass, capa OptiFine)
- Dúvida sobre como usar, ativar ou acessar produto comprado
- Reclamação sobre entrega, pagamento ou reembolso
- Erro específico relatado (mensagem de erro, etapa travada)
- Pedido de ajuda explícito com produto da loja

Responda APENAS "não" em TODOS os outros casos:
- Saudações simples, mesmo que longas
- Agradecimentos ou confirmações
- Perguntas genéricas sem citar produto específico
- Curiosidade, conversa casual, piadas, charadas ou brincadeiras
- Mensagem com menos de 5 palavras sem citar produto
- Qualquer dúvida sobre assunto que não seja produto da loja (ex: Minecraft, Game Pass, Capas)

Em caso de dúvida → "não".
Apenas "sim" ou "não". Nenhuma outra palavra.
"""

# ── Triagem de categoria ──────────────────────────────────────────────────────
CATEGORY_PATTERNS = {
    "🟩 OptiFine":              re.compile(r'\b(optifine|capa|cape|skin|lock|optif\.?net)\b', re.IGNORECASE),
    "🟦 Minecraft / Game Pass": re.compile(r'\b(minecraft|game\s*pass|xbox|microsoft|recovery\s*code|recovery|conta|email|senha|password|login\.live)\b', re.IGNORECASE),
    "🌐 Sites / Login":         re.compile(r'\b(notletters|firstmail|webmail|login alternativo)\b', re.IGNORECASE),
    "💳 Compra / Reembolso":    re.compile(r'\b(compro|comprei|paguei|pagamento|reembolso|troca|n[aã]o recebi|n[aã]o chegou)\b', re.IGNORECASE),
}

def detect_category(text: str) -> str | None:
    for name, pattern in CATEGORY_PATTERNS.items():
        if pattern.search(text):
            return name
    return None

def detect_frustration(text: str) -> bool:
    return bool(FRUSTRATION_PATTERNS.search(text))

def detect_language(text: str) -> str:
    """Detecta idioma do texto: retorna 'en' ou 'pt'.

    Heurísticas em ordem de confiança:
    1. Caracteres exclusivamente portugueses (ã, õ, ç, á, é, ê, etc.) → PT imediato
    2. Palavras exclusivamente inglesas (sem equivalente PT) → EN bump
    3. Contagem de palavras nas wordlists expandidas → tiebreak
    4. Empate ou ambíguo → PT (audiência principal é brasileira)
    """
    # Heurística 1 — caracteres exclusivamente lusófonos
    _PT_CHARS = set("ãõçáéíóúâêôàü")
    if any(c in _PT_CHARS for c in text.lower()):
        return "pt"

    en_words = {
        # Pronomes / artigos
        "what", "how", "when", "where", "why", "which", "who", "whose",
        "the", "a", "an", "this", "that", "these", "those", "my", "your",
        "his", "her", "its", "our", "their", "i", "you", "he", "she", "we", "they",
        # Verbos auxiliares
        "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would",
        "can", "could", "should", "shall", "may", "might", "must",
        # Conjunções / preposições comuns
        "and", "or", "but", "if", "because", "although", "while",
        "in", "on", "at", "to", "for", "of", "with", "by", "from",
        "about", "into", "through", "after", "before", "between",
        # Palavras de suporte / UX
        "please", "need", "want", "get", "not", "help", "use",
        "account", "error", "code", "link", "game", "pass", "key",
        "login", "email", "password", "access", "install", "download",
        "already", "still", "yet", "just", "also", "too",
    }

    pt_words = {
        # Pronomes / artigos
        "como", "quando", "onde", "porque", "qual", "quais", "quem",
        "o", "a", "os", "as", "um", "uma", "uns", "umas",
        "minha", "meu", "meus", "minhas", "seu", "sua", "seus", "suas",
        "nosso", "nossa", "nossos", "nossas", "isso", "esse", "esta",
        "ele", "ela", "eles", "elas", "eu", "tu", "voce",
        # Verbos
        "tenho", "tem", "estar", "estou", "está", "estão",
        "posso", "pode", "poderia", "preciso", "precisa", "quero", "quer",
        "fazer", "fiz", "faz", "feito", "consegui", "consegue", "dar",
        # Conjunções / preposições
        "mas", "pois", "para", "por", "com", "sem", "sobre", "entre",
        "aqui", "ali", "la", "sim", "nao", "ate", "desde",
        # Palavras específicas de suporte
        "ajuda", "problema", "erro", "conta", "senha", "acesso",
        "ativar", "ativação", "comprei", "paguei", "não", "tudo",
        "olá", "oi", "obrigado", "obrigada", "valeu", "vlw",
        "funcionou", "consegui", "deu", "certo", "errado",
    }

    words    = re.findall(r'\b\w+\b', text.lower())
    en_score = sum(1 for w in words if w in en_words)
    pt_score = sum(1 for w in words if w in pt_words)
    return "en" if en_score > pt_score + 1 else "pt"  # PT precisa de 2+ scores de vantagem EN

# ─────────────────────────────────────────────────────────────────────────────
# DECISOR DE BOTÕES — 3 camadas
# Retorna: "no_reply" | "no_buttons" | "show_buttons"
# ─────────────────────────────────────────────────────────────────────────────

async def decide_buttons(user_text: str, ai_reply: str, pool, is_frustrated: bool,
                          has_image: bool = False) -> str:
    text = user_text.strip()

    # Camada 0 — ruído puro (nem chegou aqui normalmente, mas por segurança)
    if not has_image and (len(text) < 2 or NONSENSE_PATTERN.match(text)):
        return "no_reply"

    # Camada 1A — frustração → sempre botões
    if is_frustrated:
        return "show_buttons"

    # Camada 1B — imagem enviada → sempre botões (usuário está mostrando um problema)
    if has_image:
        return "show_buttons"

    # Camada 1C — saudação/agradecimento/confirmação pura → sem botões
    if GREETING_PATTERN.match(text) or THANKS_PATTERN.match(text) or CONFIRM_PATTERN.match(text):
        return "no_buttons"

    # Camada 2 — keyword de produto detectada → botões direto
    if SUPPORT_KEYWORDS.search(text):
        return "show_buttons"

    # Camada 3 — Verificação proativa de resolução (Prompt Rule #73)
    # Se a IA terminou com uma pergunta de confirmação (ex: "Funcionou?"), mostra botões.
    # Isso cobre casos onde a IA deu uma solução mas não usou keywords óbvias.
    confirmation_questions = ["conseguiu acessar?", "funcionou?", "a capa apareceu?", "deu certo?"]
    if any(q in ai_reply.lower() for q in confirmation_questions):
        return "show_buttons"

    # Camada 4 — zona cinzenta → classificador semântico
    try:
        answer = await pool.complete(
            model=MODEL_TEXT,
            messages=[
                {"role": "system", "content": _CLASSIFIER_PROMPT},
                {"role": "user",   "content": f"Mensagem: {text[:300]}\nResposta do assistente: {ai_reply[:200]}"},
            ],
            temperature=0.0,
            max_tokens=3,
        )
        result = answer.strip().lower()
        log.debug(f"[Classifier] '{text[:60]}' → '{result}'")
        
        # Só mostra botões se o classificador disser SIM E não for uma resposta muito curta (evita falso positivo em piadas/papo furado)
        if result.startswith("sim") and len(ai_reply) > 50:
            return "show_buttons"
        return "no_buttons"
    except Exception as e:
        log.warning(f"[Classifier] Falhou, assumindo no_buttons: {e}")
        return "no_buttons"

# ─────────────────────────────────────────────────────────────────────────────
# SUPORTE A IMAGENS — baixa e converte para base64 para o Groq Vision
# ─────────────────────────────────────────────────────────────────────────────

# Limite de tamanho de imagem: 8MB — evita travar o bot com imagens enormes
IMAGE_MAX_BYTES = 8 * 1024 * 1024

async def fetch_image_b64(url: str, content_type: str, session: aiohttp.ClientSession | None = None) -> str | None:
    """Baixa imagem da CDN do Discord e retorna base64. Rejeita imagens > 8MB.
    
    FIX #2 — aceita uma sessão compartilhada para evitar criar/destruir
    ClientSession a cada chamada (desperdício de recursos e ResourceWarning).
    Se session=None, cria uma sessão temporária (fallback).
    """
    import base64
    
    async def _download(sess: aiohttp.ClientSession) -> str | None:
        try:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                content_length = resp.content_length
                if content_length and content_length > IMAGE_MAX_BYTES:
                    log.warning(f"[Vision] Imagem recusada: {content_length/1024/1024:.1f}MB > 8MB limite")
                    return None
                data = await resp.content.read(IMAGE_MAX_BYTES + 1)
                if len(data) > IMAGE_MAX_BYTES:
                    log.warning(f"[Vision] Imagem recusada após download: {len(data)/1024/1024:.1f}MB")
                    return None
                return base64.b64encode(data).decode("utf-8")
        except asyncio.TimeoutError:
            log.warning("[Vision] Timeout ao baixar imagem")
            return None
        except Exception as e:
            log.warning(f"[Vision] Falha ao baixar imagem: {e}")
            return None

    try:
        if session:
            return await _download(session)
        else:
            async with aiohttp.ClientSession() as s:
                return await _download(s)
    except Exception as e:
        log.warning(f"[Vision] Erro inesperado: {e}")
        return None

# ── Formatador de embed ───────────────────────────────────────────────────────
STEP_PATTERN = re.compile(r'^(\d+)[.\)]\s+(.+)', re.MULTILINE)

def build_embed_response(text: str, category: str | None, is_frustrated: bool) -> discord.Embed | None:
    steps = STEP_PATTERN.findall(text)
    if len(steps) < 2:
        return None

    first_match = STEP_PATTERN.search(text)
    intro = text[:first_match.start()].strip() if first_match else ""

    color = discord.Color.orange() if is_frustrated else discord.Color.blurple()
    embed = discord.Embed(color=color)

    if category:
        embed.set_author(name=f"Categoria: {category}")
    if intro:
        embed.description = intro[:300]

    for num, content in steps[:15]:
        embed.add_field(name=f"Passo {num}", value=content.strip()[:1020], inline=False)

    last_match  = list(STEP_PATTERN.finditer(text))[-1]
    after_steps = text[last_match.end():].strip()
    if after_steps:
        embed.set_footer(text=after_steps[:250])

    return embed

# ── View dos botões ───────────────────────────────────────────────────────────
class SupportView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int, channel_id: int, category: str | None, cog=None):
        super().__init__(timeout=300)
        self.user_id    = user_id
        self.guild_id   = guild_id
        self.channel_id = channel_id
        self.category   = category
        self.cog        = cog   # referência ao AISupport para acessar ai_last_activity / ai_session_category
        self.message: discord.Message | None = None  # referência à mensagem para on_timeout

    async def _disable_all(self, interaction: discord.Interaction):
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    async def on_timeout(self):
        """Desabilita os botões quando a view expira — evita botões presos para sempre."""
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass  # mensagem pode ter sido deletada — não é crítico

    @discord.ui.button(label="✅ Meu problema foi resolvido", style=discord.ButtonStyle.success, custom_id="ai_resolved")
    async def resolved(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Estes botões são apenas para quem fez a pergunta.", ephemeral=True)
        log_ai_quality(self.user_id, self.guild_id, self.channel_id, "resolved", self.category)
        clear_ai_history(self.user_id)
        # Usa referência ao cog para limpar sessão (os atributos ficam na instância, não em globais)
        if self.cog is not None:
            self.cog.ai_last_activity.pop(self.user_id, None)
            self.cog.ai_session_category.pop(self.user_id, None)
        embed = discord.Embed(
            description="Fico feliz que o problema foi resolvido! 🎉\nSeu histórico de conversa foi limpo.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await self._disable_all(interaction)

    @discord.ui.button(label="❌ Ainda preciso de ajuda", style=discord.ButtonStyle.danger, custom_id="ai_unresolved")
    async def need_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Estes botões são apenas para quem fez a pergunta.", ephemeral=True)
        log_ai_quality(self.user_id, self.guild_id, self.channel_id, "unresolved", self.category)

        # FIX #19 — usa banco para contar reincidências (sobrevive a reloads do cog)
        try:
            from utils.database import get_user_unresolved_count
            repeat_count = get_user_unresolved_count(self.user_id, hours=1)
        except Exception:
            repeat_count = 1

        if repeat_count >= 3:
            desc = (
                "Percebi que você já precisou de ajuda várias vezes nessa questão. "
                "Nossa equipe vai resolver isso de forma definitiva:\n\n"
                f"🎫 **[Abrir Ticket Prioritário]({TICKET_URL})**\n\n"
                "_Descreva o que já tentou — isso acelera muito o atendimento._"
            )
        else:
            desc = (
                "Entendido! A nossa equipe vai te ajudar pessoalmente.\n\n"
                f"🎫 **[Abrir um Ticket de Suporte]({TICKET_URL})**"
            )

        embed = discord.Embed(description=desc, color=discord.Color.blurple())
        await self._disable_all(interaction)
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ── Cog principal ─────────────────────────────────────────────────────────────
class AISupport(commands.Cog):
    def __init__(self, bot):
        self.bot  = bot
        self.pool        = get_pool()
        self.pool.set_bot(bot)
        self._load_prompt()
        self._groq_semaphore = asyncio.Semaphore(3)
        self._processing: set[int] = set()
        self._cleanup_task   = None

        # FIX #7 — estado de sessão como atributos de instância em vez de globals.
        # Isso evita estado órfão quando o cog é recarregado via .reload.
        self.ai_last_activity:    dict[int, float] = {}
        self.ai_session_category: dict[int, str]   = {}

        # FIX #2 — sessão aiohttp compartilhada (criada no cog_load, fechada no cog_unload)
        self._http_session: aiohttp.ClientSession | None = None

        # FIX #11 — semáforos por guild para limitar chamadas Groq simultâneas
        self._guild_semaphores: dict[int, asyncio.Semaphore] = {}

        # FIX #14 — cache de deduplicação de mensagens (hash -> timestamp)
        self._last_msg_content: dict[str, float] = {}

        # FIX #12 — aviso de configuração faltando
        if not SUPPORT_CHANNEL_ID:
            log.warning(
                "[AISupport] SUPPORT_CHANNEL_ID não configurado no .env — "
                "o bot não responderá a nenhuma mensagem de suporte!"
            )

    def _load_prompt(self):
        # FIX #6 — usa Path relativo ao arquivo em vez de CWD
        from pathlib import Path
        prompt_path = Path(__file__).parent.parent / "prompt.txt"
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            log.warning(f"[AISupport] prompt.txt não encontrado em {prompt_path} — usando fallback genérico.")
            raw = "Você é um assistente da Darke Store."

        # Segurança: injeta TICKET_URL dinamicamente no prompt em vez de deixar
        # a URL hardcoded no arquivo em disco. Assim, se o prompt.txt vazar via
        # prompt injection, a URL interna não fica exposta.
        # Use o placeholder {{TICKET_URL}} no prompt.txt onde quiser a URL.
        self.system_prompt_pt = raw.replace("{{TICKET_URL}}", TICKET_URL or "")
        self.system_prompt_en = (
            self.system_prompt_pt
            + "\n\n---\n**IMPORTANT:** The user is writing in English. Reply entirely in English with the same professional tone."
        )

    # FIX #13 — método público para hot-reload do prompt sem reiniciar o cog
    # Chamado pelo comando .reloadprompt em ai_tools.py
    def reload_prompt(self):
        """Recarrega prompt.txt em memória. Seguro para chamar a qualquer hora."""
        self._load_prompt()
        log.info("[AISupport] Prompt recarregado via reload_prompt().")

    async def cog_load(self):
        # FIX #2 — cria sessão aiohttp compartilhada aqui para reutilização
        self._http_session = aiohttp.ClientSession()
        self._cleanup_task = self.bot.loop.create_task(self._session_cleanup_loop())

    def cog_unload(self):
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
        # Fecha a sessão compartilhada ao descarregar o cog
        if self._http_session and not self._http_session.closed:
            asyncio.create_task(self._http_session.close())
        try:
            loop = asyncio.get_running_loop()
            if not loop.is_closed():
                loop.create_task(self._notify_active_sessions_shutdown())
        except RuntimeError:
            pass  # não há loop rodando — não é crítico

    async def _notify_active_sessions_shutdown(self):
        """DM para usuários com sessão ativa informando que o bot vai reiniciar."""
        active = list(self.ai_last_activity.keys())  # FIX #7 — instância
        if not active:
            return
        log.info(f"[AISupport] Notificando {len(active)} sessão(ões) ativa(s) sobre restart.")
        for uid in active:
            try:
                user = self.bot.get_user(uid)
                if user:
                    await user.send(
                        "⚠️ O bot da **Darke Store** está sendo reiniciado momentaneamente. "
                        "Seu histórico de suporte foi mantido — pode continuar de onde parou!"
                    )
            except Exception:
                pass

    async def _session_cleanup_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                # FIX #10 — try/except dentro do loop: uma exceção não mata a task inteira
                now          = time.time()
                timeout_secs = AI_SESSION_TIMEOUT_HOURS * 3600
                to_clear     = [uid for uid, ts in list(self.ai_last_activity.items()) if (now - ts) > timeout_secs]
                for uid in to_clear:
                    clear_ai_history(uid)
                    self.ai_last_activity.pop(uid, None)   # FIX #7
                    self.ai_session_category.pop(uid, None) # FIX #7
                    try:
                        user = self.bot.get_user(uid)
                        if user:
                            await user.send(
                                "🕐 Sua sessão de suporte da **Darke Store** expirou por inatividade.\n"
                                "Seu histórico foi limpo. Se precisar de ajuda novamente, é só mandar mensagem no canal de suporte!"
                            )
                    except discord.Forbidden:
                        # FIX #20 — DM bloqueada pelo usuário: não é erro, só log debug
                        log.debug(f"[AISupport] FIX#20 — DM bloqueada para user {uid} (sessão expirou)")
                    except Exception:
                        pass
                if to_clear:
                    log.info(f"Sessões de IA limpas por inatividade: {len(to_clear)} usuário(s).")
            except Exception as e:
                # FIX #10 — loga o erro mas mantém o loop rodando
                log.error(f"[AISupport] Erro no cleanup loop: {e}")
            await asyncio.sleep(1800)

    def _trim_history_by_tokens(self, history: list, max_chars: int = 6000) -> list:
        """FIX #1 — garante que o histórico não exceda o limite de tokens do Groq.

        Usa contagem de chars como proxy (1 token ≈ 4 chars).
        Remove as mensagens mais antigas até caber no limite.
        Sempre preserva as 2 últimas trocas (4 mensagens) por contexto mínimo.
        """
        if not history:
            return history
        total = sum(len(m.get("content", "") if isinstance(m.get("content"), str) else "") for m in history)
        if total <= max_chars:
            return history
        while history and total > max_chars and len(history) > 4:
            removed = history.pop(0)
            content = removed.get("content", "")
            total  -= len(content) if isinstance(content, str) else 0
        return history

    def sanitize(self, text: str) -> str:
        text = text.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
        text = re.sub(r'<@&?\d+>', '', text)
        return text.strip()

    def split_message(self, text: str, limit: int = 1990) -> list[str]:
        if len(text) <= limit:
            return [text]
        parts = []
        while text:
            if len(text) <= limit:
                parts.append(text)
                break
            split_at = text.rfind('\n', 0, limit)
            if split_at == -1:
                split_at = limit
            parts.append(text[:split_at])
            text = text[split_at:].lstrip()
        return parts

    async def _send_parts(self, channel, parts: list[str], safe_send_fn, view=None):
        """FIX #9 — envia partes da mensagem com delay entre elas para evitar rate-limit."""
        for part in parts[:-1]:
            await safe_send_fn(channel, content=part)
            await asyncio.sleep(0.5)  # evita burst de rate-limit em respostas longas
        return await safe_send_fn(channel, content=parts[-1], view=view)

    async def _call_groq_text(self, messages: list) -> str:
        """Chamada padrão de texto."""
        return await self.pool.complete(
            model=MODEL_TEXT,
            messages=messages,
            temperature=0.7,
            max_tokens=500,
            top_p=1,
        )

    async def _triage_screenshot(self, image_content: list) -> dict:
        """
        Etapa de triagem: chama Vision com prompt restrito para classificar o screenshot.
        Retorna dict com categoria, problema_resumido e urgencia.
        Nunca lança exceção — retorna fallback em caso de erro.
        """
        import json as _json
        fallback = {"categoria": "outro", "problema_resumido": "Screenshot recebido", "urgencia": "media"}
        try:
            raw = await self.pool.complete(
                model=MODEL_VISION,
                messages=[
                    {"role": "system", "content": _TRIAGE_PROMPT},
                    {"role": "user",   "content": image_content},
                ],
                temperature=0.1,
                max_tokens=150,
            )
            # FIX #15 — usa regex para extrair o JSON de forma robusta
            # Cobre variações como ```json, ``` json, ``` ou sem fences
            import re as _re
            json_match = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if not json_match:
                log.warning(f"[Vision Triage] Sem JSON válido na resposta: {raw[:100]}")
                return fallback
            result = _json.loads(json_match.group())
            if "categoria" not in result:
                return fallback
            if result["categoria"] not in TRIAGE_CATEGORIES:
                result["categoria"] = "outro"
            return result
        except Exception as e:
            log.warning(f"[Vision Triage] Falha na triagem: {e}")
            return fallback

    async def _call_groq_vision(self, messages: list) -> str:
        """Chamada com visão (imagens). Usa modelo diferente."""
        return await self.pool.complete(
            model=MODEL_VISION,
            messages=messages,
            temperature=0.7,
            max_tokens=600,
        )

    async def _build_vision_content(self, user_text: str, attachments: list) -> list:
        """
        Monta o campo 'content' da mensagem do usuário para o modelo Vision.
        Usa URLs diretas do CDN do Discord — o Groq Vision acessa diretamente.
        NÃO usa base64 inline (causa erro 400 no Groq).
        """
        content = []

        # Texto do usuário
        texto = user_text.strip() if user_text.strip() else "Analise esta imagem e descreva o problema que você vê."
        content.append({"type": "text", "text": texto})

        # Imagens — URL direta do CDN do Discord
        added = 0
        for att in attachments:
            ct = (att.content_type or "").lower()
            if not any(ct.startswith(t) for t in SUPPORTED_IMAGE_TYPES):
                continue

            # Groq Vision aceita URLs públicas diretamente — sem download, sem base64
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": att.url   # URL direta do Discord CDN
                }
            })
            added += 1
            log.debug(f"[Vision] Imagem adicionada via URL: {att.filename} ({ct})")

        if added == 0:
            log.warning("[Vision] Nenhuma imagem válida encontrada nos anexos.")

        return content

    async def _ensure_http_session(self):
        """FIX #16 — recria a sessão aiohttp se tiver fechado inesperadamente."""
        if self._http_session is None or self._http_session.closed:
            log.warning("[AISupport] Sessão aiohttp fechada inesperadamente — recriando.")
            self._http_session = aiohttp.ClientSession()

    @commands.hybrid_command(name="sessoes", description="Mostra sessões de IA ativas no momento (Apenas Dono).")
    async def sessoes(self, ctx: commands.Context):
        """FIX #18 — comando de diagnóstico para ver sessões ativas sem acessar o banco."""
        from utils.cache import OWNER_ID
        if str(ctx.author.id) != str(OWNER_ID):
            return await ctx.send("❌ Apenas o dono pode ver as sessões.", ephemeral=True)

        now = time.time()
        active = self.ai_last_activity
        if not active:
            return await ctx.send("ℹ️ Nenhuma sessão de IA ativa no momento.", ephemeral=True)

        lines = []
        for uid, ts in sorted(active.items(), key=lambda x: -x[1]):
            idle_min = int((now - ts) / 60)
            cat  = self.ai_session_category.get(uid, "—")
            user = self.bot.get_user(uid)
            name = str(user) if user else f"ID:{uid}"
            lines.append(f"• **{name}** — {cat} — inativo há {idle_min}min")

        embed = discord.Embed(
            title=f"🧠 Sessões de IA Ativas ({len(active)})",
            description="\n".join(lines[:20]),
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"Timeout: {AI_SESSION_TIMEOUT_HOURS}h | Processando agora: {len(self._processing)}")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="limparhistorico", description="Limpa seu histórico de conversa com a IA.")
    async def limparhistorico(self, ctx: commands.Context):
        clear_ai_history(ctx.author.id)
        self.ai_last_activity.pop(ctx.author.id, None)   # FIX #7
        self.ai_session_category.pop(ctx.author.id, None) # FIX #7
        await ctx.send("🧹 Histórico limpo com sucesso!", ephemeral=True)

    @commands.hybrid_command(name="iaqualidade", description="Mostra estatísticas de qualidade das respostas da IA (Apenas Dono).")
    async def iaqualidade(self, ctx: commands.Context):
        from utils.cache import OWNER_ID
        if str(ctx.author.id) != str(OWNER_ID):
            return await ctx.send("❌ Apenas o dono pode ver estas estatísticas.", ephemeral=True)
        stats      = get_ai_quality_stats(ctx.guild.id)
        emoji_rate = "🟢" if stats["rate"] >= 70 else ("🟡" if stats["rate"] >= 40 else "🔴")
        embed = discord.Embed(title="📊 Qualidade da IA — Darke Store", color=discord.Color.blurple())
        embed.add_field(name="📩 Total de Feedbacks",           value=str(stats["total"]),      inline=True)
        embed.add_field(name="✅ Resolvidos",                    value=str(stats["resolved"]),   inline=True)
        embed.add_field(name="❌ Não Resolvidos",                value=str(stats["unresolved"]), inline=True)
        embed.add_field(name=f"{emoji_rate} Taxa de Resolução", value=f"**{stats['rate']}%**",  inline=False)
        embed.add_field(
            name="📅 Período",
            value=f"Últimos 7 dias: **{stats['resolved_7d']}** resolvidos / **{stats['total_7d']}** total\nTudo: **{stats['resolved']}** / **{stats['total']}**",
            inline=False
        )
        cats = "\n".join(f"• {c['name']}: **{c['count']}**" for c in stats["categories"][:8]) if stats["categories"] else "Sem dados ainda."
        embed.add_field(name="📁 Por Categoria", value=cats, inline=False)
        await ctx.send(embed=embed)

    # ── Listener principal ────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if not SUPPORT_CHANNEL_ID or message.channel.id != SUPPORT_CHANNEL_ID:
            return

        u_id = message.author.id
        now  = time.time()

        # FIX #14 — deduplicação: ignora mensagem idêntica enviada em menos de 3s
        _last_content = getattr(self, "_last_msg_content", {})
        _msg_key = f"{u_id}:{message.content.strip()}"
        if (now - _last_content.get(_msg_key, 0)) < 3.0:
            log.debug(f"[AISupport] Mensagem duplicada ignorada para user {u_id}")
            return
        _last_content[_msg_key] = now
        # Limita o tamanho do cache de deduplicação (max 500 entradas)
        if len(_last_content) > 500:
            oldest = sorted(_last_content, key=lambda k: _last_content[k])[:100]
            for k in oldest:
                del _last_content[k]
        self._last_msg_content = _last_content

        # Cooldown
        last = ai_cooldown_cache.get(u_id, 0)
        remaining = config["ai_cooldown"] - (now - last)
        if remaining > 0:
            try:
                await message.delete()
                await message.author.send(
                    f"⚠️ Aguarde mais **{remaining:.0f}s** antes de enviar outra mensagem no suporte."
                )
            except discord.Forbidden:
                log.debug(f"[AISupport] FIX #20 — DM bloqueada para user {u_id} (cooldown aviso)")
            except Exception:
                pass
            return

        user_text = message.content.strip()

        # ── Modo manutenção ───────────────────────────────────────────────────
        try:
            from cogs.ai_tools import maintenance_mode
            if maintenance_mode:
                return
        except ImportError:
            pass

        # FIX #11 — rate-limit por guild: max 10 chamadas Groq simultâneas por servidor
        guild_sem = self._guild_semaphores.setdefault(message.guild.id, asyncio.Semaphore(10))

        # Detecta imagens válidas nos anexos
        image_attachments = [
            att for att in message.attachments
            if any((att.content_type or "").lower().startswith(t) for t in SUPPORTED_IMAGE_TYPES)
        ]
        has_image = len(image_attachments) > 0

        # Sem texto E sem imagem → ignora
        if not has_image and (len(user_text) < 2 or NONSENSE_PATTERN.match(user_text)):
            return

        if has_image and not user_text:
            user_text = ""

        # Rate limit por usuário
        if u_id in self._processing:
            return
        self._processing.add(u_id)

        try:
            ai_cooldown_cache[u_id] = now
            self.ai_last_activity[u_id] = now   # FIX #7
            try:
                log_command(message.guild.id, u_id, "ai_support")
            except Exception:
                pass

            # ── Frustração ────────────────────────────────────────────────────
            is_frustrated = detect_frustration(user_text)
            if is_frustrated:
                embed_frust = discord.Embed(
                    description=(
                        "Entendo que você está frustrado — vamos resolver isso.\n\n"
                        "Para atendimento imediato da nossa equipe:\n"
                        f"🎫 **[Abrir um Ticket]({TICKET_URL})**\n\n"
                        "_Responderei sua pergunta enquanto isso..._"
                    ),
                    color=discord.Color.orange()
                )
                await message.reply(embed=embed_frust, mention_author=True)

            # ── Categoria e idioma ────────────────────────────────────────────
            category = detect_category(user_text)
            if not category and u_id in self.ai_session_category:  # FIX #7
                category = self.ai_session_category[u_id]
            elif category:
                self.ai_session_category[u_id] = category  # FIX #7

            lang          = detect_language(user_text)
            system_prompt = self.system_prompt_en if lang == "en" else self.system_prompt_pt

            if category:
                system_prompt += (
                    f"\n\n[TRIAGEM AUTOMÁTICA] Dúvida sobre: **{category}**. Priorize esse contexto."
                )
            if is_frustrated:
                system_prompt += (
                    "\n[ATENÇÃO] Usuário demonstrou FRUSTRAÇÃO. Seja empático, direto, mencione o ticket."
                )
            if has_image:
                system_prompt += (
                    "\n[IMAGEM] O usuário enviou uma imagem junto à mensagem. "
                    "Analise visualmente o conteúdo da imagem para entender o problema "
                    "(pode ser uma tela de erro, print de credencial, etc.) e responda com base no que vê."
                )

            # ── Histórico + FIX #1 (limite de tokens) ────────────────────────
            raw_history = get_ai_history(u_id)[-8:]  # pega um pouco mais para o trim decidir
            history = self._trim_history_by_tokens(raw_history)

            # Feedback imediato para imagens
            analyzing_msg = None
            if has_image:
                try:
                    analyzing_embed = discord.Embed(
                        description="🔍 Analisando sua imagem...",
                        color=discord.Color.blurple()
                    )
                    try:
                        analyzing_msg = await message.reply(embed=analyzing_embed, mention_author=False)
                    except discord.HTTPException:
                        analyzing_msg = await message.channel.send(embed=analyzing_embed)
                except Exception:
                    pass

            async with message.channel.typing():
                # FIX #11 — adquire semáforo de guild antes de chamar a IA
                async with guild_sem:
                    try:
                        triage_result = None

                        if has_image:
                            vision_content = await self._build_vision_content(user_text, image_attachments)
                            if len(vision_content) == 1:
                                log.warning("[Vision] Nenhuma imagem carregada, usando fallback texto.")
                                messages_to_send = [{"role": "system", "content": system_prompt}] + history
                                messages_to_send.append({"role": "user", "content": user_text})
                                reply = await self._call_groq_text(messages_to_send)
                            else:
                                # FIX #4 — triage e resposta em chamada paralela (asyncio.gather)
                                # Reduz latência total de 2x para ~1x (execução simultânea)
                                triage_task  = asyncio.create_task(self._triage_screenshot(vision_content))
                                vision_msgs  = [
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user",   "content": vision_content},
                                ]
                                reply_task   = asyncio.create_task(self._call_groq_vision(vision_msgs))
                                triage_result, reply = await asyncio.gather(triage_task, reply_task)
                                log.debug(f"[Vision Triage] Resultado: {triage_result}")
                        else:
                            messages_to_send = [{"role": "system", "content": system_prompt}] + history
                            messages_to_send.append({"role": "user", "content": user_text})
                            reply = await self._call_groq_text(messages_to_send)

                        safe_reply = self.sanitize(reply)

                        # FIX #8 — threshold mínimo: classificador semântico só roda para
                        # mensagens com 8+ palavras (evita custo extra em queries simples)
                        _word_count = len(user_text.split())
                        if _word_count < 8 and not has_image and not is_frustrated:
                            # Usa heurística rápida sem chamar o classificador
                            if SUPPORT_KEYWORDS.search(user_text):
                                decision = "show_buttons"
                            elif GREETING_PATTERN.match(user_text) or THANKS_PATTERN.match(user_text):
                                decision = "no_buttons"
                            else:
                                decision = "no_buttons"
                        else:
                            decision = await decide_buttons(
                                user_text, safe_reply, self.pool, is_frustrated, has_image=has_image
                            )

                        if decision == "no_reply":
                            return

                        history_user_text = user_text if user_text.strip() else "[Usuário enviou uma imagem]"
                        append_ai_history(u_id, "user",      history_user_text)
                        append_ai_history(u_id, "assistant", safe_reply)

                        view = SupportView(u_id, message.guild.id, message.channel.id, category, cog=self) \
                               if decision == "show_buttons" else None

                        # ── Badge de triagem (imagens) ─────────────────────────
                        if has_image and triage_result:
                            cat_key  = triage_result.get("categoria", "outro")
                            cat_name, cat_color = TRIAGE_CATEGORIES.get(cat_key, TRIAGE_CATEGORIES["outro"])
                            urgencia = triage_result.get("urgencia", "media")
                            problema = triage_result.get("problema_resumido", "")
                            codigo   = triage_result.get("codigo_erro")
                            urgencia_icon = {"alta": "🔴", "media": "🟡", "baixa": "🟢"}.get(urgencia, "🟡")

                            triage_embed = discord.Embed(
                                title=f"{cat_name}",
                                description=f"_{problema}_" if problema else None,
                                color=cat_color
                            )
                            if codigo:
                                triage_embed.add_field(name="🆔 Código Detectado", value=f"`{codigo}`", inline=True)
                            triage_embed.set_footer(
                                text=f"Urgência: {urgencia_icon} {urgencia.capitalize()} • Análise automática de screenshot"
                            )
                            try:
                                await message.reply(embed=triage_embed, mention_author=True)
                            except discord.HTTPException:
                                await message.channel.send(message.author.mention, embed=triage_embed)

                        embed = build_embed_response(safe_reply, category, is_frustrated)

                        if analyzing_msg:
                            try:
                                await analyzing_msg.delete()
                            except Exception:
                                pass

                        async def safe_send(channel, **kwargs):
                            try:
                                return await message.reply(**kwargs)
                            except discord.HTTPException as _e:
                                if _e.code == 50035:
                                    kwargs.pop("mention_author", None)
                                    return await channel.send(
                                        content=f"{message.author.mention} " + (kwargs.pop("content", "") or ""),
                                        **kwargs
                                    )
                                raise

                        if embed:
                            sent = await safe_send(message.channel, embed=embed, view=view)
                        else:
                            # FIX #9 — usa _send_parts com delay anti rate-limit
                            parts = self.split_message(safe_reply)
                            sent  = await self._send_parts(message.channel, parts, safe_send, view=view)

                        if view and hasattr(view, "message"):
                            view.message = sent

                    except RuntimeError:
                        if analyzing_msg:
                            try:
                                await analyzing_msg.delete()
                            except Exception:
                                pass
                        try:
                            await message.reply(
                                "⚠️ Nosso serviço de IA está temporariamente indisponível. Tente novamente em alguns instantes.",
                                delete_after=15
                            )
                        except discord.HTTPException:
                            await message.channel.send(
                                f"{message.author.mention} ⚠️ Nosso serviço de IA está temporariamente indisponível.",
                                delete_after=15
                            )
                    except Exception as e:
                        if analyzing_msg:
                            try:
                                await analyzing_msg.delete()
                            except Exception:
                                pass
                        log.error(f"Erro inesperado na IA: {e}")
                        try:
                            await message.reply("⚠️ Ocorreu um erro interno. Tente novamente.", delete_after=10)
                        except discord.HTTPException:
                            await message.channel.send(
                                f"{message.author.mention} ⚠️ Ocorreu um erro interno. Tente novamente.",
                                delete_after=10
                            )

        finally:
            self._processing.discard(u_id)


async def setup(bot):
    await bot.add_cog(AISupport(bot))
