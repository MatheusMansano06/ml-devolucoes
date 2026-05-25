# 🛠️ Guia Prático - Trabalhando com Devoluções

## 🎯 Objetivos Comuns

### 1. Testar o módulo localmente
### 2. Melhorar a interface ou backend
### 3. Adicionar novos endpoints
### 4. Depurar problemas

---

## 📍 Estrutura de Arquivos - Onde está o quê?

```
seller-hub/                          ← Você está aqui
├── src/devolucoes_app.py            ← GERENCIADOR (Python)
├── templates/devolucoes.html        ← INTERFACE (HTML/JS)
├── app.py                           ← ROTAS FLASK
│
projeto-devolucoes/                  ← PROJETO SEPARADO
├── backend/                         ← API Node.js
│   ├── src/index.js                 ← Express server
│   ├── package.json
│   └── ...
│
└── frontend/                        ← UI React
    ├── src/App.jsx
    ├── vite.config.js
    ├── package.json
    └── ...
```

---

## 🚀 Cenário 1: Executar Localmente para Testar

### Passo 1: Verificar dependências

```bash
# Ter Node.js instalado
node --version          # Deve ser v16+
npm --version           # Deve ser v8+

# Python rodando o Seller Hub
python app.py           # Começa a rodar em http://127.0.0.1:5000
```

### Passo 2: Acessar a página de devoluções

```
1. Abra http://127.0.0.1:5000/login
2. Digite o PIN (padrão: 1234 ou valor em .env PIN_MERCADO_LIVRE)
3. Clique em "Gestão de Devoluções"
4. Clique em "Abrir ferramenta"
```

### Passo 3: Observar o que acontece

```
Você deve ver:
✅ Backend: OK (verde)  
✅ Frontend: OK (verde, após 3-5s)
```

Se algum virar vermelho, procure **"Logs"** na página.

### Passo 4: Testar a funcionalidade

```
- Liste devoluções
- Aceite uma
- Rejeite outra
- Verifique se status muda no Mercado Livre/Shopee
```

---

## 🎨 Cenário 2: Melhorar a Interface (Frontend)

### Problema: Botão "Aceitar devolução" está confuso

### Passo 1: Localizar o arquivo

```bash
cd C:\Users\mansa\OneDrive\Área de Trabalho\projeto-devolucoes\frontend\src

# Encontrar onde o botão é definido
findstr /r "Aceitar" components/*.jsx
# ou
grep -r "Accept" components/
```

Típico resultado: `components/DevolutionDetail.jsx`

### Passo 2: Abrir no editor

```bash
code .  # Abre VSCode na pasta frontend
# Arquivo: src/components/DevolutionDetail.jsx
```

### Passo 3: Fazer mudança

**Antes**:
```jsx
<button onClick={handleAccept}>Aceitar</button>
```

**Depois**:
```jsx
<button className="button primary" onClick={handleAccept}>
  ✅ Aceitar Devolução
</button>
```

### Passo 4: Testar a mudança

```bash
# O Vite faz hot-reload automático
# Só salve o arquivo e recarregue o navegador
# Não precisa reiniciar nada!
```

A mudança aparece em tempo real no iframe.

### Passo 5: Commit

```bash
cd projeto-devolucoes
git add .
git commit -m "feat: melhorar texto botão aceitar devolução"
git push
```

---

## 🔌 Cenário 3: Adicionar Novo Endpoint no Backend

### Problema: Preciso de um endpoint que retorna estatísticas de devoluções

### Passo 1: Abrir backend

```bash
cd C:\Users\mansa\OneDrive\Área de Trabalho\projeto-devolucoes\backend
code .
```

### Passo 2: Criar novo arquivo de rota

**Arquivo**: `src/routes/stats.js`

```javascript
const express = require('express');
const router = express.Router();

// GET /api/stats/devolutions
// Retorna: total, aceitas, rejeitadas, em_processo
router.get('/devolutions', async (req, res) => {
  try {
    // Aqui você consulta o BD ou chama a API ML/Shopee
    const stats = {
      total: 42,
      accepted: 28,
      rejected: 10,
      pending: 4,
      last_updated: new Date().toISOString()
    };
    res.json(stats);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

module.exports = router;
```

### Passo 3: Registrar a rota no servidor

**Arquivo**: `src/index.js`

```javascript
const express = require('express');
const statsRouter = require('./routes/stats');

const app = express();

// Registrar rota
app.use('/api/stats', statsRouter);

// ... resto do código
```

### Passo 4: Testar o endpoint

```bash
# Abra browser ou use curl/Postman
curl http://127.0.0.1:3001/api/stats/devolutions

# Resposta esperada:
# {
#   "total": 42,
#   "accepted": 28,
#   "rejected": 10,
#   "pending": 4,
#   "last_updated": "2025-01-20T10:30:00.000Z"
# }
```

### Passo 5: Usar no Frontend

**Arquivo**: `src/components/StatsPanel.jsx`

```jsx
import { useEffect, useState } from 'react';

export function StatsPanel() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    fetch('http://127.0.0.1:3001/api/stats/devolutions')
      .then(r => r.json())
      .then(setStats);
  }, []);

  if (!stats) return <div>Carregando...</div>;

  return (
    <div className="stats">
      <div>Total: {stats.total}</div>
      <div>Aceitas: {stats.accepted}</div>
      <div>Rejeitadas: {stats.rejected}</div>
      <div>Pendentes: {stats.pending}</div>
    </div>
  );
}
```

---

## 🐛 Cenário 4: Depurar Problema

### Problema: Backend respondendo, mas frontend não carrega

### Passo 1: Verificar logs Python

Volte para a página de devoluções no Seller Hub.

Scroll down para **"Logs"**:

```
backend: iniciado
backend: port 3001 listening
backend: database connected
frontend: iniciado
frontend: VITE v5.0.0 building...
frontend: ⚠️  warnings found
frontend: ❌ Could not resolve 'react-router-dom'
```

**Problema**: `react-router-dom` não está instalado.

### Passo 2: Resolver o problema

```bash
cd projeto-devolucoes/frontend

# Instalar dependência faltante
npm install react-router-dom

# Ou se arquivo package.json.lock está corrompido:
rm package-lock.json
npm install
```

### Passo 3: Reiniciar no browser

Clique "Parar" → "Abrir ferramenta" novamente.

Os logs devem mostrar que funcionou:

```
frontend: ✅ built successfully
frontend: Local:   http://127.0.0.1:5173/devolucoes-app/
```

### Passo 4: Verificar no navegador

```
Developer Tools (F12) → Console
```

Se houver erros vermelhos, procure por:
- `Cannot find module`
- `TypeError`
- `SyntaxError`

### Passo 5: Procurar solução

```bash
# Se é erro de dependência:
npm install <nome-do-pacote>

# Se é erro de código:
# Abra arquivo, procure a linha, corrija
# Salve, Vite faz hot reload
```

---

## 🔍 Cenário 5: Entender o Fluxo de uma Devolução

### "Quero saber exatamente o que acontece quando clico 'Aceitar devolução'"

### Passo 1: Abrir DevTools (F12)

```
Network Tab
```

Clique "Aceitar devolução" e observe:

```
POST /api/devolutions/123/accept

Request:
  - Headers: Authorization, Content-Type
  - Body: { decision: 'accepted', reason: 'Item danificado' }

Response:
  - Status: 200 OK
  - Body: { id: 123, status: 'accepted', updated_at: '...' }
```

### Passo 2: Localizar handler no backend

**Arquivo**: `backend/src/routes/devolutions.js`

```javascript
router.post('/:id/accept', async (req, res) => {
  const { id } = req.params;
  const { reason } = req.body;

  // 1. Buscar devolução no BD
  const devolution = await Devolution.findById(id);

  // 2. Atualizar status
  devolution.status = 'accepted';
  devolution.reason = reason;
  await devolution.save();

  // 3. Chamar API Mercado Livre (atualizar lá também)
  await ml_api.acceptDevolution(id);

  // 4. Retornar resposta
  res.json(devolution);
});
```

### Passo 3: Rastrear integração com ML

```javascript
// Arquivo: backend/src/services/ml_api.js

async function acceptDevolution(devolutionId) {
  // ML usa OAuth token (em .env)
  const token = process.env.ML_ACCESS_TOKEN;

  // Chamar API ML para atualizar devolução
  const response = await fetch(
    `https://api.mercadolivre.com/devolutions/${devolutionId}`,
    {
      method: 'PUT',
      headers: { Authorization: `Bearer ${token}` },
      body: JSON.stringify({ status: 'accepted' })
    }
  );

  return response.json();
}
```

### Passo 4: Visualizar no DevTools

```
1. Network tab → POST /api/devolutions/123/accept
2. Clique nela → "Response"
3. Veja o JSON retornado
4. Verifique timestamp para confirmar que atualizou
```

---

## ✨ Tarefas Comuns

### Task 1: Adicionar nova coluna na tabela de devoluções

```jsx
// Arquivo: frontend/src/components/DevolutionsList.jsx

// Antes:
// <tr><td>ID</td><td>Produto</td><td>Status</td></tr>

// Depois:
// <tr><td>ID</td><td>Produto</td><td>Status</td><td>Data</td><td>Ações</td></tr>

const columns = ['id', 'product', 'status', 'created_at', 'actions'];

return (
  <table>
    <thead>
      <tr>{columns.map(col => <th key={col}>{col}</th>)}</tr>
    </thead>
    <tbody>
      {devolutions.map(dev => (
        <tr key={dev.id}>
          <td>{dev.id}</td>
          <td>{dev.product}</td>
          <td>{dev.status}</td>
          <td>{new Date(dev.created_at).toLocaleDateString('pt-BR')}</td>
          <td>
            <button onClick={() => accept(dev.id)}>Aceitar</button>
            <button onClick={() => reject(dev.id)}>Rejeitar</button>
          </td>
        </tr>
      ))}
    </tbody>
  </table>
);
```

### Task 2: Adicionar filtro por status

```jsx
const [filter, setFilter] = useState('all');

const filtered = devolutions.filter(dev =>
  filter === 'all' || dev.status === filter
);

return (
  <>
    <select value={filter} onChange={e => setFilter(e.target.value)}>
      <option value="all">Todas</option>
      <option value="pending">Pendentes</option>
      <option value="accepted">Aceitas</option>
      <option value="rejected">Rejeitadas</option>
    </select>
    
    <table>
      {/* renderizar filtered */}
    </table>
  </>
);
```

### Task 3: Adicionar notificação sonora/visual

```jsx
useEffect(() => {
  if (newDevolution) {
    // Som
    const audio = new Audio('/notification.mp3');
    audio.play();

    // Notificação visual
    alert(`Nova devolução: ${newDevolution.product}`);
  }
}, [newDevolution]);
```

---

## 🧪 Testando Mudanças

### Teste local (sem deploy)

```bash
# Terminal 1: Rodar Seller Hub
cd C:\Users\mansa\OneDrive\Área de Trabalho\seller-hub
python app.py

# Terminal 2: Rodar backend (opcional, usa node_modules existing)
cd projeto-devolucoes/backend
npm start

# Terminal 3: Rodar frontend (opcional)
cd projeto-devolucoes/frontend
npm run dev

# Browser: Abrir http://127.0.0.1:5000/devolucoes
```

### Teste automático

```bash
cd projeto-devolucoes/backend

# Rodar testes
npm test

# Ver cobertura
npm test -- --coverage
```

---

## 📦 Produção (Deploy)

### Antes de fazer push para produção:

1. **Teste localmente** ✅
2. **Rode testes** ✅
3. **Build de produção** ✅

```bash
# Build frontend
cd projeto-devolucoes/frontend
npm run build    # Gera dist/

# Verificar build
ls -la dist/
```

4. **Commit e push**

```bash
git add -A
git commit -m "feat: melhorias no módulo de devoluções"
git push
```

5. **Deploy no servidor**

```bash
# No servidor VPS
cd /opt/projeto-devolucoes
git pull
cd backend && npm install && npm start &
cd frontend && npm install && npm run build
```

6. **Reiniciar Docker do Seller Hub**

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

---

## 🎯 Checklist antes de começar a trabalhar

- [ ] Node.js instalado (`node --version`)
- [ ] npm instalado (`npm --version`)
- [ ] Seller Hub rodando (`python app.py`)
- [ ] Conseguir acessar http://127.0.0.1:5000/login
- [ ] Conseguir clicar em "Gestão de Devoluções"
- [ ] Backend + Frontend iniciam (sem erros)
- [ ] VSCode aberto na pasta correta (`code .`)
- [ ] Git configurado (`git config --list`)

---

## 🆘 SOS - Problemas Comuns

| Problema | Causa | Solução |
|----------|-------|---------|
| "Ferramenta não inicia" | npm não instalado | Instalar Node.js |
| Backend OK, frontend não | Vite está lento | Esperar 5-10s, recarregar |
| Hot-reload não funciona | Arquivo em node_modules | Reiniciar `npm run dev` |
| Porta 5173 já em uso | Outro processo | `lsof -i :5173` → kill |
| Erro "Cannot find module" | Dependência faltante | `npm install <nome>` |
| BD não conecta | Credenciais `.env` | Verificar `.env` do backend |

---

## 📚 Recursos

- **Documentação Vite**: https://vitejs.dev/
- **React Docs**: https://react.dev/
- **Express.js**: https://expressjs.com/
- **Mercado Libre API**: https://developers.mercadolibre.com.br/

---

**Sucesso! Agora você sabe como trabalhar no módulo de devoluções! 🎉**
