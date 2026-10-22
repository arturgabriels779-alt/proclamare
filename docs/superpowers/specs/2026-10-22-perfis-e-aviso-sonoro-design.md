# Proclamare v2.8: aviso sonoro antes do anúncio + contas com perfis

Data: 2026-10-22. Pedido do Artur (dono do projeto, não técnico). Este documento descreve O QUE construir e
por quê; o COMO detalhado fica para o plano. Onde está escrito "decisão do Artur", foi ele quem decidiu; onde está
"escolha do desenho", foi decidido aqui e pode ser revisto.

São duas partes independentes. A parte A (aviso sonoro) é pequena e pode ir primeiro. A parte B (contas) muda a
autenticação inteira e é a maior mudança desde o PIN.

---

## Parte A: aviso sonoro antes do anúncio ("ding-dong" de aeroporto)

### Intenção
Antes de a voz falar, tocar um aviso curto de notas (como em aeroporto e shopping) para os clientes prestarem
atenção. Decisão do Artur: ter algumas opções para escolher.

### O que fazer
- **Opções** (escolha do desenho, nomes provisórios): `nenhum`, `ding_dong` (2 notas descendo), `aeroporto`
  (3 notas subindo), `suave` (4 notas), `sino` (1 nota longa com eco). Até 3 s cada.
- **Os sons são gerados pelo próprio código** (síntese de senoides com envelope, como WAV), nunca arquivos baixados
  ou gravações de terceiros: sem direito autoral e sem dependência nova. Podem ser gerados uma vez e guardados em
  cache em memória ou em `audios_temp`.
- **Quando toca**: em todo anúncio falado: o digitado, o das mensagens prontas e o tipo `anuncio` da Programação.
  NÃO toca antes de um áudio agendado (`audio`, como a Ave Maria) nem antes do "Testar caixas".
- **Ordem**: abaixar a música → aviso sonoro → fala → restaurar a música, tudo dentro do mesmo contexto de
  "música abaixada" e na thread de áudio única (`executar_no_audio`), nas duas rotas (`_anunciar` e
  `_anunciar_na_saida`, saída fixada em "Som da loja"). No online, o mp3 da fala continua sendo gerado ANTES de
  abaixar a música (como hoje); o aviso entra logo depois de abaixar.
- **Tetos**: os tetos atuais dos anúncios comuns (80 s/90 s) não podem ser estourados nem afrouxados por causa do
  aviso; ele conta dentro do tempo. O "parar" da Programação também corta o aviso.
- **Onde se escolhe**: no bloco "Voz", que passa a ser a configuração da loja guardada no servidor (ver B5).
  Um botão "Ouvir" toca só o aviso escolhido nas caixas (ação explícita da pessoa). Só gerente e ADM mudam.
- **Padrão de fábrica** (escolha do desenho): `aeroporto` ligado, para a novidade aparecer. Instalações que já existem
  também ganham o aviso ao atualizar.

### Não fazer
- Não enviar arquivos de aviso próprios pelo painel (pode vir depois).
- Não ter volume separado para o aviso (escolha do desenho: o aviso é gerado num nível parecido com o da fala).

---

## Parte B: contas com nome, função e perfil

### Intenção
Hoje há um único PIN para todos os aparelhos da rede e o PC das caixas é livre. Ninguém sabe QUEM fez um anúncio.
O Artur quer que cada pessoa tenha a própria conta, com nome e função registrados, para que um anúncio prejudicial
tenha autor conhecido. E quer que cada cargo só veja o que pode usar: o funcionário só anuncia; o gerente faz tudo.

### Como a pessoa usa (decisão do Artur)
A pessoa abre o link do Proclamare (celular ou computador), aparece a tela de entrar, ela digita a conta dela e cai
direto no que o perfil permite. Um fiscal de caixa vê só o anúncio e as mensagens prontas; não vê o Spotify, a
música nem a voz.

### B1. Perfis (decisão do Artur; detalhes marcados como escolha do desenho)

| Pode... | ADM | Gerente | Funcionário |
|---|---|---|---|
| Fazer anúncio digitado e usar mensagens prontas | sim | sim | sim |
| Editar mensagens prontas | sim | sim | não |
| Mudar voz, motor, velocidade e aviso sonoro da loja | sim | sim | não |
| Música (Spotify, YouTube, Minhas músicas, listas, atalhos) | sim | sim | não (nem vê) |
| Programação de horários | sim | sim | não (nem vê) |
| Ver o histórico de anúncios de todos (B4) | sim | sim | só os próprios (escolha do desenho) |
| Criar, editar, desativar contas de funcionário | sim | sim | não |
| Criar, editar, desativar contas de gerente e ADM | sim | não | não |
| Saídas de som, segurança/rede, credenciais e conexão do Spotify | sim | não (escolha do desenho) | não |
| Desligar o Proclamare (continua só no PC das caixas) | sim | sim | não |

- **ADM**: a primeira conta, criada por quem ativa o Proclamare (decisão do Artur). Tem de existir sempre pelo
  menos um ADM ativo: o sistema recusa desativar ou rebaixar o último.
- **Gerente**: tudo o que o aplicativo faz hoje, menos o que está marcado acima como só do ADM (decisão do Artur:
  "o gerente tem acesso a tudo"; a reserva do ADM foi proposta neste desenho e o Artur aprovou o plano).
- **Funcionário**: só o painel de anúncio. A interface esconde o resto E o servidor recusa (403) toda rota fora do
  perfil. Esconder só na tela não basta.

### B2. Conta
- Campos: **nome** (1..60, uma linha), **função** (texto livre 1..40, como "Fiscal de caixa"), **usuário** (login,
  3..30, minúsculas, números, ponto e sublinhado, único), **senha**, **perfil**, **ativa** (sim/não).
- Senha: guardada só como hash forte e lento (o mesmo tipo de proteção do PIN atual, PBKDF2 com sal). Mínimo
  (escolha do desenho): 8 caracteres. Nunca aparece em resposta, log ou auditoria.
- Contas não são apagadas, só **desativadas**, para o histórico continuar mostrando o nome de quem fez. Desativar,
  trocar a senha ou o perfil encerra na hora as sessões abertas daquela conta.
- Quem esqueceu a senha: um gerente ou ADM define uma senha nova. O último ADM recupera a conta no PC das caixas por
  linha de comando (como o `--definir-pin` de hoje).
- Até 50 contas (escolha do desenho).
- Guardadas fora do `config.json`, num arquivo próprio ao lado dele (como `listas.json`), nunca versionado nem
  copiado para testes.

### B3. Entrar, sessão e proteção
- A tela de entrar substitui a tela de PIN. Usuário + senha.
- A sessão (cookie assinado, HttpOnly, SameSite=Strict, como hoje) passa a carregar QUAL conta está logada. Mantém a
  renovação deslizante e o teto absoluto da sessão de hoje. Botão "Sair" sempre visível.
- Toda a proteção contra tentativa de senha que existe hoje para o PIN continua valendo: bloqueio crescente por IP,
  por faixa de rede, a trava diária e o cookie de aparelho conhecido. Escolha do desenho: soma-se um bloqueio por
  conta (muitas senhas erradas para o mesmo usuário travam aquele usuário por um tempo, sem revelar se o usuário
  existe).
- A mensagem de erro do login é sempre a mesma ("usuário ou senha incorretos"), exista ou não o usuário.
- Continuam valendo: rotas novas protegidas por padrão, inventário de rotas no docstring + `test_higiene.py`,
  Host/Origin/Content-Type, cabeçalhos de segurança, vigia de túnel.

### B4. Histórico de anúncios (o motivo de tudo)
- Cada anúncio (digitado, mensagem pronta ou "Tocar agora" de um horário) fica registrado com: data e hora, **nome,
  função e usuário** de quem fez, **o texto falado**, resultado (tocou, ocupado, erro) e se veio do PC das caixas
  ou da rede.
- Anúncios da Programação que tocam sozinhos no horário ficam com o autor = quem criou ou alterou aquele horário
  por último.
- **Isso muda uma regra atual**: hoje a auditoria nunca guarda o texto. A regra continua valendo para o
  `logs/auditoria.log`; o texto passa a ser guardado SÓ neste histórico novo, num arquivo próprio, com acesso
  restrito no Windows (como o `config.json`), lido só por gerente e ADM pelo painel.
- Ninguém apaga nem edita entradas do histórico pelo painel, nem o ADM: elas saem sozinhas depois de **90 dias**
  (escolha do desenho, alinhado ao log de auditoria e à LGPD).
- Tela nova para gerente e ADM: lista do histórico com busca por pessoa e por data.
- Os carimbos `criado_por`/`alterado_por` de horários, listas, atalhos e mensagens passam a guardar a conta (nome)
  em vez de só "local"/"remoto".
- `docs/GUIA.md` ganha um aviso de privacidade: o sistema guarda nome, função e o texto dos anúncios por 90 dias.

### B5. Voz da loja no servidor
Hoje motor, voz e velocidade são escolhidos em cada aparelho e enviados a cada anúncio. Com perfis, isso vira uma
**configuração única da loja** guardada no servidor (motor, voz, velocidade e aviso sonoro), mudada só por gerente e
ADM. O `/api/announce` passa a usar sempre a configuração da loja; campos de voz enviados pelo cliente são
ignorados ou recusados (o plano decide qual, desde que o funcionário não consiga burlar). A Programação continua
podendo ter voz própria por horário de anúncio, como hoje.

### B6. Passagem do PIN para as contas
- Ao atualizar, o Proclamare continua funcionando com o PIN até alguém criar a primeira conta ADM.
- A primeira conta ADM só pode ser criada no PC das caixas (requisição local), por uma tela "Criar conta de
  administrador" que aparece enquanto não houver nenhuma conta.
- Assim que o ADM existe, o PIN deixa de valer e as sessões de PIN abertas são encerradas.

### B6b. PC das caixas também exige login (decisão do Artur, 2026-10-22)
- Depois que existe o primeiro ADM, o PC das caixas (loopback) deixa de ser livre: para anunciar ou mexer em
  qualquer coisa, a pessoa entra com a PRÓPRIA conta, e o histórico mostra o nome dela, não "PC das caixas".
- Não existe "conta das caixas" compartilhada (seria a mesma brecha de hoje).
- A aba **Tocador da loja** e os horários agendados continuam funcionando SEM ninguém logado: as rotas de que o
  Tocador precisa (estado/heartbeat, fila de comandos, áudio das músicas) ficam liberadas só para o próprio PC das
  caixas, e só para isso; elas não permitem anunciar nem mudar configuração.
- PC compartilhado (escolha do desenho): no PC das caixas a sessão sai sozinha depois de 5 minutos sem uso, para a
  próxima pessoa não anunciar com o nome de quem esqueceu de sair. Nos aparelhos pessoais vale a sessão longa de hoje.
- A criação da primeira conta ADM e a recuperação por linha de comando continuam só no PC das caixas.

### B7. Decisões tomadas na revisão do desenho (escolhas do desenho, para o Artur conferir)
- **Rede x perfil**: o perfil passa a ser a regra principal. As proteções que dependem da ORIGEM continuam por cima
  para quem vem da rede: limites de taxa por IP, vigia de túnel, trava de login, tetos diários. A "revisão do que os
  aparelhos da rede fizeram" ao trocar o PIN (`desativar_remotos`, `/api/seguranca/remotos`, `programacao_remota`)
  é substituída por: desativar a conta (encerra as sessões) + histórico e carimbos por conta. O plano lista o que
  sai e ajusta os testes.
- **Só no PC das caixas, além do perfil**: continuam só locais remover música, abrir pasta, desligar e a conexão do
  Spotify (o OAuth já exige `127.0.0.1`). Criar e editar contas pode ser feito pela rede por gerente/ADM; a
  PRIMEIRA conta ADM só no PC das caixas.
- **Antes do primeiro ADM** (só PIN): a sessão de PIN vale como gerente e o PC das caixas segue livre, ou seja, o
  comportamento de hoje. A voz da
  loja no servidor (B5) entra junto com as contas: na atualização, a configuração atual vira a voz da loja.
- **Aviso sonoro nos horários**: anúncios agendados usam sempre o aviso da loja, sem escolha por horário.
- **Itens de antes da atualização**: horários, listas e mensagens antigos aparecem com autor "antes das contas"
  (PC das caixas ou aparelho da rede). Continuam funcionando, sem bloqueio; quem salvar de novo vira o autor.

### Não fazer (nesta versão)
- Aprovação de anúncio por um superior antes de tocar (fila de aprovação). "Liberação por cadeia" foi entendido como
  hierarquia de permissões: quem cria e o que cada um pode.
- Cadastro feito pela própria pessoa, recuperação de senha por e-mail, login com Google ou similar.
- Perfis personalizados além dos três.
- Mais de uma loja no mesmo Proclamare.

### Seria um fracasso se...
- Um funcionário conseguisse, por qualquer caminho (inclusive chamando a API direto), mexer na música, na voz ou na
  Programação.
- Um anúncio saísse sem autor registrado, ou alguém conseguisse apagar ou mudar o próprio rastro.
- O fiscal precisasse de mais de alguns segundos para anunciar no dia a dia.
- A aba Tocador da loja ou os horários agendados parassem de tocar por falta de alguém logado.
- A senha de alguém aparecesse em log, resposta ou arquivo de teste.

### Restrições do projeto que continuam valendo
CSP rígido (sem inline), tudo por `createElement`/`textContent`, testes nunca tocam som real nem usam as portas
8502/8599, `config.json` e arquivos de dados nunca versionados nem copiados para testes, Revisor + Red Team + QA antes
da entrega, contrato no docstring do `server.py` e `app.js` mudados juntos (vira contrato v2.8).

### Deixado para quem construir
Formato dos arquivos novos, nomes das rotas, desenho exato da tela de entrar e do histórico (seguindo o visual
Bento claro/escuro atual), a síntese exata das notas de cada aviso e a divisão em etapas.
