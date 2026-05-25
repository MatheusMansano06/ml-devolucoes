# 📱 Resumo Rápido - Módulo de Devoluções

## O que é?

Um subsistema integrado que **gerencia devoluções de produtos** em Mercado Livre, Shopee e outros canais de forma centralizada.

---

## 🏗️ Arquitetura em 30 segundos

```
┌─ Flask App (port 5000) ──────────┐
│  seller-hub/app.py               │
│                                  │
│  → /devolucoes (rota)            │
│  → Renderiza HTML                │
│  → Mostra iframe com ferramenta  │
└──────────────────────────────────┘
         ↓
┌─ Python Manager ─────────────────┐
│  src/devolucoes_app.py           │
│                                  │
│  ✓ Inicia backend (npm start)    │
│  ✓ Inicia frontend (npm run dev) │
│  ✓ Monitora saúde (health checks)│
│  ✓ Coleta logs                   │
└──────────────────────────────────┘
         ↙              ↘
    [Node.js]      [React/Vite]
   Backend API      Frontend UI
   Port 3001        Port 5173
```

---

## 🎯 Onde está cada coisa?

| O quê | Onde | Arquivo |
|-------|------|---------|
| Gerenciador (Python) | seller-hub | `src/devolucoes_app.py` |
| Interface HTML | seller-hub | `templates/devolucoes.html` |
| Rotas Flask | seller-hub | `app.py` (linhas 133-160) |
| Backend API | Fora | `projeto-devolucoes/backend/` |
| UI React | Fora | `projeto-devolucoes/frontend/` |

---

## 🚀 Como começar a trabalhar

### 1️⃣ Teste local
```bash
python app.py
# Acesse http://127.0.0.1:5000/login
# PIN: 1234
# Clique: Gestão de Devoluções
```

### 2️⃣ Melhorar Frontend
```bash
cd projeto-devolucoes/frontend
code .  # Abrir VSCode

# Edite arquivos em src/
# Salve e recarregue navegador (hot reload automático)
```

### 3️⃣ Melhorar Backend
```bash
cd projeto-devolucoes/backend
code .  # Abrir VSCode

# Edite arquivos em src/routes/
# Pare e reinicie: npm start
```

---

## 📊 O que você VÊ quando acessa /devolucoes

```
╔════════════════════════════════════════════════════╗
║ 🔄 Gestão de Devoluções                          ║
║ Multiplataforma                                    ║
║ Mercado Livre, Shopee e demais canais             ║
╠════════════════════════════════════════════════════╣
║                                                    ║
║ [Backend: ✅ OK]  [Frontend: ✅ OK]               ║
║                                                    ║
║ [Autorizar ML] [Abrir ferramenta] [Parar]        ║
║                                                    ║
╠════════════════════════════════════════════════════╣
║  ┌─ FERRAMENTA ──────────────────────────────────┐║
║  │ (iframe mostrando React app)                  │║
║  │                                               │║
║  │ Devoluções pendentes:                         │║
║  │ ┌─ Produto: Pneu traseiro                    ││
║  │ │ Motivo: Chegou furado                       ││
║  │ │ Cliente: João da Silva                      ││
║  │ │ [✅ Aceitar] [❌ Rejeitar]                 ││
║  │ │                                            ││
║  │ ├─ Produto: Correia                         ││
║  │ │ Motivo: Não encaixa                        ││
║  │ │ Cliente: Maria Santos                      ││
║  │ │ [✅ Aceitar] [❌ Rejeitar]                 ││
║  │ └─                                            ││
║  │                                               │║
║  └───────────────────────────────────────────────┘║
║                                                    ║
╠════════════════════════════════════════════════════╣
║ Logs:                                              ║
║ backend: iniciado                                 ║
║ backend: port 3001 listening                      ║
║ frontend: ✅ built successfully                  ║
╚════════════════════════════════════════════════════╝
```

---

## 🔄 Fluxo de uma Devolução

```
1. Cliente solicita devolução no Mercado Livre/Shopee
   ↓
2. Backend sincroniza (GET /devolutions)
   ↓
3. Frontend mostra na lista
   ↓
4. Usuário clica "Aceitar"
   ↓
5. POST /api/devolutions/:id/accept
   ↓
6. Backend atualiza BD
   ↓
7. Backend atualiza no Mercado Livre/Shopee (API)
   ↓
8. Status muda de "Pendente" para "Aceita"
```

---

## 💻 Stack Tecnológico

| Camada | Tecnologia | Port |
|--------|-----------|------|
| Interface Web | Flask | 5000 |
| Manager | Python + subprocess | - |
| Backend API | Node.js + Express | 3001 |
| Frontend UI | React + Vite | 5173 |

---

## 🎯 Tarefas Típicas

### ✏️ Melhorar UI
```bash
# Arquivo: projeto-devolucoes/frontend/src/components/DevolutionsList.jsx
# Mude cor, adicione coluna, mude layout
# Salve → Recarregue browser (hot reload)
```

### 🔌 Adicionar endpoint
```bash
# Arquivo: projeto-devolucoes/backend/src/routes/stats.js
# Crie novo arquivo com:
# router.get('/devolutions', async (req, res) => { ... })
# Registre em src/index.js
# Restart: npm start
```

### 🐛 Depurar erro
```bash
# Abra DevTools (F12)
# Console → veja erros
# Network → veja requests/responses
# Procure em código (ctrl+shift+f)
```

---

## 🛑 Problemas Rápidos

**Botão não funciona?**
- Abra DevTools (F12) → Console
- Procure erro vermelho
- Procure função no código (ctrl+shift+f)

**Backend respondendo mas Frontend não?**
- Espere 5-10s (Vite compilando)
- Recarregue página
- Procure erro em Logs da página

**Mudança no código não aparece?**
- Frontend: salve arquivo, recarregue browser
- Backend: pare (ctrl+c) e restart (`npm start`)

---

## 📖 Documentos de Referência

| Documento | Para quem | Lê em |
|-----------|----------|-------|
| `ENTENDER_DEVOLUCOES.md` | Entender a arquitetura | 20 min |
| `TRABALHAR_DEVOLUCOES.md` | Trabalhar no código | 30 min |
| `DEVOLUCOES_RESUMO_RAPIDO.md` | Refer rápido | 3 min |

---

## ✅ Checklist para começar

- [ ] Node.js instalado
- [ ] Seller Hub rodando
- [ ] Conseguir acessar /devolucoes
- [ ] Backend + Frontend iniciando
- [ ] VSCode aberto na pasta correta
- [ ] Ler `TRABALHAR_DEVOLUCOES.md` (Cenário 1)

---

## 🚀 Primeiro Teste

```bash
# 1. Inicia Seller Hub
python app.py

# 2. Acessa no browser
http://127.0.0.1:5000/login
# PIN: 1234

# 3. Clica em Gestão de Devoluções
# 4. Clica em "Abrir ferramenta"
# 5. Espera 5-10s
# 6. Ve interface carregando
```

Pronto! Você está testando o módulo de devoluções! 🎉

---

**Próximo passo?** Leia `TRABALHAR_DEVOLUCOES.md` para aprender como fazer mudanças!
