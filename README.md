# 🌍 AlienSMART - Protótipo WebGIS e IA Agentic

> **Inteligência Artificial ao Serviço da Biodiversidade** 🤖🌿

![Python](https://img.shields.io/badge/Python-3.8+-3776ab?style=flat-square&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat-square&logo=fastapi)
![LangChain](https://img.shields.io/badge/LangChain-IA%20Agentic-00a86b?style=flat-square)
![Status](https://img.shields.io/badge/Status-Em%20Desenvolvimento-orange?style=flat-square)

---

## 📋 Sobre o Projeto

Este repositório contém o **protótipo exploratório** desenvolvido no âmbito do projeto **AlienSMART**. O objetivo principal é explorar, testar e desenvolver protótipos baseados em **IA Agentic** para apoiar a preparação, geração e validação de relatórios técnicos sobre espécies invasoras.

---

## 🗺️ Arquitetura do Projeto

O projeto segue uma **arquitetura desacoplada** de Frontend e Backend:

```
invasive-species-ai-app/
├── 📁 backend/
│   ├── 🐍 main.py                # API REST (FastAPI) & Rotas
│   ├── 🤖 agent_report.py        # Agente LangChain/Ollama
│   ├── 📊 records.json           # Base de Dados Local
│   └── 📄 requirements.txt       # Dependências Python
│
└── 📁 frontend/
    └── 🌐 index.html             # Interface WebGIS (Leaflet)
```

---

## 🎯 Capacidades Analíticas

O sistema foi desenhado para responder às seguintes questões chave:

| Questão                        | Descrição                                        |
| ------------------------------ | ------------------------------------------------ |
| 🏆 **Espécie Dominante**       | Qual é a espécie com mais registos?              |
| 📍 **Hotspot Municipal**       | Qual é o município com mais ocorrências?         |
| 🔄 **Distribuição Geográfica** | Que espécies aparecem em múltiplos municípios?   |
| ⚠️ **Áreas Prioritárias**      | Há municípios críticos para monitorização?       |
| 🔍 **Limitações Dados**        | Que restrições têm os dados disponíveis?         |
| 📈 **Informação Adicional**    | Que dados são necessários para análise rigorosa? |

---

## 🧠 Modos de Funcionamento

O sistema suporta **2 modos híbridos** para o motor de IA:

### 🔒 **Modo 1: Local (Ollama)** - Privacidade + Zero Custo

```
✅ Sem conexão à internet
✅ Dados sensíveis protegidos
✅ Controlo total da infraestrutura
❌ Menor capacidade analítica
```

### ☁️ **Modo 2: Online (OpenAI)** - Máxima Inteligência

```
✅ Melhor qualidade de análise
✅ Maior fluidez de linguagem
❌ Requer chave de API
❌ Consumo de créditos
```

---

## 🚀 Guia de Instalação Rápida

### Pré-requisitos

```bash
# Modo Local
✓ Ollama instalado e em execução
  → https://ollama.ai

# Modo Online
✓ Chave de API OpenAI válida
  → https://platform.openai.com/api-keys
```

### Instalação das Dependências

```bash
cd backend/
pip install -r requirements.txt
```

---

## 💻 Configuração: Modo Local (Ollama)

### 1️⃣ Baixar Modelo Otimizado

```bash
ollama pull llama3.2
```

### 2️⃣ Configurar Backend (`agent_report.py`)

```python
from langchain_ollama import ChatOllama

model = ChatOllama(
    model="llama3.2",
    temperature=0
)
```

### 3️⃣ (Opcional) Endpoint Remoto

Se o Ollama está num servidor dedicado, crie `.env`:

```env
OLLAMA_BASE_URL=http://<ip-servidor>:11434/
```

E atualize o código:

```python
import os
from langchain_ollama import ChatOllama

model = ChatOllama(
    model="llama3.2",
    temperature=0,
    base_url=os.getenv("OLLAMA_BASE_URL")
)
```

---

## 🌐 Configuração: Modo Online (OpenAI)

### 1️⃣ Configurar Credenciais (`.env`)

```env
OPENAI_API_KEY=sk-proj-sua-chave-aqui
```

### 2️⃣ Configurar Backend (`agent_report.py`)

```python
from langchain_openai import ChatOpenAI

model = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0
)
```

---

## 🔄 Trocar entre Modos

Para alternar entre **Modo Local** e **Modo Online**:

1. Edite `backend/agent_report.py`
2. Alterne entre os blocos de import:
   - **Local**: `from langchain_ollama import ChatOllama`
   - **Online**: `from langchain_openai import ChatOpenAI`
3. Reinicie a aplicação

---

## 📚 Estrutura de Ficheiros Importantes

| Ficheiro           | Propósito                       |
| ------------------ | ------------------------------- |
| `main.py`          | 🚀 Servidor FastAPI & Endpoints |
| `agent_report.py`  | 🤖 Lógica do Agente IA          |
| `records.json`     | 📊 Base de dados de registos    |
| `requirements.txt` | 📦 Dependências Python          |
| `index.html`       | 🗺️ Interface do utilizador      |

---

## 🛠️ Stack Tecnológico

- **Backend**: FastAPI, LangChain, Ollama/OpenAI
- **Frontend**: HTML5, Leaflet, JavaScript
- **Base de Dados**: JSON local
- **IA**: LangChain Agents, Tool Calling

---

## 📞 Suporte & Contribuições

Para dúvidas, reportar bugs ou sugerir melhorias, por favor abra uma **Issue** neste repositório.

---

**Desenvolvido com ❤️ para o Projeto AlienSMART**

O ficheiro backend/requirements.txt encontra-se unificado e já inclui os pacotes de ambos os ecossistemas, eliminando a necessidade de reinstalação:

langchain-ollama (Suporte Local)

langchain-openai (Suporte Cloud/API)
