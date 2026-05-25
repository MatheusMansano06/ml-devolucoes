# 🔍 Entendendo o Módulo de Devoluções

## 📋 Visão Geral

O módulo de **Gestão de Devoluções** é um subsistema integrado ao Seller Hub que gerencia devoluções de produtos em múltiplas plataformas (Mercado Livre, Shopee, etc.) de forma centralizada.

### Componentes principais:
- **Backend**: Node.js/Express (porta 3001)
- **Frontend**: React/Vite (porta 5173)
- **Manager (Python)**: `devolucoes_app.py` - gerencia ciclo de vida
- **Interface (HTML)**: `devolucoes.html` - iframe para exibição
- **Rotas Flask**: em `app.py` - integração com web

---

## 🏗️ Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│                    SELLER HUB (Flask)                           │
│                      app.py                                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                    Rota: /devolucoes
                              ↓
        ┌─────────────────────────────────────────────────┐
        │     devolucoes.html (Template Flask)            │
        │                                                   │
        │  - Status Backend (OK/Erro)                      │
        │  - Status Frontend (OK/Erro)                     │
        │  - Botões (Abrir, Parar)                         │
        │  - Logs de execução                              │
        │                                                   │
        │  <iframe src="/devolucoes-app/">                 │
        │    └─ Exibe frontend da ferramenta               │
        │  </iframe>                                        │
        └─────────────────────────────────────────────────┘
                              ↓
        ┌─────────────────────────────────────────────────┐
        │    devolucoes_app.py (DevolucoesAppRunner)      │
        │                                                   │
        │  - Gerencia processos (start/stop)               │
        │  - Monitora saúde (health checks)                │
        │  - Coleta logs em tempo real                     │
        └─────────────────────────────────────────────────┘
                      ↙              ↘
          ┌──────────────────┐  ┌──────────────────┐
          │  Backend Node.js  │  │ Frontend React   │
          │  (npm start)      │  │ (npm run dev)    │
          │  Porta 3001       │  │ Porta 5173       │
          │                   │  │                  │
          │ - API REST        │  │ - UI Devoluções  │
          │ - Lógica negócio  │  │ - Integração ML  │
          │ - BD devoluções   │  │ - Integração SH  │
          └──────────────────┘  └──────────────────┘
```

---

## 📂 Arquivos e Responsabilidades

### `src/devolucoes_app.py` (Gerenciador Python)

**O que faz**:
- Inicia/para processos backend e frontend como subprocessos Python
- Monitora se estão rodando (health checks)
- Coleta logs em tempo real de ambos processos
- Oferece snapshot do estado atual

**Classe principal**: `DevolucoesAppRunner`

```python
class DevolucoesAppRunner:
    def __init__(self, project_dir: Path):
        # Diretório do projeto (projeto-devolucoes)
        self.project_dir = project_dir
        self.backend_process = None      # Processo Node backend
        self.frontend_process = None     # Processo Vite frontend
        self.logs = deque(maxlen=400)    # Últimos 400 logs
        self.error = ""                  # Mensagem de erro

    def start(self) -> None:
        # Inicia backend: npm start (na pasta backend/)
        # Inicia frontend: npm run dev -- --host 127.0.0.1 --port 5173
        # (com --base /devolucoes-app/ para rodar sob path)

    def stop(self) -> None:
        # Para frontend e backend gracefully

    def snapshot(self) -> DevolucoesSnapshot:
        # Retorna estado atual:
        # - running: se algo está rodando
        # - backend_ok: se /health responde
        # - frontend_ok: se porta 5173 responde
        # - logs: últimas 400 linhas
        # - error: mensagem de erro
```

**Configurações via .env**:
```env
DEVOLUCOES_PROJECT_DIR=C:\Users\mansa\OneDrive\Área de Trabalho\projeto-devolucoes
DEVOLUCOES_BACKEND_PORT=3001
DEVOLUCOES_FRONTEND_PORT=5173
```

---

### `templates/devolucoes.html` (Interface Web)

**O que faz**:
- Mostra status dos serviços (backend/frontend)
- Oferece botões para Abrir/Parar ferramenta
- Mostra logs em tempo real
- Incorpora frontend em iframe

**Fluxo**:

```
1. Usuário acessa /devolucoes
   ↓
2. devolucoes_runner.start() é chamado
   ↓
3. HTML mostra:
   - Status Backend/Frontend (vermelho = erro, verde = OK)
   - Mensagem "Ferramenta ainda não iniciada" até frontend responder
   - Botão "Abrir ferramenta" para iniciar manualmente
   - Botão "Parar" para derrubar tudo
   ↓
4. Quando frontend fica OK (porta 5173 responde):
   - <iframe src="http://127.0.0.1:5173/devolucoes-app/" />
   - Mostra a UI React
   ↓
5. Logs aparecem em tempo real (últimas 60 linhas)
```

**Status visual**:

```
┌─ Gestão de Devoluções ───────────────────────┐
│ Operacao integrada                           │
│ Mercado Livre, Shopee e demais canais...    │
│                                              │
│ [Backend: OK] [Frontend: erro]               │
│ [Autorizar ML] [Abrir ferramenta] [Parar]   │
│                                              │
│ ┌─ Ferramenta ainda não iniciada ──────────┐ │
│ │ Clique em "Abrir ferramenta" para        │ │
│ │ iniciar o backend e frontend.            │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ ┌─ Logs ───────────────────────────────────┐ │
│ │ backend: iniciado                         │ │
│ │ backend: conectado no port 3001           │ │
│ │ frontend: iniciado                        │ │
│ │ frontend: VITE v5.0.0 building...        │ │
│ └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

---

### Rotas Flask (em `app.py`)

```python
@app.get("/devolucoes")
def devolucoes():
    # 1. Verifica login (session)
    # 2. Chama devolucoes_runner.start() (inicia backend + frontend)
    # 3. Retorna template com snapshot do estado
    # → Renderiza devolucoes.html
```

```python
@app.post("/devolucoes/start")
def devolucoes_start():
    # Usuário clicou "Abrir ferramenta"
    # 1. Verifica login
    # 2. Chama devolucoes_runner.start() (re-inicia se não estiver)
    # 3. Redireciona para /devolucoes
```

```python
@app.post("/devolucoes/stop")
def devolucoes_stop():
    # Usuário clicou "Parar"
    # 1. Verifica login
    # 2. Chama devolucoes_runner.stop() (encerra processos)
    # 3. Redireciona para /devolucoes
```

```python
@app.get("/devolucoes-app/")
def devolucoes_app_proxy():
    # Proxy: redireciona requests do iframe para http://127.0.0.1:5173
    # (Assim o frontend fica em /devolucoes-app/ mas roda em 5173)
```

---

## 🔄 Fluxo de Execução

### Cenário 1: Usuário acessa /devolucoes pela primeira vez

```
1. Browser: GET /devolucoes
   ↓
2. Flask: Verifica session (autenticado?)
   ↓
3. Flask: Chama devolucoes_runner.start()
   │
   ├─ Inicia: npm start (backend, cwd=projeto-devolucoes/backend/)
   │   └─ Abre porta 3001, processa lógica de devoluções
   │
   ├─ Inicia: npm run dev (frontend, cwd=projeto-devolucoes/frontend/)
   │   └─ Abre porta 5173, carrega Vite + React
   │
   ├─ Coleta logs em threads daemon
   │   └─ Armazena em self.logs (deque de 400)
   │
   └─ Faz health checks:
      ├─ GET http://127.0.0.1:3001/health → OK?
      └─ GET http://127.0.0.1:5173 → OK?
   ↓
4. Flask: Renderiza devolucoes.html com snapshot
   ↓
5. HTML mostra:
   - [Backend: OK] ✅ (respondeu no health check)
   - [Frontend: Aguardando...] ⏳ (Vite ainda compilando)
   - Botão "Abrir ferramenta" disponível
   - Últimos 60 logs
   ↓
6. JavaScript no HTML: auto-refresh a cada 4 segundos se frontend não OK
   ↓
7. Quando Vite termina compilação (~3-5s):
   - Frontend responde na porta 5173
   - [Frontend: OK] ✅
   - <iframe> é renderizado, mostra a UI React
```

### Cenário 2: Usuário clica "Parar"

```
1. Browser: POST /devolucoes/stop
   ↓
2. Flask: Chama devolucoes_runner.stop()
   │
   ├─ Encerra frontend_process.terminate()
   │   └─ Fecha Vite (porta 5173 para de responder)
   │
   └─ Encerra backend_process.terminate()
      └─ Fecha Node (porta 3001 para de responder)
   ↓
3. Flask: Redireciona para /devolucoes
   ↓
4. HTML agora mostra:
   - [Backend: erro] ❌
   - [Frontend: erro] ❌
   - "Ferramenta ainda não iniciada"
   - Botão "Abrir ferramenta" disponível
```

### Cenário 3: Usuário clica "Abrir ferramenta" (já rodando)

```
1. Browser: POST /devolucoes/start
   ↓
2. Flask: Chama devolucoes_runner.start()
   │
   └─ Se já estão rodando: não faz nada (verifica _is_process_running)
   └─ Se caíram: reinicia
   ↓
3. Flask: Redireciona para /devolucoes
   ↓
4. HTML mostra iframe (ou reinicia se caiu)
```

---

## 🎯 O que acontece DENTRO do frontend React

O frontend (que roda em http://127.0.0.1:5173) provavelmente:

```
1. Conecta ao backend (http://127.0.0.1:3001)
   ├─ GET /devolutions → lista devoluções
   ├─ POST /devolutions/:id/accept → aceita devolução
   └─ POST /devolutions/:id/reject → rejeita devolução

2. Integra com APIs (Mercado Livre, Shopee)
   ├─ Via credenciais em .env
   └─ Backend faz as chamadas (seguro, não expõe credenciais)

3. Mostra UI para gerenciar devoluções
   ├─ Lista de devoluções pendentes
   ├─ Detalhes (produto, motivo, cliente)
   ├─ Ações (aceitar, rejeitar, aprovar reembolso)
   └─ Status em tempo real
```

---

## 🔧 Tecnologia por Camada

| Camada | Tecnologia | Porta | Responsabilidade |
|--------|-----------|-------|------------------|
| **Wrapper** | Python | - | Inicia/para processos, coleta logs |
| **Interface** | Flask + Jinja2 | 5000 | Renderiza HTML, gerencia sessão |
| **Frontend** | React + Vite | 5173 | UI para gerenciar devoluções |
| **Backend** | Node.js + Express | 3001 | API, lógica, integração plataformas |

---

## 🐛 Possíveis Problemas e Soluções

### Problema 1: "Ferramenta ainda não iniciada" (travado)

**Causas**:
- Pasta `projeto-devolucoes` não existe ou caminho errado
- `npm` não instalado no sistema
- Porta 5173 já em uso (outro processo usando)
- Vite demorando para compilar (>10s)

**Soluções**:
```env
# Verificar caminho em .env
DEVOLUCOES_PROJECT_DIR=C:\Users\mansa\OneDrive\Área de Trabalho\projeto-devolucoes
# Ou ajustar se tiver em outro lugar
```

```bash
# Verificar se npm existe
npm --version

# Matar processos na porta 5173
netstat -ano | findstr :5173
taskkill /PID <PID> /F

# Reconstruir node_modules
cd C:\Users\mansa\OneDrive\Área de Trabalho\projeto-devolucoes\frontend
npm install
npm run build
```

### Problema 2: Backend respondendo mas Frontend não

**Causas**:
- Erro em `npm run dev` do frontend
- Arquivo `package.json` corrompido

**Soluções**:
```bash
# Limpar cache e reinstalar
cd C:\Users\mansa\OneDrive\Área de Trabalho\projeto-devolucoes\frontend
rm -r node_modules package-lock.json
npm install
npm run dev
```

Verificar logs na página de devoluções.

### Problema 3: Processos não terminam ao clicar "Parar"

**Causas**:
- `process.terminate()` não funcionou (processo travado)
- Subprocessos filhos ainda rodando

**Soluções**:
```python
# Em devolucoes_app.py, usar SIGKILL em vez de SIGTERM
def stop(self) -> None:
    for process in (self.frontend_process, self.backend_process):
        if self._is_process_running(process):
            process.kill()  # SIGKILL (força)
            # process.terminate()  # SIGTERM (graceful)
```

---

## 📊 Estrutura do projeto-devolucoes

```
projeto-devolucoes/
├── backend/
│   ├── package.json
│   ├── src/
│   │   ├── routes/
│   │   │   ├── devolutions.js      ← GET /devolutions
│   │   │   ├── mercadolivre.js     ← Integração ML
│   │   │   └── shopee.js           ← Integração Shopee
│   │   ├── middleware/
│   │   │   └── auth.js
│   │   └── index.js                ← express app, porta 3001
│   └── node_modules/
│
├── frontend/
│   ├── package.json
│   ├── src/
│   │   ├── components/
│   │   │   ├── DevolutionsList.jsx ← Lista de devoluções
│   │   │   ├── DevolutionDetail.jsx ← Detalhes
│   │   │   └── Actions.jsx         ← Botões (aceitar, rejeitar)
│   │   ├── pages/
│   │   │   └── index.jsx           ← Página principal
│   │   ├── App.jsx                 ← Componente raiz
│   │   └── main.jsx                ← Vite entry point
│   ├── vite.config.js              ← Config com base: /devolucoes-app/
│   └── node_modules/
│
└── README.md
```

---

## ✅ Checklist de Implementação

Se você quer melhorar o módulo de devoluções:

- [ ] Entender a arquitetura (você já fez!)
- [ ] Localizar projeto-devolucoes
- [ ] Verificar se npm roda em ambos os lados (frontend + backend)
- [ ] Adicionar health check no backend (`GET /health`)
- [ ] Testar ciclo start → stop → start
- [ ] Adicionar testes automáticos
- [ ] Documentar novos endpoints da API
- [ ] Melhorar tratamento de erros
- [ ] Adicionar retry automático se cair

---

## 🚀 Próximas Melhorias Sugeridas

### 1. **Auto-restart se cair**
```python
def start(self) -> None:
    # Se processo morrer, reiniciar automaticamente
    while True:
        if not self._is_process_running(self.backend_process):
            self.backend_process = self._start_process(...)
        time.sleep(5)
```

### 2. **Melhor tratamento de erros**
```python
def _url_ok(self, url: str, retries: int = 3) -> bool:
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=2)
            return r.status_code < 500
        except Exception:
            time.sleep(1)
    return False
```

### 3. **Prometheus metrics**
```python
# Expor métricas de saúde
@app.get("/devolucoes/metrics")
def devolucoes_metrics():
    snapshot = devolucoes_runner.snapshot()
    return {
        "backend_ok": snapshot.backend_ok,
        "frontend_ok": snapshot.frontend_ok,
        "uptime_seconds": ...,
        "logs_count": len(snapshot.logs),
    }
```

### 4. **WebSocket para logs em tempo real**
```python
# Ao invés de refresh a cada 4s, usar WebSocket
from flask_socketio import emit
@socketio.on("connect")
def handle_connect():
    emit("devolucoes_snapshot", devolucoes_runner.snapshot())
```

---

## 📝 Resumo

**O módulo de devoluções**:
1. ✅ É um subsistema independente (backend + frontend separados)
2. ✅ Gerenciado por Python (iniciar/parar processos)
3. ✅ Integrado ao Seller Hub via iframe
4. ✅ Monitora saúde (health checks)
5. ✅ Mostra logs em tempo real
6. ✅ Funciona em múltiplas plataformas (ML, Shopee)

**Quando você acessa /devolucoes**:
- Backend Node.js inicia (lógica, API)
- Frontend React inicia (UI)
- HTML mostra status e iframe com ferramenta
- Você pode aceitar/rejeitar devoluções ali mesmo

Perguntas? Temos a documentação completa aqui! 🎉
