# Plano: dados por usuário no modo cloud (Turso + R2)

**Estado:** adiado em 22/11/2026. O modo cloud vai para homologação como está (login com Adaflow, dados
compartilhados entre quem entra) e o foco passa para a UI. Este plano é retomado quando o uso justificar.

**Por que foi adiado:** separar dados por usuário obriga a guardar os áudios fora da máquina (R2), e as duas
coisas juntas mudam bastante o projeto em relação ao upstream.

**Enquanto isso:** um servidor por cliente ou equipe, restrito com `NIX_SSO_ALLOWED_ORGS`
(ver [`../cloud/README.md`](../cloud/README.md)).

## Como o app guarda dados hoje

- Um único SQLite local (`aicollector.db`) com três tabelas: `tracks`, `projects`, `settings`. Nenhuma tem dono.
- Os áudios ficam em disco, em `files/<modelo>/...`; o banco guarda o caminho absoluto de cada arquivo.
- Todo acesso passa por `backend/app/db.py`: cerca de 22 funções, todas pegando a conexão por `get_db()`.
  `FILES_DIR` só é usado dentro desse arquivo.
- Stems e MIDI usam o id da faixa como nome de pasta (`files/<modelo>/stems/<id>`): dois bancos com ids
  repetidos colidiriam no disco.
- `settings` mistura o que é do usuário (`artist`) com o que é do servidor (`engine.<motor>`).

## Estratégia: um banco por usuário, sem editar o upstream

Uma coluna "dono" exigiria reescrever as 22 funções do upstream, com conflito a cada merge. Trocar qual banco a
requisição usa não exige editar nada.

| Peça | Como |
|---|---|
| Quem é o usuário | O portão do SSO (`backend/nix/gate.py`) já identifica o usuário; passa a guardá-lo em um `contextvar` da requisição. |
| Banco do usuário | O `nix` substitui `app.db.get_db` por uma versão que devolve a conexão do usuário atual. O esquema e as migrações continuam sendo os do upstream, executados em cada banco. |
| Sem usuário no contexto | No modo cloud a chamada falha; nunca cai em um banco compartilhado. |
| Arquivos | Por usuário, pelo mesmo mecanismo. Resolve a colisão das pastas de stems. |
| Configurações | `artist` por usuário; `engine.*` em um banco de sistema (interceptando `get_setting`/`set_setting`). |
| Desktop e local | Nada muda: sem `NIX_SSO`, o `get_db()` original continua em uso. |

## Turso

- Encaixa no desenho: "um banco por usuário" é um modelo que o Turso suporta diretamente.
- Forma recomendada: réplica embutida (arquivo local por usuário, sincronizado com o banco dele no Turso).
  Leitura local, escrita no Turso.
- A separação por usuário não depende do Turso; ele acrescenta durabilidade, backup e acesso por outros serviços.
- Criar bancos sob demanda exige um token da API de plataforma do Turso no servidor (credencial sensível).

## Áudio no R2

- Os áudios não vão para o Turso. Sem armazenamento persistente, o banco aponta para arquivos que somem quando a
  máquina de GPU é trocada.
- É a parte que mais muda o projeto: hoje o upstream lê e grava caminhos locais em vários pontos (salvar faixa,
  tocar, baixar com tags, stems, MIDI, exportar para DAW). Tudo isso passa a depender de um objeto remoto.
- Caminho a avaliar primeiro: manter o disco local como cache de trabalho e sincronizar com o R2 (subir depois de
  gravar, baixar sob demanda), em vez de trocar cada leitura por uma chamada remota.

## Riscos a validar com um protótipo

1. Compatibilidade do cliente Python `libsql` (0.1.11 no PyPI em 11/2026) com o que o upstream usa do `sqlite3`:
   acesso a colunas por nome (`row["id"]`), `PRAGMA`, `lastrowid`. Pode precisar de um adaptador fino.
2. Latência de escrita: o editor salva o projeto inteiro como JSON a cada alteração.
3. Trabalho em segundo plano (stems, MIDI) gravando no banco depois da requisição: precisa carregar o usuário
   certo. Hoje o upstream usa `asyncio.to_thread`, que preserva o contexto; conferir de novo na retomada.

## O que continua compartilhado mesmo com bancos separados

- A GPU e o modelo ativo: um usuário trocar de modelo interrompe a geração de outro.
- Funções de administração (instalar e apagar modelos, configurações dos motores, pastas de dataset de LoRA,
  tokens do Hugging Face): restringir ao papel de administrador do Adaflow na mesma entrega.
- Jobs dos motores: o resultado é consultado por id direto no motor, sem checagem de dono.

## Etapas

1. Isolamento com SQLite local por usuário, arquivos por usuário e rotas de administração restritas por papel.
2. Turso: trocar a fábrica de conexões pela réplica embutida, com criação do banco no primeiro login.
3. Áudio no R2.
4. Migração: atribuir o banco atual a um usuário, se já houver dados no servidor.

## Decisões em aberto

1. Unidade de separação: por usuário, ou por organização do Adaflow (a equipe compartilha biblioteca e projetos).
2. Quais papéis do Adaflow podem mexer em modelos e configurações do servidor.
3. Ordem das etapas 2 e 3, e se a etapa 1 sozinha já atende um primeiro cliente.
