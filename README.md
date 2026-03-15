# Darke Store Bot v12

Bot Discord de suporte inteligente para a Darke Store — com IA, moderação, backup automático, analytics e mais.

---

## Requisitos

- Python 3.10 ou superior
- Conta no [Discord Developer Portal](https://discord.com/developers/applications)
- Chave de API Groq gratuita em [console.groq.com](https://console.groq.com)

---

## Instalação

```bash
# 1. Clone ou extraia o projeto
cd darke-store-bot

# 2. Crie um ambiente virtual (recomendado)
python -m venv venv
source venv/bin/activate      # Linux/Mac
venv\Scripts\activate         # Windows

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Configure as variáveis de ambiente
cp .env.example .env
# Edite o .env com seus valores reais

# 5. Inicie o bot
python bot.py
```

---

## Configuração do `.env`

| Variável | Obrigatória | Descrição |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Token do bot no Discord Developer Portal |
| `OWNER_ID` | ✅ | Seu ID de usuário no Discord |
| `SUPPORT_CHANNEL_ID` | ✅ | Canal onde a IA responde automaticamente |
| `LOG_CHANNEL_ID` | ✅ | Canal de logs de moderação |
| `TICKET_URL` | ✅ | Link para o canal/categoria de tickets |
| `GROQ_API_KEY` | ✅ | Chave da API Groq para a IA |
| `GROQ_API_KEY2`, `GROQ_API_KEY3` | ❌ | Chaves adicionais para rotação automática |

---

## Personalização da IA

Edite o arquivo `prompt.txt` para customizar o comportamento da IA de suporte.

Após editar, aplique sem reiniciar o bot:
```
.reloadprompt
```

---

## Comandos principais

### 🤖 IA e Suporte
| Comando | Descrição |
|---|---|
| `.reloadprompt` | Recarrega o `prompt.txt` sem reiniciar |
| `.manutencao on/off` | Pausa/retoma a IA com aviso automático no canal |
| `.statusia` | Mostra se a IA está ativa ou em manutenção |
| `.faqsugestao [dias]` | IA analisa perguntas e sugere melhorias pro prompt |
| `.faqhistorico` | Histórico de sugestões de FAQ geradas |
| `.limparhistorico` | Limpa histórico de conversa com a IA |
| `.iaqualidade` | Estatísticas de qualidade das respostas |

### 🛡️ Moderação
| Comando | Descrição |
|---|---|
| `.ban @user [motivo]` | Bane um membro |
| `.kick @user [motivo]` | Expulsa um membro |
| `.mute @user [minutos] [motivo]` | Silencia um membro |
| `.unmute @user` | Remove o silenciamento |
| `.warn @user [motivo]` | Adiciona aviso ao membro |
| `.warns @user` | Lista avisos do membro |
| `.clearwarns @user` | Remove todos os avisos |
| `.modlog @user` | Histórico de ações do membro |

### 💾 Backup
| Comando | Descrição |
|---|---|
| `.backup` | Cria backup manual do servidor |
| `.listarbackups` | Lista backups disponíveis |
| `.restaurar [número]` | Restaura um backup |
| `.backupinfo` | Histórico de backups no banco |

### 📊 Analytics
| Comando | Descrição |
|---|---|
| `.stats` | Resumo geral de atividade |
| `.statsmod` | Estatísticas de moderação |
| `.statsai` | Estatísticas da IA |
| `.statsmembros` | Entradas e saídas de membros |
| `.statscomandos` | Comandos mais usados |

### ⚙️ Sistema (apenas dono)
| Comando | Descrição |
|---|---|
| `.healthcheck` | Status completo do sistema |
| `.ping` | Latência WebSocket e API |
| `.cogstatus` | Status de cada módulo |
| `.reloadall` | Recarrega todos os módulos |
| `.poolstatus` | Status das chaves Groq |
| `.alertas on/off` | Ativa/desativa alertas automáticos |

---

## Estrutura de arquivos

```
darke-store-bot/
├── bot.py              # Ponto de entrada
├── prompt.txt          # Instruções da IA de suporte
├── requirements.txt    # Dependências Python
├── .env                # Configurações (não commitar!)
├── .env.example        # Modelo de configuração
├── cogs/               # Módulos do bot
│   ├── ai_support.py   # IA de suporte (núcleo)
│   ├── ai_tools.py     # Ferramentas de gestão da IA
│   ├── moderation.py   # Comandos de moderação
│   ├── backup.py       # Backup automático e manual
│   ├── analytics.py    # Estatísticas
│   ├── health_check.py # Monitoramento do sistema
│   ├── alerts.py       # Alertas automáticos para o dono
│   ├── ux.py           # Help interativo e interface
│   └── ...             # Outros módulos
├── utils/
│   ├── database.py     # Banco de dados SQLite
│   ├── groq_pool.py    # Pool de chaves Groq
│   ├── cache.py        # Cache e variáveis de ambiente
│   └── logger.py       # Sistema de logs
└── backups/            # Backups do servidor (gerado automaticamente)
```

---

## Notas de produção

- O banco de dados (`darke_store.db`) é criado automaticamente na primeira execução
- Backups ficam em `./backups/` — faça backup desta pasta regularmente
- Logs ficam em `./logs/bot.log`
- O histórico de IA é purgado automaticamente todo dia à meia-noite (mensagens >30 dias)
- Em caso de chave Groq inválida (401), o dono recebe DM automática com instruções

---

## Suporte

Problemas com o bot? Use `.healthcheck` para diagnóstico completo ou verifique os logs em `./logs/bot.log`.


## Variáveis de ambiente opcionais

```env
# Modelos da Groq (opcional — usa padrão se não definido)
GROQ_MODEL_TEXT=llama-3.1-8b-instant
GROQ_MODEL_VISION=meta-llama/llama-4-scout-17b-16e-instruct
```
