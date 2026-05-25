AlienSMART - Protótipo WebGIS e IA Agentic

Este repositório contém o protótipo exploratório desenvolvido no âmbito do projeto AlienSMART. O objetivo principal é explorar, testar e desenvolver protótipos baseados em IA Agentic para apoiar a preparação, geração e validação de relatórios técnicos sobre espécies invasoras.

🗺️ Estrutura do Projeto

O projeto encontra-se dividido numa arquitetura desacoplada de Frontend e Backend:

invasive-species-ai-app/
├── backend/
│   ├── main.py            # API REST (FastAPI) e rotas de relatórios
│   ├── agent_report.py    # Configuração do Agente LangChain/Ollama
│   ├── records.json       # Base de dados local de registos georreferenciados
│   └── requirements.txt   # Dependências do ecossistema Python
└── frontend/
    └── index.html         # Interface Web com mapa interativo Leaflet


📊 Perguntas de Análise

O sistema foi desenhado para ajudar decisores, técnicos e gestores do território a responder às seguintes questões de monitorização:

Qual é a espécie com mais registos?

Qual é o município com mais ocorrências?

Que espécies aparecem em mais do que um município?

Há municípios que possam ser considerados prioritários para monitorização?

Que limitações existem no conjunto de dados?

Que informação adicional seria necessária para uma análise mais rigorosa?

🧠 Modos do Modelo de Linguagem (LLM Modes)

O sistema suporta arquiteturas híbridas, permitindo alternar de forma flexível entre processamento Local (Privacidade e Custo Zero) e Online (Maior Raciocínio Comercial).

Requisitos Prévios

Modo Local: O software Ollama deve estar instalado e em execução no sistema operativo.

Modo Online: Uma chave de API válida (OPENAI_API_KEY) registada no ambiente de desenvolvimento.

💻 1. Modo Local (Ollama)

Indicado para funcionamento offline, garantia de privacidade de dados sensíveis e controlo total da infraestrutura de IA.

Instalar as dependências necessárias:

pip install -r backend/requirements.txt


Descarregar o modelo otimizado para chamadas de ferramentas (tool calling):

ollama pull llama3.2


Configuração do código (agent_report.py):

from langchain_ollama import ChatOllama
model = ChatOllama(model="llama3.2", temperature=0)


Definição de Endpoint Externo (Opcional):
Caso o Ollama esteja a rodar num servidor dedicado na rede e não no teu localhost, adiciona ao teu .env:

OLLAMA_BASE_URL=http://<ip-do-servidor>:11434/


E inicializa o modelo passando a variável de ambiente:

import os
model = ChatOllama(model="llama3.2", temperature=0, base_url=os.getenv("OLLAMA_BASE_URL"))


🌐 2. Modo Online (OpenAI)

Oferece maior fluidez linguística e capacidade analítica estrita utilizando os serviços cloud da OpenAI.

Instalar as dependências necessárias:

pip install -r backend/requirements.txt


Definir as credenciais secretas no ficheiro .env:

OPENAI_API_KEY=sk-proj-asua-chave-aqui


Configuração do código (agent_report.py):

from langchain_openai import ChatOpenAI
model = ChatOpenAI(model="gpt-4o-mini", temperature=0)


🔄 Alternância Rápida entre Motores

Para trocar o motor de inferência do AlienSMART de forma manual, acede ao ficheiro backend/agent_report.py e altera os blocos de import e inicialização do model consoante as instruções descritas acima.

O ficheiro backend/requirements.txt encontra-se unificado e já inclui os pacotes de ambos os ecossistemas, eliminando a necessidade de reinstalação:

langchain-ollama (Suporte Local)

langchain-openai (Suporte Cloud/API)
