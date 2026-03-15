"""
utils/groq_pool.py  ─  v9
──────────────────────────
Pool de chaves Groq com:
  ✅ Rotação automática em rate-limit (429)
  ✅ Alerta ao dono em chave inválida (401/403) — não silencia mais
  ✅ Marcação de chave como "morta" para não tentar novamente
  ✅ .poolstatus mostra chaves mortas
"""
import asyncio
import os
import time
from utils.logger import log

try:
    from groq import AsyncGroq, AuthenticationError
    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False
    AuthenticationError = Exception  # fallback


class _KeyState:
    def __init__(self, key: str, index: int):
        self.key       = f"KEY_{index+1}"   # nunca loga a chave real
        self.client    = AsyncGroq(api_key=key) if _GROQ_AVAILABLE else None
        self.blocked_until: float = 0.0
        self.tokens_today: int    = 0
        self.dead: bool           = False    # 401/403 — chave inválida
        self._reset_day: str      = self._today()

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _maybe_reset_daily(self):
        """Reseta o contador de tokens se já é um novo dia (UTC)."""
        today = self._today()
        if today != self._reset_day:
            self.tokens_today = 0
            self._reset_day   = today

    def mark_blocked(self, seconds: int = 60):
        self.blocked_until = time.time() + seconds

    def is_available(self) -> bool:
        if self.dead:
            return False
        if time.time() < self.blocked_until:
            return False
        return True

    def status_dict(self) -> dict:
        if self.dead:
            return {"key": self.key, "status": "❌ inválida (401/403)", "wait": 0, "tokens": 0, "budget_pct": 0}
        wait = max(0, int(self.blocked_until - time.time()))
        if wait > 0:
            return {"key": self.key, "status": f"⏳ bloqueada", "wait": wait, "tokens": self.tokens_today, "budget_pct": round(self.tokens_today / 500_000 * 100)}
        return {"key": self.key, "status": "disponível", "wait": 0, "tokens": self.tokens_today, "budget_pct": round(self.tokens_today / 500_000 * 100)}


class GroqPool:
    def __init__(self, keys: list[str]):
        if not _GROQ_AVAILABLE:
            raise RuntimeError("Biblioteca 'groq' não instalada. Execute: pip install groq")
        if not keys:
            raise RuntimeError("Nenhuma GROQ_API_KEY configurada no .env")

        self._states   = [_KeyState(k, i) for i, k in enumerate(keys)]
        self._bot      = None   # injetado após bot.setup_hook pelo bot.py
        self._rr_index = 0      # Round-robin: próxima chave a tentar

    @property
    def key_count(self) -> int:
        return len(self._states)

    def set_bot(self, bot):
        """Injeta referência ao bot para enviar alertas ao dono."""
        self._bot = bot

    def _pick(self) -> "_KeyState | None":
        """Escolhe a próxima chave disponível via round-robin.
        Distribui carga entre todas as chaves, evitando sobrecarregar a primeira."""
        n = len(self._states)
        for _ in range(n):
            state = self._states[self._rr_index % n]
            self._rr_index = (self._rr_index + 1) % n
            if state.is_available():
                return state
        return None

    async def _alert_owner(self, message: str):
        """Envia DM ao dono sobre problema crítico com chave."""
        from utils.cache import OWNER_ID
        if not self._bot or not OWNER_ID:
            return
        try:
            owner = await self._bot.fetch_user(int(OWNER_ID))
            await owner.send(f"🚨 **Alerta Groq Pool**\n{message}")
        except Exception as e:
            log.warning(f"[GroqPool] Não foi possível alertar o dono: {e}")

    async def complete(self, model: str, messages: list, **kwargs) -> str:
        attempts = len(self._states)
        last_err = None

        for _ in range(attempts):
            state = self._pick()
            if state is None:
                break

            try:
                resp = await state.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **kwargs,
                )
                content = resp.choices[0].message.content

                # Reseta contador diário se necessário, depois rastreia tokens
                state._maybe_reset_daily()
                try:
                    state.tokens_today += resp.usage.total_tokens
                except (AttributeError, TypeError):
                    state.tokens_today += len(content) // 4  # fallback por caracteres

                return content

            except Exception as e:
                err_str = str(e)
                err_low = err_str.lower()
                last_err = e

                # 429 — rate limit: bloqueia por 60s e tenta outra chave
                if "429" in err_str or "rate" in err_low:
                    log.warning(f"[GroqPool] {state.key}: rate-limit, bloqueando 60s")
                    state.mark_blocked(60)
                    continue

                # 401/403 — chave inválida: marca como morta e alerta dono
                if "401" in err_str or "403" in err_str or "authentication" in err_low or "invalid api key" in err_low:
                    if not state.dead:
                        state.dead = True
                        log.error(f"[GroqPool] {state.key}: chave INVÁLIDA (401/403) — marcada como morta.")
                        asyncio.create_task(self._alert_owner(
                            f"A **{state.key}** da Groq está **inválida** (erro 401/403).\n"
                            "Acesse https://console.groq.com e atualize a chave no `.env`, "
                            "depois use `.reload cogs.ai_support` para recarregar."
                        ))
                    continue

                # Outros erros — propaga imediatamente
                raise

        if last_err:
            # Verifica se todas as chaves estão mortas
            dead_count = sum(1 for s in self._states if s.dead)
            if dead_count == len(self._states):
                asyncio.create_task(self._alert_owner(
                    "🚨 **CRÍTICO:** Todas as chaves da Groq estão inválidas!\n"
                    "A IA está completamente offline. Atualize as chaves no `.env` urgentemente."
                ))
            raise RuntimeError(f"Todas as chaves Groq indisponíveis. Último erro: {last_err}")

        raise RuntimeError("Todas as chaves Groq atingiram o rate-limit ou estão mortas.")

    def status(self) -> list[dict]:
        return [s.status_dict() for s in self._states]


_pool_instance: GroqPool | None = None


def get_pool() -> GroqPool:
    global _pool_instance
    if _pool_instance is None:
        from utils.cache import GROQ_API_KEYS
        if not GROQ_API_KEYS:
            single = os.getenv("GROQ_API_KEY", "")
            if single:
                GROQ_API_KEYS.append(single)
        _pool_instance = GroqPool(GROQ_API_KEYS)
    return _pool_instance
