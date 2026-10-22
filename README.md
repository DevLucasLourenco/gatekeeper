# gatekeeper

Gates por resposta para o Claude Code, impostos por hooks. Uma resposta que mudou o projeto só termina quando as evidências pedidas existem: os subagentes certos rodaram depois da última mudança, as skills foram usadas, o texto de fechamento está na resposta.

O `CLAUDE.md` é só contexto: o Claude pode esquecer o que está lá. O gatekeeper não depende de lembrança. O hook `Stop` lê o que aconteceu de fato no turno e devolve a resposta ao Claude com uma instrução quando falta algo.

## Sumário

- [Instalação](#instalação)
- [Configurar um projeto](#configurar-um-projeto)
- [Referência da configuração](#referência-da-configuração)
- [Como funciona](#como-funciona)
- [Escape e auditoria](#escape-e-auditoria)
- [Linha de comando](#linha-de-comando)
- [Desempenho](#desempenho)
- [Limitações conhecidas](#limitações-conhecidas)
- [Desenvolvimento](#desenvolvimento)
- [Licença](#licença)

## Instalação

Requisitos: **Python 3.13 ou mais novo** no `PATH` como `python` (só biblioteca padrão, sem `pip install`) e **git**. Os hooks chamam `python` direto, sem shell, então ele precisa ser um executável de verdade (`python.exe`), não um atalho `.cmd`. Com Python mais antigo, os hooks avisam no stderr e não impõem nada.

O repositório é ao mesmo tempo o plugin e um marketplace. Registre o marketplace uma vez e instale o plugin, direto do GitHub:

```bash
claude plugin marketplace add DevLucasLourenco/gatekeeper
claude plugin install gatekeeper@gatekeeper
```

Ou de um clone local, que é o jeito mais prático de mexer no código:

```bash
git clone https://github.com/DevLucasLourenco/gatekeeper.git
claude plugin marketplace add "C:/caminho/para/gatekeeper"
claude plugin install gatekeeper@gatekeeper
```

`gatekeeper` é o nome do marketplace declarado em `.claude-plugin/marketplace.json`, nos dois casos.

`install` aceita `--scope user` (padrão, vale para todos os seus projetos), `project` (fica no `.claude/settings.json` do repositório, para o time) ou `local`. Instalar não liga nada sozinho: **um projeto só entra nos gates quando tem `.claude/gatekeeper.toml`**. Sem esse arquivo, todos os hooks saem na hora sem fazer nada.

Para desenvolver o plugin sem instalar, rode o Claude Code com `--plugin-dir "C:/caminho/para/gatekeeper"`.

Depois de mudar o código do plugin:

- **Marketplace adicionado por caminho local** (o caso acima): o plugin é carregado direto da pasta. A mudança vale na próxima sessão ou depois de `/reload-plugins`, sem subir o `version`.
- **Marketplace hospedado em git**: o Claude Code copia o plugin para um cache e o `version` do `plugin.json` fixa a cópia. Suba o `version` e rode `claude plugin update gatekeeper@gatekeeper`.

## Configurar um projeto

O jeito guiado é a skill do plugin: digite `/gatekeeper`. Uma skill de plugin responde pelo nome curto quando nenhum outro comando usa o mesmo nome. A forma completa, `/gatekeeper:gatekeeper`, funciona sempre, inclusive se outro comando tiver tomado o `/gatekeeper`.

Antes de perguntar qualquer coisa, a skill roda o `inventory`, que lista os subagentes, as skills, os hooks e a estrutura do projeto. A partir daí ela funciona de três jeitos:

| Como você chama | O que acontece |
|---|---|
| `/gatekeeper` sem texto, num projeto sem configuração | A skill propõe um conjunto de gates baseado no que encontrou, explicando de onde veio cada proposta. Depois pergunta, uma coisa por vez, o que ainda está em aberto |
| `/gatekeeper <o que você quer>` | O texto vira a especificação. A skill traduz o pedido em gates, resolve os nomes pelo inventário ("o revisor" vira o subagente que revisa) e só pergunta o que o texto deixou em aberto |
| `/gatekeeper` num projeto já configurado | A skill mostra os gates atuais e pergunta o que adicionar, mudar ou tirar |

Exemplo com texto:

```text
/gatekeeper exigir o reviewer depois de qualquer mudança em src, mensagens em português
```

Em todos os casos, a skill mostra o TOML final e só grava depois do seu sim. Depois roda o `doctor`. Ela nunca põe num gate um subagente ou skill que não existe. Se faltar algum, oferece criá-lo com você.

O jeito manual é partir de um preset e editar:

```bash
python "C:/caminho/para/gatekeeper/engine/run.py" init --preset verifiers
python "C:/caminho/para/gatekeeper/engine/run.py" doctor
```

| Preset | O que traz |
|---|---|
| `minimal` | Um gate de fechamento: toda resposta termina com "Summary" |
| `verifiers` | `reviewer` e `tester` depois de qualquer mudança, mais o fechamento |

Os subagentes citados precisam existir em `.claude/agents/` (do projeto ou do usuário) ou vir de um plugin. O `doctor` avisa quando não encontra um.

Os gates valem a partir do próximo prompt.

## Referência da configuração

Arquivo: `.claude/gatekeeper.toml`. A validação é estrita: chave desconhecida é erro, porque um erro de digitação desligaria um gate em silêncio.

```toml
language = "pt-BR"        # en | pt-BR: idioma das mensagens de bloqueio e do resumo injetado
on_error = "open"         # open: erro interno deixa a resposta terminar; closed: bloqueia
max_blocks = 3            # bloqueios por turno (1 a 8) antes de desistir e deixar terminar
bypass_token = "#skip-gates"
ignore = ["*.lock"]       # caminhos sujos que nunca contam como mudança (glob, ver abaixo)

[inject]
enabled = true            # injeta a lista de gates a cada prompt
preamble = "Primeira linha opcional do texto injetado."

[[gates]]
id = "verify"             # minúsculas, dígitos, - e _ (até 40)
description = "Depois de mudar arquivos, rode reviewer e tester em paralelo."
when = "changed"          # always (padrão) | changed
paths = ["lib/**/*.rs"]  # opcional; implica when = "changed"
message = "Texto opcional que substitui a mensagem gerada."
[gates.require]
subagents = ["reviewer", "tester"]
skills = ["test-first"]
final_text = "Resumo"
after_last_change = true  # padrão: a evidência precisa vir depois da última mudança
```

| Chave | Significado |
|---|---|
| `when = "always"` | O gate vale em toda resposta, mudando ou não o projeto |
| `when = "changed"` | Só quando o turno deixou o projeto diferente de como começou |
| `paths` | Só quando algum caminho mudado casa com um dos globs (regras abaixo) |
| `subagents` | Cada nome precisa aparecer como `agent_type` de um subagente que **terminou** no turno. `plugin:nome` também satisfaz `nome` |
| `skills` | Uso da ferramenta Skill com esse nome, ou um prompt que começa com `/nome` |
| `final_text` | Trecho que a resposta final precisa conter. Ignora maiúsculas, acentos e espaços repetidos |
| `after_last_change` | Com `true`, subagente ou skill de antes da última mudança não vale |

Um gate precisa de pelo menos uma exigência em `[gates.require]`. Um `final_text` que fica vazio depois da normalização (só acentos ou espaços) é recusado, porque passaria sempre.

### Globs de `paths` e `ignore`

Seguem as regras do `.gitignore`, sobre caminhos relativos à raiz do repositório git:

| Padrão | Casa com |
|---|---|
| `*.lock` | `Cargo.lock` e `a/b/Cargo.lock`: sem `/`, vale em qualquer profundidade |
| `node_modules` | Um arquivo ou uma pasta com esse nome, em qualquer profundidade, com tudo dentro |
| `dist/` | Tudo dentro de qualquer pasta `dist` (a barra final exige que seja pasta) |
| `src/**` | Tudo dentro de `src/` na raiz, mas não `outro/src/...`: com `/`, fica ancorado na raiz |
| `docs/*.md` | `docs/a.md`, mas não `docs/sub/a.md`: `*` não atravessa `/`, `**` atravessa |
| `/build` | Só `build` na raiz, com tudo dentro |

A comparação diferencia maiúsculas, como o git. `ignore` vale antes de tudo: um caminho ignorado não conta como mudança, então também não ativa um gate com `paths`.

## Como funciona

```text
UserPromptSubmit ─► abre o turno: snapshot inicial do git + injeta o resumo dos gates
                    (prompt automático do Claude Code, como o hand-back de um subagente, não abre turno)
PostToolUse      ─► depois de Edit/Write/Bash/PowerShell/Skill: novo snapshot (e registra a skill)
                    depois de Agent: registra o subagente que ficou rodando em segundo plano
SubagentStop     ─► registra o agent_type e o agent_id do subagente que terminou
Stop             ─► snapshot final, compara tudo e julga cada gate
```

**Mudança vem do git, não do texto do comando.** Cada snapshot é o `HEAD` mais a lista de caminhos sujos do `git status`, com tamanho e data de modificação. Comparar snapshots resolve três problemas de detectar escrita por regex:

- Escrever fora do repositório (pasta temporária, scratchpad) não conta como mudança.
- Caminhos ignorados pelo `.gitignore` (como `target/` e `node_modules/`) não contam.
- Uma escrita que nenhuma regex previu, feita por shell, subagente ou ferramenta MCP, aparece no snapshot final do `Stop`.

Uma edição desfeita no mesmo turno não conta como mudança, porque o que vale é a diferença entre o início e o fim. A **ordem** vem da sequência de snapshots: a última mudança é o último snapshot diferente do anterior, e só valem as evidências registradas depois dela. Uma skill que muda arquivos conta como evidência depois da própria mudança.

**Commit no mesmo turno.** Um turno que começa limpo, edita e faz `git commit` termina limpo, mas o `HEAD` andou. Quando o `HEAD` final difere do inicial, os caminhos que mudaram entre os dois commits (`git diff-tree`, ou `git ls-tree` se o repositório não tinha commit) entram na mudança do turno, respeitando `ignore` e `paths`. Isso cobre `commit --amend`, `reset` para um commit anterior e `checkout` de outro commit. Um `push` não move o `HEAD`, então não é mudança. Um `--amend` que só troca a mensagem também não, porque nenhum caminho muda. Se o git não conseguir listar os caminhos do commit (o commit inicial sumiu depois de um `gc`, timeout), o turno conta como mudança mesmo assim, e os gates com `paths` passam a valer, para errar do lado seguro.

Para a **ordem**, o snapshot compara o que está no disco, não como o git descreve. `git add` e um commit do conteúdo que já estava no disco não movem a última mudança. Por isso o fluxo editar → verificadores → commit passa: o commit não invalida os verificadores que rodaram antes dele. Um commit que traz conteúdo novo (um `--amend` depois de outra edição, um `reset --hard`, um `checkout`) reescreve arquivos e move a última mudança.

**Subagentes em segundo plano.** A documentação dos hooks diz que um subagente lançado em segundo plano devolve `status: "async_launched"` no `tool_response` do `PostToolUse`, com o `agentId`. Na prática, no app desktop, observamos que a ferramenta Agent também pode devolver na hora com `isAsync: true`, mesmo com `run_in_background: false`; esse campo não aparece na documentação, e o gatekeeper aceita os dois sinais. Nesse caso ele registra o lançamento com o `agentId`. Enquanto algum subagente lançado assim não tiver `SubagentStop` com o mesmo id, o `Stop` não julga: a resposta termina, a auditoria registra `waiting` e nenhum bloqueio é contado. Quando o subagente termina, o Claude Code envia o resultado como um prompt automático. A documentação confirma que esse retorno dispara um `UserPromptSubmit`, mas não descreve o texto; o formato que observamos é `<agent-message from="...">` com `[Subagent hand-back]`, ou `<task-notification>`. Esse prompt não abre turno novo: o turno que mudou o projeto continua, o `SubagentStop` conta para ele e o `Stop` seguinte julga tudo junto. O payload do `UserPromptSubmit` não diz quem enviou o prompt, então o gatekeeper reconhece esses envelopes pelo começo do texto.

**Log enxuto.** Só o início do turno guarda o snapshot completo. Os eventos seguintes guardam o `HEAD` e um hash dele, que basta para achar a última mudança, e o `Stop` compara o snapshot final com o inicial para saber quais caminhos mudaram. Um turno com centenas de ferramentas num repositório sujo fica em dezenas de KB, não em megabytes.

**Estado fora do projeto.** O log do turno e a auditoria ficam na pasta de dados do plugin (`~/.claude/plugins/data/gatekeeper-gatekeeper/`), nunca no repositório. Se ficassem no repositório, eles mesmos apareceriam como mudança. Cada sessão tem seu log, então duas sessões no mesmo projeto não se misturam. Logs de sessão com mais de 14 dias são apagados quando um turno novo começa.

**Hooks concorrentes.** Chamadas de ferramenta em paralelo e vários subagentes disparam hooks ao mesmo tempo. O log é JSONL só de acréscimo, e quem lê ignora linha quebrada. O `git status` roda com `GIT_OPTIONAL_LOCKS=0` para não disputar o `index.lock`.

**Falhas.** Só o `Stop` bloqueia. Os outros hooks sempre saem com 0: um erro do gatekeeper nunca impede uma ferramenta ou um prompt. No `Stop`, `on_error = "open"` deixa a resposta terminar e escreve o erro no stderr; `closed` bloqueia. Depois de `max_blocks` bloqueios no mesmo turno, o gatekeeper desiste e registra `gave_up` na auditoria. Isso vale também para a falha fechada (com configuração inválida, o limite é 3). O próprio Claude Code tem um teto de 8 continuações seguidas causadas por hook Stop: a contagem zera quando o Claude chama uma ferramenta, e o teto muda com `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`.

**Configuração inválida não passa em silêncio.** Se o `.claude/gatekeeper.toml` não valida, o hook do prompt avisa você (mensagem na tela) e o Claude (contexto) de que os gates estão desligados até a correção.

## Escape e auditoria

Escrever `#skip-gates` no prompt dispensa todos os gates daquele turno. `#skip-gates:verify,closing` dispensa só esses. Só o usuário consegue fazer isso, porque o token é lido do prompt que você digitou. Um token dentro do relatório de um subagente ou de uma notificação de tarefa não vale: esses prompts automáticos não abrem turno.

Toda resposta julgada vira um registro na auditoria: `passed`, `blocked` (com os gates), `bypassed`, `gave_up` ou `waiting` (havia subagente em segundo plano rodando; vem com `pending`), mais os caminhos mudados e os gates dispensados. Quando passa de 5 MB, o arquivo vira `.jsonl.1` e um novo começa. Para ver o resumo:

```bash
python "C:/caminho/para/gatekeeper/engine/run.py" stats
```

Use para descobrir qual gate mais barra. Um gate que bloqueia sempre provavelmente está mal descrito. Um que nunca bloqueia talvez não precise existir.

## Linha de comando

`python engine/run.py <comando>`, rodado na raiz do projeto ou com `--root <pasta>`:

| Comando | O que faz |
|---|---|
| `init [--preset minimal\|verifiers]` | Escreve `.claude/gatekeeper.toml` a partir do preset. Nunca sobrescreve |
| `doctor` | Confere Python, git, a configuração, se os subagentes e skills citados existem, e mostra onde fica o estado |
| `stats` | Resume a auditoria do projeto |
| `inventory` | Imprime em JSON os subagentes, as skills, os hooks (do projeto, locais e do usuário; os de plugins não aparecem), as pastas, extensões, testes e CI do projeto. É o que a skill `/gatekeeper` usa para sugerir gates. Lista até 40 subagentes e 40 skills: primeiro os do projeto, depois os mais relevantes para gates (palavras de teste, revisão, lint, segurança, auditoria e qualidade no nome, com peso maior, e no começo da descrição), com descrições de até 160 caracteres e sem as skills desligadas (`"off"` em `skillOverrides`) |
| `hook <evento>` | Usado pelos hooks do plugin: lê o payload no stdin |

Os hooks recebem do Claude Code a pasta de dados do plugin, mas um comando rodado no terminal não. Por isso `doctor` e `stats` procuram a pasta do plugin instalado (`~/.claude/plugins/data/gatekeeper-*`) e aceitam `--data-dir <pasta>` para apontar outra, como a de um plugin carregado com `--plugin-dir`.

## Desempenho

Medições locais (Windows 11, Python 3.14, pasta dentro do OneDrive). Entre execuções, a variação chega a 20-30%, então leia como ordem de grandeza:

| Operação | Custo |
|---|---|
| Python vazio | ~80 ms |
| Import do caminho dos hooks | ~105 ms |
| Um snapshot num repositório limpo | ~90 ms (quase tudo é o início do processo `git`) |
| Um snapshot com 500 arquivos sujos | ~150 ms |
| Um snapshot depois de um commit no turno | ~160 ms (um `git diff-tree` a mais, só quando o `HEAD` andou) |
| Processo inteiro de um hook, com 500 arquivos sujos | ~400 a 450 ms |

O custo recai em `UserPromptSubmit`, `Stop` e em cada `Edit`, `Write`, `Bash`, `PowerShell` ou `Skill`. Depois de `Agent`, o hook só registra o lançamento, sem chamar o git. Leituras (`Read`, `Grep`, `Glob`) não disparam nada. Acima de 2000 caminhos sujos, o snapshot deixa de ler tamanho e data de cada arquivo, para não pagar milhares de `stat`. Em repositórios grandes, ligar o `core.fsmonitor` e o `core.untrackedCache` do git acelera o `git status`.

Ficaram para depois:

- Um processo persistente, que eliminaria os ~200 ms de Python e import por evento, mas traz mais complexidade e mais pontos de falha.
- Snapshot só depois de `Bash` e `PowerShell` que de fato escrevem, o que exige decidir quanto de precisão trocar por velocidade.

## Limitações conhecidas

- **Só detecta mudança em repositório git.** Fora de git, nenhum gate `changed` dispara; os gates `always` continuam valendo.
- **Mudanças suas contam.** Arquivo que você salvar no editor durante o turno aparece no snapshot como se fosse do Claude.
- **Prompt automático reconhecido pelo texto.** Os envelopes `<agent-message from=` e `<task-notification>` e o campo `isAsync` foram observados na prática, não estão na documentação. O payload do `UserPromptSubmit` não traz a origem do prompt, então o hand-back de subagente e a notificação de tarefa são reconhecidos pelo começo do texto (`<agent-message from=`, `<task-notification>`). Se o Claude Code mudar esse formato, cada hand-back volta a abrir um turno novo. Um prompt que você digitar começando assim também não abre turno.
- **Subagente em segundo plano que não volta.** Enquanto um subagente lançado em segundo plano não termina, o `Stop` não julga. Se ele nunca terminar (foi parado, a sessão caiu), os gates daquele turno só voltam a valer no próximo prompt seu. Não há teto de tempo: o gatekeeper não protege contra um modelo que lance um subagente que não volta, só contra um que esqueça os verificadores.
- **Prompt seu enquanto o subagente roda.** Um prompt real abre turno novo. A mudança do turno anterior deixa de ser cobrada, e o `SubagentStop` que chegar depois conta para o turno novo.
- **Commit com muitos caminhos.** Acima de 2000 caminhos entre sujos e commitados, o snapshot não lê tamanho e data do resto, e todo commit novo passa a mover a última mudança.
- **Repositório muito sujo.** Acima de 2000 caminhos sujos, uma nova edição num arquivo que já estava sujo e ficou além desse limite não aparece. Arquivos novos, apagados e com status alterado continuam aparecendo.
- **Skill pela ferramenta.** A documentação garante o `PostToolUse` para ferramentas em geral, mas não cita a Skill. Um prompt que começa com `/nome` sempre conta.
- **Hooks antigos do projeto.** Se o projeto já tem hooks próprios que impõem as mesmas regras (por exemplo um `Stop` que bloqueia), os dois bloqueiam. Desligue os antigos ao migrar. O `inventory` lista esses hooks, e a skill `/gatekeeper` avisa sobre eles.

## Desenvolvimento

```text
.claude-plugin/   plugin.json (manifesto) e marketplace.json (o marketplace do próprio repositório)
hooks/hooks.json  liga os eventos ao engine/run.py
skills/gatekeeper/  skill /gatekeeper
engine/gatekeeper/
  config.py       lê e valida o TOML
  matching.py     globs estilo gitignore e normalização de texto
  turn.py         eventos do turno → fatos (puro)
  rules.py        fatos + configuração → violações (puro)
  snapshot.py     git status → snapshot
  state.py        log do turno e auditoria, fora do projeto
  hooks.py        handlers de cada evento e entrada dos hooks (stdin)
  messages.py     textos em en e pt-BR
  inventory.py    o que existe no projeto, para a skill sugerir gates
  cli.py          init, doctor, stats, inventory
engine/run.py     ponto de entrada; o caminho dos hooks pula o argparse
tests/            unittest, sem dependências
```

`turn.py` e `rules.py` não fazem I/O: a maior parte do comportamento é testada sem disco nem git. Para rodar os testes:

```bash
python -m unittest discover -s tests -t .
```

Para validar os manifestos:

```bash
claude plugin validate .
claude plugin validate .claude-plugin/plugin.json
```

## Licença

[MIT](LICENSE).
