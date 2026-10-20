"""
Proclamare — servidor local (v2.7.1 "Segurança"; v2.7 "Salvar playlists"; v2.6 "Listas" e "Programação"; v2.5 "Mensagens e Desligar"; base v2.4.2 "Spotify", v2.4.1 "tempo da música", v2.4 "Minhas músicas", v2.3 saídas de som, v2.2.1 e v2.2 com PIN)
=========================================================================
Backend Flask que roda no computador ligado ao amplificador/caixas de som
da loja. Serve a interface (static/) e cuida de três coisas:

  POST /api/announce        — converte texto em voz e TOCA na saída de som
                               deste computador (bloqueia até terminar).
                               Só um anúncio por vez: se já houver um tocando,
                               responde 409 na hora (não enfileira).

  /spotify/* e /api/spotify/* — controla o Spotify do próprio computador da
                               loja via Spotify Connect (o app oficial do
                               Spotify, instalado e logado nesse PC, vira um
                               "dispositivo" controlável pela API). Por isso
                               qualquer computador da rede pode comandar a
                               música sem precisar manter nenhuma aba aberta
                               com login próprio.

  /api/auth/*                — acesso pela rede com PIN (v2.2): status, login,
                               logout, definir/trocar/remover o PIN e revogar
                               sessões. O computador da loja (loopback) nunca
                               pede PIN; sem PIN definido o acesso remoto à API
                               fica desligado.

  /api/youtube/*             — o YouTube não tem um protocolo equivalente ao
                               Spotify Connect, então uma aba precisa ficar
                               aberta TOCANDO no computador da loja; esta
                               rota é só um relé de comandos (tocar, pausar,
                               trocar, volume) para essa aba, para que outros
                               computadores também consigam controlá-la.
                               A aba tocadora recebe os comandos por
                               long-poll (latência quase instantânea).

Inventário de rotas (v2.7.1)
----------------------------
  Uma linha por rota e método: acesso | limite por IP | corpo. Acesso: "publica" = sem sessão; "pin" = PC da loja OU
  aparelho com sessão (PIN); "local" = SÓ o PC da loja (403 para qualquer aparelho remoto, mesmo com PIN). Toda rota
  nova nasce "pin" (before_request); só /api/status e /api/auth/* (e o callback do OAuth) são públicas. O teste
  tests/test_higiene.py compara esta tabela com app.url_map: rota sem linha aqui (ou linha sem rota) quebra a suíte.
  GET  /                                   publica | - | -
  GET  /static/<path:filename>             publica | - | -
  GET  /api/status                         publica | - | -
  GET  /api/auth/status                    publica | - | - (campos de ataque/segurança só para o PC local)
  POST /api/auth/login                     publica | 5 erros/5 min por IP (dobra até 24 h), 20 por /24, 300/dia no total; 10 acertos/min; local 30/5 min | {"pin"}
  POST /api/auth/logout                    publica | - | {}
  POST /api/auth/pin                       local | - | {"pin": str|null, + opções de revisão}
  POST /api/auth/revogar                   local | - | {opções de revisão}
  POST /api/auth/liberar                   local | - | {}
  POST /api/auth/alertas/visto             local | - | {}
  GET  /api/seguranca/remotos              local | - | -
  POST /api/seguranca/remotos/aprovar      local | - | {"tipo","id"} | {"todos":true}
  POST /api/sistema/desligar               local | - | {}
  POST /api/announce                       pin | 10/min por IP remoto | {"text","engine"?,"voice"?,"speed"?,"offline_voice_id"?}
  GET  /api/voices                         pin | - | -
  GET  /api/youtube/state                  pin | - | -
  POST /api/youtube/state                  pin | 60/10 s | heartbeat da aba Tocador (chaves conhecidas; outra = 400)
  GET  /api/youtube/commands               pin | 4 long-polls simultâneos | -
  POST /api/youtube/command                pin | 30/10 s (seek 10/4 s) | {"action","params"?}
  POST /api/youtube/queue                  pin | adições 20/10 s | {"op", ... conforme a op}
  GET  /api/musicas                        pin | - | -
  GET  /api/musicas/<music_id>/audio       pin | - | -
  GET  /api/musicas/<music_id>/capa        pin | 600/min | -
  POST /api/musicas/enviar                 pin | 12/min, 1 envio por vez | multipart (até 100 MB só local/sessão)
  POST /api/musicas/remover                local | - | {"id"}
  POST /api/musicas/abrir-pasta            local | - | {}
  POST /api/musicas/atualizar              pin | 6/min | {"aguardar"?}
  GET  /api/mensagens                      pin | - | -
  POST /api/mensagens                      pin | 20/min | {"versao","mensagens"} (60 KB)
  POST /api/mensagens/restaurar            pin | 20/min | {"versao"}
  GET  /api/listas                         pin | - | -
  POST /api/listas                         pin | 20/min | {"nome","itens"?|"musicas"?} (60 KB)
  POST /api/listas/da-fila                 pin | 20/min | {"nome","incluir_atual"?}
  GET  /api/listas/<lista_id>              pin | busca de títulos 60/min | -
  POST /api/listas/<lista_id>              pin | 20/min | {"versao","nome"?,"itens"?|"musicas"?} (60 KB)
  POST /api/listas/<lista_id>/apagar       pin | 20/min | {"versao"}
  POST /api/listas/<lista_id>/tocar        pin | 6/10 s | {"embaralhar"?}
  GET  /api/programacao                    pin | - | -
  POST /api/programacao                    pin | 20/min | {"versao","itens"} (60 KB)
  POST /api/programacao/<item_id>/testar   pin | 6/min | {}
  POST /api/programacao/parar              pin | 20/min | {}
  GET  /api/som/estado                     pin | - | -
  GET  /api/som/saidas                     pin | - | -
  POST /api/som/descobrir                  pin | 6/min | {"tipos"?}
  POST /api/som/saida                      pin | 30/min (campos do PC = local) | {"sid", ...}
  POST /api/som/testar                     pin | 3/min (10/10 min no total) | {"sid","abaixar_musica"?}
  POST /api/som/preferencias               pin | 30/min | merge parcial
  GET  /api/spotify/config                 pin | - | -
  POST /api/spotify/config                 local | - | {"client_id"?,"client_secret"?}
  POST /api/spotify/desconectar            local | - | {"apagar_credenciais"?}
  GET  /api/spotify/status                 pin | - | -
  GET  /api/spotify/devices                pin | - | -
  GET  /api/spotify/search                 pin | - | ?q=
  GET  /api/spotify/now-playing            pin | - | -
  POST /api/spotify/activate               pin | 20/10 s | {"device_id"}
  POST /api/spotify/play                   pin | 20/10 s | {"uri"|"context_uri"}
  POST /api/spotify/pause                  pin | 20/10 s | {}
  POST /api/spotify/next                   pin | 20/10 s | {}
  POST /api/spotify/previous               pin | 20/10 s | {}
  POST /api/spotify/volume                 pin | 20/10 s | {"value"}
  GET  /api/spotify/atalhos                pin | - | -
  POST /api/spotify/atalhos                pin | 20/min | {"nome","link"}
  POST /api/spotify/atalhos/<atalho_id>    pin | 20/min | {"versao","nome"?,"link"?}
  POST /api/spotify/atalhos/<atalho_id>/apagar  pin | 20/min | {"versao"}
  POST /api/spotify/atalhos/<atalho_id>/tocar   pin | 6/10 s | {}
  GET  /api/spotify/atual-contexto         pin | 20/10 s | -
  GET  /spotify/login                      pin | 5/min | - (redireciona; nunca devolve texto)
  GET  /spotify/callback                   publica | - | ?code=&state= (state de uso único)

Contrato v2.7.1 (correções da auditoria de segurança)
-----------------------------------------------------
  Só ACRESCENTA campos e rotas e endurece defesas; um corpo com chave que o painel nunca mandou agora é 400.
  Nada de texto, nome, título, link ou id vai para o log ou para a auditoria (só contagens e tokens curtos).
  A) Transporte (waitress; criar_servidor_waitress() é a MESMA função do main() e dos testes de integração)
  * P01: clear_untrusted_proxy_headers=False. Antes o waitress apagava X-Forwarded-For/Host/Proto e Forwarded
    antes do Flask e a regra "cabeçalho de proxy tira o status de local" não funcionava (um X-Forwarded-For
    bastava para virar "PC da loja"). Sem trusted_proxy nenhum deles é interpretado; remote_addr é o do socket.
    CABECALHOS_DE_PROXY = Via, Forwarded, X-Forwarded-For/Host/Server/Proto/Port/By, X-Real-IP, CF-Connecting-IP,
    True-Client-IP, Tailscale-User-Login. O filtro de corpo do waitress usa a MESMA lista.
  * P03: channel_timeout 60 s (o long-poll é de 25 s e conta como requisição em andamento; um envio lento de
    músicas só é cortado se ficar 60 s SEM chegar nenhum byte), connection_limit 400 (< 512 do select no Windows),
    no máximo 64 conexões simultâneas por IP remoto e 300 no total dos remotos; o loopback é isento e sempre
    sobra lugar para ele. A conexão que passa do teto é fechada antes de ler qualquer byte. Vale sempre (mesmo
    com PROCLAMARE_MUSICAS_ENVIO=0). Um aviso por minuto no log, sem IP, com os tetos (por aparelho, total dos
    remotos, geral).
  * P06: PROCLAMARE_CONFIAR_LOOPBACK passa a ter 3 modos: "1" (loopback sempre é o PC da loja), "0" (ninguém é) e
    "auto" (padrão): igual a "1" enquanto não houver encaminhamento de rede para a porta. A vigia "vigia-tunel"
    (na partida e a cada 60 s; só leitura) procura: entrada do `netsh interface portproxy` (registro) cujo destino é
    esta porta; processos cloudflared, ngrok, frpc, bore, chisel, socat, lt/localtunnel com a porta na linha de
    comando (a porta casa por limite de dígitos: 8502 sim, 18502 não); ssh/plink com -R/-L/-D, juntos (-fNR, -NL) ou
    como opção (-oRemoteForward=) e a porta. Só o NOME do processo é lido de todos; a linha de comando só dos
    suspeitos. Achou ("fortes") = no modo auto o loopback deixa de valer (todos precisam de PIN), COM OU SEM PIN
    configurado (sem PIN, manter a confiança seria fail-open: quem entra pelo túnel teria acesso total), e o log
    avisa UMA vez. Para o dono não ficar trancado: GET /api/auth/status (PÚBLICO) ganha "loopback_suspenso" (bool;
    só o booleano, nunca nome de processo) = endereço e Host de loopback, mas tratado como remoto por causa do
    túnel (junto de "pin_configurado", que já existia); e o 403 "pin_nao_configurado" desse estado ganha
    "codigo": "loopback_suspenso" e a mensagem MSG_LOOPBACK_SUSPENSO (fechar o encaminhamento, `python server.py
    --definir-pin`, ou PROCLAMARE_CONFIAR_LOOPBACK=1). A vigia
    DETECTA ALGUNS casos (portproxy, túneis conhecidos); não é garantia, por isso =0 continua sendo o seguro; processo de túnel sem a porta ("fracos") é só aviso no painel. Linha de
    comando de processo nunca é logada nem guardada. Tailscale serve e túneis que o detector não vê continuam
    exigindo PROCLAMARE_CONFIAR_LOOPBACK=0. Qualquer outra conta ou programa do PC continua sendo "o PC da loja"
    (risco aceito: o loopback é compartilhado por todas as sessões do Windows). O login "local" tem limite folgado
    (30 tentativas/5 min, 429).
  * P07 (revisto na rodada 4): três camadas, nenhuma derruba sessões abertas nem o PC da loja. (1) POR IP: 5 PINs
    errados -> bloqueio de 5 min que DOBRA a cada bloqueio seguido (10, 20 min ... até 24 h) e só desce um nível a
    cada 24 h sem bloqueio; o nível é gravado no config.json (seguranca.niveis, até 200 IPs) e volta depois de
    reiniciar. (2) POR FAIXA /24 (IPv4, também para ::ffff:a.b.c.d; /64 no IPv6): 20 erros em 10 min somando os IPs
    da faixa -> bloqueio FIXO de 10 min, SEM escalada (uma faixa é a Wi-Fi inteira); aparelhos com COOKIE DE
    APARELHO válido (veja abaixo; nunca o IP) ficam FORA da faixa (não contam e não são barrados por ela; seguem só o
    limite por IP). 429 da faixa: "codigo": "bloqueio_rede", "Muitas tentativas vindas desta rede". (3) TETO GERAL
    DIÁRIO: 300 erros em 24 h no total -> o login pela rede TRAVA para quem NÃO é conhecido (429 com "travado":
    true, "trava_manual": bool, "bloqueado_por_s" = o que falta para a trava automática expirar; null na manual). aparelhos CONHECIDOS
    continuam podendo tentar (um atacante com muitos IPs não derruba o login da equipe), mas com um ORÇAMENTO FIXO de
    5 erros por aparelho por trava (não renova; esgotou = 429 travado) e sujeitos ao limite/escalada POR IP. "Conhecido"
    é provado por um COOKIE DE APARELHO ("proclamare_aparelho": HMAC derivado da chave de sessão, amarrado à
    acesso_versao, HttpOnly, SameSite=Strict, Path=/api/auth, 180 dias DESLIZANTES), emitido no login certo, renovado no
    GET /api/auth/status de quem tem sessão válida (se faltar ou vencer em < 30 dias) e invalidado ao trocar
    o PIN ou revogar - NUNCA pelo IP (reaproveitável/forjável na LAN); ele só muda o limite de tentativas, não dá
    acesso a nada; o mesmo cookie é o que isenta da faixa /24. Erros de conhecidos entram no histórico mas NÃO
    prolongam a trava. A 1ª trava numa janela móvel de 7 dias EXPIRA sozinha 24 h depois; uma 2ª trava dentro de 7
    dias fica até o dono liberar no PC (fail-closed, "login_trava_manual": true); o histórico de travas (só datas,
    seguranca.travas, até 10) sobrevive a reinício. O dono pode liberar a qualquer momento; na partida a contagem e a trava são refeitas a partir de seguranca.falhas (reiniciar não
    devolve 300 tentativas; seguranca.liberado_em impede que erros anteriores a uma liberação religuem a trava).
    Logins CERTOS: no máximo 10/min por IP (429). POST /api/auth/liberar {"zerar_escalada"?: bool} (só local; {}
    continua válido) tira os bloqueios ativos e o trava SEM derrubar sessões nem trocar o PIN; mantém o nível de
    escalada por IP, a menos que {"zerar_escalada": true}. Trocar o PIN ou revogar as sessões zera tudo, inclusive
    os níveis no disco. No estado "loopback_suspenso" o 429 de login para 127.0.0.1 repete o aviso do túnel
    ("codigo": "loopback_suspenso" + MSG_LOOPBACK_SUSPENSO). O IP nunca vai para o log comum (só para a auditoria).
    Os PINs errados são gravados no config.json em LOTE (no máximo 1 gravação a cada 30 s e ao desligar; o painel
    conta também os pendentes em memória).
  B) Alertas de PIN e logins novos (P12) - GET /api/auth/status, só para o PC local, ganha
    "seguranca": {"alerta": bool, "falhas_24h": int, "falhas_novas": int, "bloqueio_global_s": int,
    "logins_novos": [{"em": ISO com fuso, "ip"}], "ultimos_logins": [{"em","ip","novo": bool}] (até 10, o mais novo
    primeiro), "visto_em": ISO|null, "tuneis": {"fortes": [nome], "fracos": [nome], "confianca_loopback": bool,
    "modo": "0"|"1"|"auto"}, "login_travado": bool, "bloqueios_ativos": int (IPs/redes bloqueados agora),
    "pendentes": {horarios,listas,atalhos,mensagens,musicas,som,criados_pela_rede,mudados_pela_rede,total},
    "pendentes_remotos": int (= pendentes.total)}. Os contadores ficam no config.json (chave "seguranca": falhas
    das últimas 24 h, últimos 20 logins, IPs conhecidos por 90 dias, "visto_ate" e "novos" = logins de IP novo ainda
    não vistos, à parte, até 100: 20 logins comuns não apagam o alerta; login certo de IP já conhecido há menos de
    1 h não regrava o arquivo) e SOBREVIVEM a reinício. "bloqueio_global_s" = segundos até a trava AUTOMÁTICA expirar (1..86400), 0 sem trava e null na trava manual;
    "login_trava_manual": bool.
    "alerta" liga com 5+ PINs errados desde o último "visto", login travado ou algum IP/rede bloqueado, login de IP
    nunca visto ainda não visto, ou encaminhamento de rede detectado. POST /api/auth/alertas/visto {} (só local) marca como visto.
    falhas_recentes e bloqueio_global_s (v2.2) continuam, agora persistidos.
  C) O que aparelhos remotos deixaram (P04)
  * Carimbos gravados SÓ pelo servidor: horários (programacao.json) "origem" (quem alterou o CONTEÚDO por último;
    ligar/desligar pelo PC não conta, ligar/desligar por um remoto conta), "criado_por" (imutável) e "alterado_em"
    (epoch); listas e atalhos do Spotify (listas.json) e mensagens prontas (config.json) "criado_por",
    "alterado_por" e "alterado_em". Registro anterior à v2.7.1 vale "local" (não há como saber). O cliente não pode mandar
    nenhum (400 nos horários; nas listas/atalhos/mensagens a chave é desconhecida). Os GETs continuam iguais, salvo
    os horários, que ganham "criado_por" e "alterado_em".
  * "Pendente" = último autor remoto: horário com origem "remoto" OU que toca uma lista/playlist do Spotify cujo
    último autor foi remoto ("motivo": "item" | "alvo"); lista, atalho e mensagem com alterado_por "remoto".
  * GET /api/seguranca/remotos (só local) -> {ok, pendentes:{horarios,listas,atalhos,mensagens,musicas,som,
    criados_pela_rede,mudados_pela_rede,total}, horarios:[{id,rotulo,tipo,hora,dias,data,ativo,motivo,alvo_nome,
    criado_por,alterado_por,alterado_em}], listas:[{id,nome,total,criado_por,alterado_por,alterado_em}],
    atalhos:[{id,nome,criado_por,...}], mensagens:[{id,titulo,criado_por,...}], musicas:[{id,titulo,enviada_em,
    criado_por}], som:{pendente,alterado_em}} (o texto das mensagens e dos anúncios não vem aqui). "criados_pela_rede"
    = o item NASCEU num aparelho remoto; "mudados_pela_rede" = é do dono mas um remoto mexeu no conteúdo (ou o horário
    do dono toca uma lista/playlist alterada por remoto, motivo "alvo"). "alvo_nome" = nome da lista/playlist que o
    horário toca (null nos outros tipos), o mesmo campo do GET /api/programacao. Músicas enviadas por remoto
    (config.json "musicas_origem": só id e epoch) e mudanças de preferências de som por remoto
    ("som_alterado_por") entram na revisão, mas NUNCA são apagadas pela rotina.
  * POST /api/seguranca/remotos/aprovar (só local) {"tipo":"horario|lista|atalho|mensagem|musica|som","id"} (som:
    id "preferencias") ou {"todos":true} -> {ok, aprovados, pendentes}: o dono assume o item (último autor vira
    "local"; criado_por não muda). Aprovar um horário com motivo "alvo" aprova a lista/playlist dele (aprovados >= 1).
    404 se o item não está pendente.
  * Rodada 4 - revisão: "pendentes" ganha "apagaveis_pela_rede" (quantas listas+atalhos+mensagens o remover_remotos
    realmente APAGA: criados por remoto E ainda com último autor remoto; horários criados pela rede são só desativados e
    músicas nunca são apagadas) e "removidos_pela_rede" (só números, informativo, fora do "total": quantos itens um
    aparelho REMOTO apagou - horários, listas, atalhos, mensagens -, por tipo em GET /api/seguranca/remotos
    "removidos"; zera em aprovar {"tipo":"removidos","id":"todos"} ou {"todos":true}). A EDIÇÃO DO DONO APROVA
    IMPLICITAMENTE: quem mexe no conteúdo no PC da loja passa a ser o último autor e o item sai da lista de pendentes
    e do remover_remotos (criado_por continua "remoto"). Saídas de som aprovadas por aparelho remoto
    (som.saidas[sid].aprovada_por == "remoto") entram no tipo "som" (pendentes.som = preferências + saídas;
    inventário "som": {pendente, alterado_em, saidas}); aprovar {"tipo":"som","id":"preferencias"} assume também as
    saídas (aprovada_por vira "local").
  * Rodada 4 - vigia de túneis: processo suspeito cuja linha de comando não pôde ser lida (acesso negado) conta como
    "fraco" (aviso); os nomes casam por PREFIXO (cloudflared*, ngrok*, frpc*, frps*, chisel*, bore*, devtunnel*, zrok*)
    ou exato (socat, ncat, lt, localtunnel, ssh, plink). A vigia olha na partida e a cada 60 s: um encaminhamento
    novo pode levar até 60 s para suspender a confiança no loopback (e, ao ser fechado, até 60 s para voltar).
    MSG_LOOPBACK_SUSPENSO: (1) feche o encaminhamento; (2) para definir PIN desligue o Proclamare, rode
    .venv\\Scripts\\python.exe server.py --definir-pin e ligue de novo; (3) PROCLAMARE_CONFIAR_LOOPBACK=1 só com o
    alerta de que quem entrar pelo encaminhamento terá acesso total sem PIN.
  * Os carimbos só mudam quando o CONTEÚDO muda: salvar uma lista/atalho/mensagem igual não marca ninguém. O teto
    de PROG_REMOTO_LISTAS_DIA_MAX (10 por dia) também conta horários ativos que tocam lista/playlist ALTERADA por
    remoto, e um remoto que edita o conteúdo de uma lista/playlist agendada pode estourá-lo (400 "limite_diario").
  * Corrida conhecida: uma requisição remota que já estava em andamento quando o PIN mudou ou as sessões foram
    revogadas pode terminar de gravar DEPOIS da revisão (janela de milissegundos). O que ela deixar aparece como
    pendente em GET /api/seguranca/remotos; a revisão pode ser repetida.
  * POST /api/auth/pin e POST /api/auth/revogar aceitam, além do que já tinham, as OPÇÕES DE REVISÃO (todas bool;
    chave desconhecida ou não-bool = 400, antes de gravar qualquer coisa): "desativar_remotos" (padrão TRUE:
    horários pendentes ficam inativos, saem da fila de execução e, se um está tocando, é interrompido),
    "remover_remotos" (padrão false: apaga SÓ mensagens, listas e atalhos CRIADOS por remoto, criado_por
    "remoto"; o que é do dono e foi só alterado por remoto fica pendente; mensagens: se não sobrar nenhuma volta à fábrica), "restaurar_mensagens" (padrão false: todas as mensagens voltam à fábrica),
    "limpar_fila" (padrão false: esvazia a fila do Tocador). A resposta ganha "revisao": {horarios_desativados,
    horarios_cancelados, parou_item, fila_limpa, mensagens_removidas, listas_removidas, atalhos_removidos,
    mensagens_restauradas, itens_removidos} (ou {"falhou": true} se a revisão deu erro; a troca/revogação vale).
    "programacao_remota" agora conta TODOS os horários pendentes (também os desativados e os que tocam algo
    alterado por remoto). O painel mostra a confirmação ANTES de chamar.
  * P13: um horário que já venceu e espera na fila é relido pelo id antes de tocar: apagado ou desativado nesse
    meio-tempo = "não tocou" com motivo "removido" (uma edição vale). POST /api/programacao, a troca do PIN e a
    revogação tiram da fila os pedidos dos horários removidos/desativados (cancelar_fila).
  * P14: o teto de 60 min por dia da semana (400 "limite_diario") soma áudio + estimativa de fala dos anúncios
    (len/7 s, no máximo 80 s cada). Dois tetos novos, mesmo código e mesma regra de só recusar o que PIORA um dia:
    no máximo 72 horários ativos no mesmo dia da semana e no máximo 10 listas/playlists do Spotify ativas
    criadas/alteradas por aparelho REMOTO por dia. Programação já salva acima do limite continua valendo.
  D) Auditoria e logs (P05, P22)
  * Arquivo próprio logs/auditoria.log (PROCLAMARE_AUDIT_FILE ou ao lado de PROCLAMARE_LOG_FILE), rotação por dia
    (90 dias), data ISO 8601 com fuso, fora do log comum; o log comum ganha a mesma data e o waitress só registra
    avisos. Linhas chave=valor: acao=, ip=, origem=local|remoto + campos. Ações: login (resultado=ok|pin_errado,
    bloqueado_s), pin (resultado=definido|removido), revogar, login_liberar, alertas_visto, remotos_aprovar,
    anuncio (caracteres=N resultado=ok|ocupado|limite|sem_internet|erro; nunca o texto), musicas_enviar
    (aceitas, recusadas), spotify_config (campos=N), spotify_desconectar, desligar, e AGREGADAS (a 1ª de cada
    minuto na hora + 1 linha de resumo quantidade=N) fila_musica, comando_musica e spotify_comando. As ações das
    v2.5-v2.7 (mensagens, listas, atalhos, programação, saídas de som) seguem como estavam.
  * POST /api/announce: no máximo 10 por minuto por IP REMOTO (429); o texto passa por programacao.validar_texto
    (sem controle/formato/bidi; 400 "Campo 'text' tem um caractere que não é permitido."). O "now_playing" do heartbeat
    tem controle/formato/substituto/uso privado REMOVIDOS (não recusados).
  E) Spotify (P11): POST /api/spotify/desconectar {"apagar_credenciais"?: bool=false} (só local) apaga o refresh
    token, o access token e o aparelho escolhido; com apagar_credenciais apaga também Client ID e Secret.
    -> {ok}. Não revoga o app no Spotify (o dono remove em spotify.com/account/apps; o painel orienta).
  F) Cabeçalhos (P16): Permissions-Policy (câmera, microfone, localização, USB... fechados; autoplay e
    encrypted-media delegados ao YouTube), Cross-Origin-Resource-Policy e Cross-Origin-Opener-Policy "same-origin".
    NÃO se usa COEP (quebraria o iframe do YouTube).
  G) Esquema estrito nas rotas antigas (P44): youtube/state, command, queue (por op), spotify/play, volume,
    activate e config com chave fora da lista = 400 "Campo desconhecido.". Em "params" do command, chaves extras
    seguem ignoradas e nunca repassadas.
  H) Dados no disco (P46): cópias "*.corrompido-*" são criadas vazias, recebem a ACL e só então os dados; ficam as 3
    mais novas de cada arquivo; temporários ".config.*.tmp"/".listas.*.tmp"/".programacao.*.tmp" com mais de 1 h
    são apagados na partida.

Contrato v2.7 ("Salvar playlists": listas mistas com YouTube + atalhos do Spotify)
----------------------------------------------------------------------------------
  Só ACRESCENTA campos, rotas e um tipo de horário; o que existia continua igual. Mesmas regras de sempre: PIN para
  aparelhos remotos, 415 (não JSON), 403 (Origin de outro host), 429 (limite por IP), esquema estrito (chave
  desconhecida = 400) e erros {"ok":false,"error",codigo?,campo?,indice?}. Nada de nome, título, link ou id vai para o
  log ou para a auditoria (só QUANTOS). Armazém único: o listas.json (programacao.ArquivoJson) guarda também os atalhos.
  A) Listas MISTAS (músicas da pasta + YouTube)
  * Item guardado (no array "musicas" da lista, o nome antigo da chave): {"tipo":"musica","id","titulo"} (entrada
    antiga sem "tipo" = música) | {"tipo":"youtube","video_id"|null,"list_id"|null,"index"|null,"start"|null,
    "titulo"|null}. Os campos valem pelas MESMAS regras do parse_youtube_url (video_id 11 caracteres, list_id 2..64
    sem Mix "RD...", index 0..199 só com list_id, start 0..86400); zero em index/start vira null; NUNCA se guarda a
    URL. Ao ler do disco o item torto/repetido é descartado (nunca derruba). Título guardado <= 60.
  * POST /api/listas {"nome","itens"?|"musicas"?} e POST /api/listas/<id> {"versao","nome"?,"itens"?|"musicas"?}.
    "itens" = a lista INTEIRA, na ordem, cada elemento com UMA chave: {"music_id"} | {"url": link do YouTube} |
    {"youtube": {video_id,list_id,index,start}} (o editor reenvia assim o que já estava na lista, sem reparsear;
    chaves nulas podem ser omitidas). "itens" e "musicas" juntos = 400 campo "itens". Até 500 itens; repetido (mesma
    música, ou mesmo video_id+list_id+index+start) = 400 com "indice" do segundo; link inválido = 400 campo "itens"
    com "indice" e mensagem em português. Corpo máx. 60 KB só nestas duas rotas (era 32 KB). NÃO são 96 KB: o waitress
    corta em 64 KB toda rota que não seja o envio de músicas (waitress_limites.py); 500 itens na forma "youtube"
    cabem em ~55 KB e 500 links curtos também; passou de 60 KB = 413. Música nova tem de estar na pasta (400; 409
    "lendo_pasta" se a pasta não foi lida), as que já estavam na lista continuam aceitas mesmo sumidas.
  * Título do YouTube: o guardado, senão o do cache da fila (o mesmo _oembed_buscar/_pedir_titulo, sem host novo);
    até resolver "titulo": null (o painel mostra "Vídeo do YouTube"/"Playlist do YouTube"). Os títulos que o cache
    já resolveu são gravados no listas.json SEM mexer em versao/atualizada_em, por um gravador com debounce: UMA
    escrita do arquivo inteiro a cada ~30 s no máximo (TITULOS_GRAVAR_INTERVALO_S), para todas as listas juntas,
    agendada quando o oEmbed resolve um título que alguma lista espera e quando um GET /api/listas/<id> encontra
    título nulo. O GET em si não grava. Limite leve (60/min por IP) só para esse trabalho extra (pedir títulos ao
    YouTube e agendar a gravação): passou dele a lista continua saindo (200), sem pedir nada; leitura nunca dá 429.
    Tamanho esperado do listas.json: ~100 bytes por item do YouTube com título; 50 listas x 500 itens chegam a
    ~5-6 MB (tudo é lido para a memória e regravado por inteiro a cada alteração).
  * GET /api/listas -> o resumo ganha "youtube" (quantos itens do YouTube); "total" conta tudo; "disponiveis" = músicas
    que existem + itens do YouTube (None enquanto a pasta não foi lida, salvo lista só com YouTube); "limites" ganha
    "itens": 500. GET /api/listas/<id>: cada item ganha "tipo"; música igual à v2.6 (+"tipo"); YouTube = {tipo:
    "youtube", kind:"video"|"playlist", video_id, list_id, index, start, titulo|null, disponivel:true, url} com "url"
    canônica montada pelo servidor (watch?v=..&list=..&index=N&t=Ns ou playlist?list=..).
  * tocar_lista(lista_id, embaralhar): a fila é substituída pelos itens na ordem (ou embaralhados), música sumida é
    pulada, YouTube entra como os itens da fila (kind video/playlist), teto 200; o 1º vai num "load". Só exige a pasta
    lida se houver música na lista. Player do YouTube da aba não carregado (yt_pronto false, como o play_now): os
    itens do YouTube são pulados ("pulados_youtube"); lista só com YouTube nessa situação = 409 "youtube_indisponivel".
    404 "sem_musicas" só se não sobrar nada tocável. Resposta: os campos de sempre + "youtube" (quantos itens do
    YouTube entraram) + "pulados_youtube". O título guardado aparece na fila (semeia o cache de títulos).
  * POST /api/listas/da-fila {"nome","incluir_atual"?:bool=true} -> 201 {ok, versao, lista, cortados, ignorados}:
    o SERVIDOR monta os itens (o cliente não manda nenhum; chave extra = 400): o que toca agora na aba Tocador (se
    incluir_atual, o tocador estiver vivo e houver um item atual de verdade: arquivo com music_id, ou vídeo/playlist
    do YouTube com o player carregado; parado conta como pausado, MENOS faixa terminada (parada com a posição
    recente a menos de 2 s do fim) e tocador ocioso; playlist em reprodução entra INTEIRA, vídeo avulso entra o
    vídeo, arquivo entra a música) + a fila na ordem. Repetidos e músicas que já saíram da pasta são ignorados ("ignorados"); passou
    de 500 corta ("cortados"). 409 "fila_vazia" | "limite_listas" | "nome_repetido" | "lendo_pasta"; 429. A fila não
    é mexida. Auditoria: acao=lista_da_fila, só quantos.
  B) Atalhos do Spotify (uma playlist ou álbum + um nome; tocam com um toque; entram na Programação)
  * Guardados no listas.json, chave "spotify": [{id (8 hex), nome (1..40, mesma regra do nome de lista, único sem
    diferenciar caixa), uri ("spotify:playlist:<22 base62>" | "spotify:album:<22 base62>"), versao, atualizada_em}],
    até 50, saneados ao ler; contador próprio "spotify_versao". O link colado nunca é guardado nem aberto.
  * GET  /api/spotify/atalhos -> {ok, versao, limites:{atalhos:50,nome:40}, atalhos:[{id,nome,tipo:"playlist"|"album",
        versao,atualizada_em,url}]} (url = https://open.spotify.com/<tipo>/<id>; a uri não sai do servidor).
  * POST /api/spotify/atalhos {"nome","link"} -> 201 {ok, versao, atalho}. "link" = https://open.spotify.com/
        (intl-xx/)?(playlist|album)/<22 base62>(?si=...) ou spotify:(playlist|album):<22 base62>; qualquer outra
        coisa = 400 campo "link" ("Cole o link de uma playlist ou álbum do Spotify."). O TIPO (playlist|album) e o
        "intl-xx" não diferenciam maiúsculas (guardados em minúsculas); o id de 22 base62 diferencia.
        409 "limite_atalhos" | "nome_repetido".
  * POST /api/spotify/atalhos/<id> {"versao","nome"?,"link"?} (pelo menos um) -> {ok, versao, atalho};
    POST /api/spotify/atalhos/<id>/apagar {"versao"} -> {ok, versao}. 409 "versao_mudou" por atalho; 404
    "atalho_inexistente".
  * POST /api/spotify/atalhos/<id>/tocar {} -> o PUT /me/player/play com context_uri no aparelho salvo (a lógica do
    /api/spotify/play, em tocar_atalho_spotify(atalho_id), sem contexto de requisição). Sucesso 200 {ok:true,
    tocador_pausado:bool}: se a aba Tocador estava tocando ela recebe "pause" (SÓ depois de o Spotify aceitar; se o
    Spotify falhar a aba continua tocando). Falha no formato v2.4.2: HTTP 200 {ok:false, error, codigo, espera_s?}
    com codigo premium | sem_dispositivo | reconectar | limite | indisponivel; nenhum aparelho escolhido = 400
    codigo "sem_dispositivo" (ou 200 "reconectar" se nem está conectado); atalho apagado = 404 "atalho_inexistente".
    429: o limite geral de comandos do Spotify (20/10 s) e 6 pedidos em 10 s por IP.
  * GET /api/spotify/atual-contexto -> {ok, uri_tipo:"playlist"|"album"|null, url|null, nome|null, tocando:bool}
    quando o Spotify está com uma playlist/álbum (para "Salvar a playlist que está tocando"); outro contexto ou nada
    = tudo null; falha do Spotify = {ok:false,error,codigo}. O nome vem de api.spotify.com (host fixo, id de 22
    base62 validado, cache de 10 min; falha = null e NÃO conta no "api_ok" do /api/spotify/status). 20 pedidos/10 s.
  * Auditoria: acao spotify_atalho_criar | spotify_atalho_salvar | spotify_atalho_apagar | spotify_atalho_tocar, IP,
    local/remoto e total (sempre quantos, nunca nome/uri/id). O tocar leva também resultado=ok | <codigo da falha>
    (premium, sem_dispositivo, reconectar, limite, indisponivel, atalho_inexistente), nunca texto livre.
    O log de falha de rede do Spotify nunca leva o caminho de uma consulta com id (/playlists/<id>).
  C) Programação: tipo novo "spotify" (alvo = id do atalho, 8 hex; "embaralhar"/voz/motor/velocidade = 400)
  * No horário chama tocar_atalho_spotify (sem a trava de áudio dos anúncios; a aba Tocador, se tocava, é pausada e
    NÃO é retomada: o Spotify a substitui). tocar_atalho_spotify e tocar_lista marcam "música substituída": um
    áudio/anúncio agendado que pausou a música antes (_musica_pausada) NÃO a devolve no finally (a aba não volta a
    tocar junto com o Spotify, nem o Spotify antigo sobrepõe a lista). Não entra no teto diário de 60 min de áudio. Falhou = "não tocou" com
    "motivo" estável: spotify_desconectado | spotify_sem_aparelho | spotify_premium | spotify_limite |
    spotify_indisponivel | atalho_inexistente | desligando.
  * Atalho que não existe: 400 "atalho_inexistente" (campo "alvo") ao criar/trocar o alvo; item que já existia e
    ficou sem atalho continua aceito (alvo_ok:false, alvo_motivo:"atalho_inexistente", rotulo "Spotify das HH:MM").
    "Tocar agora" de atalho apagado = 404 "atalho_inexistente"; os outros erros vão para o histórico (202).
  * Lista agendada com YouTube: o mesmo tocar_lista (não espera a pasta se só tem YouTube). Motivos novos do
    histórico: youtube_indisponivel (lista só com YouTube e o player da aba não carregou); "aviso" quando itens do
    YouTube foram pulados.

Contrato v2.6 etapa 1 (Listas de músicas criadas no painel)
-----------------------------------------------------------
  Só ACRESCENTA rotas; nada do que já existia muda. Todas ficam atrás do PIN (PC local é livre), com
  415 (não JSON), 403 (Origin de outro host) e 429 (limite por IP) como as demais. Erros no formato
  {"ok":false,"error",codigo?,campo?,indice?} (o mesmo do /api/mensagens). Esquema estrito: chave
  desconhecida = 400. A programação de horários vem logo abaixo (etapa 2).
  * Armazenamento: listas.json AO LADO do config.json (não dentro dele: 50 x 500 músicas passam de
    500 KB e o config é regravado a cada token/volume do Spotify). Gravação atômica + DACL restrita,
    cópia "listas.json.corrompido-*" se ilegível. Só ids opacos: lista_id = 8 hex; music_id = o do
    acervo (nunca caminho). Cada música guarda um título curto (60) só para mostrar se ela sumir.
  * Limites: 50 listas; nome 1..40 caracteres (uma linha, sem controle/formato/substituto/uso privado,
    único sem diferenciar maiúsculas); até 500 músicas por lista, sem repetição; corpo máx. 32 KB.
  * Versão: cada lista tem "versao" (1 ao criar, +1 a cada gravação); as rotas que mudam uma lista
    exigem a versão que o aparelho viu. 409 {"codigo":"versao_mudou"} se outro aparelho salvou antes.
    O GET geral traz também uma "versao" do arquivo (muda a cada gravação de qualquer lista).
  * GET  /api/listas        -> {ok, versao, limites:{listas,nome,musicas}, acervo_pronto, listas:[{id,
        nome, versao, atualizada_em, total, disponiveis}]}  ("disponiveis" = null até a pasta ser lida).
  * GET  /api/listas/<id>   -> {ok, limites, lista:{...resumo, musicas:[item]}}; item de música da pasta =
        o item de /api/musicas + "disponivel":true; sumida = {id, titulo (o guardado|null),
        "disponivel":false} ("disponivel":null enquanto a pasta não foi lida). 404 se não existe.
  * POST /api/listas {"nome","musicas"?:[music_id]} -> 201 {ok, versao, lista}. 409 "limite_listas" |
        "nome_repetido" | "lendo_pasta"; 400 com campo/indice (música que não está na pasta = 400).
  * POST /api/listas/<id> {"versao", "nome"?, "musicas"?} (pelo menos um dos dois; "musicas" = a lista
        INTEIRA, na ordem) -> {ok, versao, lista}. Música nova tem de estar na pasta (400); as que já
        estavam na lista continuam aceitas mesmo sumidas (indisponíveis).
  * POST /api/listas/<id>/apagar {"versao"} -> {ok, versao}. As músicas ficam na pasta.
  * POST /api/listas/<id>/tocar {"embaralhar"?:bool} = "Tocar pasta" com a lista (fila esvaziada, as
        disponíveis entram em ordem ou embaralhadas, a 1ª sai da fila e vai num "load" ao Tocador;
        indisponíveis são puladas) -> {ok, total, adicionados, indisponiveis, cortados (teto 200 da
        fila), queue_version, primeiro, id}. 409 "sem_tocador" (nenhuma aba Tocador viva; a fila NÃO
        é mexida) | "lendo_pasta"; 404 (lista) | 404 "sem_musicas" (nenhuma disponível); 429.
  * Auditoria ("proclamare.som.auditoria"): acao (lista_criar|lista_salvar|lista_apagar|lista_tocar),
    IP, local/remoto e QUANTAS músicas; nunca nome nem ids.
  * Internos que a etapa 2 reutiliza: tocar_lista(lista_id, embaralhar) (sem contexto de requisição,
    levanta ErroLista), lista_obter(lista_id), listas_ler(), _tocador_vivo().

Contrato v2.6 etapa 2 (Programação de horários)
-----------------------------------------------
  Só ACRESCENTA rotas e um 409; nada do que já existia muda. Módulo programacao.py (relógio, validação,
  histórico; sem áudio nem Flask) + a execução aqui no server.py (seção "Programação de horários").
  Todas as rotas ficam atrás do PIN (PC local e aparelhos com PIN podem editar), com 415 (não JSON),
  403 (Origin de outro host) e 429 (limite por IP). Erros no formato {"ok":false,"error",codigo?,campo?,
  indice?} (o mesmo do /api/mensagens). Esquema estrito: chave desconhecida em qualquer nível = 400.
  * Item {id, ativo, tipo, alvo, nome, hora, dias | data, embaralhar, voz, motor, velocidade}:
      tipo "audio"   alvo = music_id (id opaco do acervo; nunca caminho). SÓ mp3, ogg, wav e flac (o que o
                     pygame toca): m4a/aac/opus = 400 "formato_nao_suportado" com mensagem clara. Até
                     10 min (PROG_AUDIO_TETO_S): mais = 400 "audio_longo". Música que não existe = 400
                     "musica_inexistente" (409 "lendo_pasta" se a pasta ainda não foi lida).
      tipo "lista"   alvo = lista_id (8 hex); não existe = 400 "lista_inexistente". "embaralhar": bool
                     (só vale aqui; true em outro tipo = 400).
      tipo "anuncio" alvo = TEXTO 1..500 caracteres, mesma higienização das Mensagens prontas (sem
                     controle/formato/substituto/uso privado; CRLF vira LF). "voz" francisca|antonio,
                     "motor" auto|online|offline, "velocidade" -50..50: opcionais (padrão francisca/auto/0);
                     só valem em anúncio. Soma dos textos de anúncio <= 10.000 caracteres (o corpo do POST
                     fica abaixo dos 64 KB do waitress; emoji em todo texto pode estourar e dá 413).
      "nome" opcional, 1..40 caracteres, uma linha (vazio = sem nome); "ativo" bool (padrão true);
      "hora" "HH:MM" (relógio de parede do PC da loja); recorrência = EXATAMENTE UMA entre "dias"
      (lista de 0..6, sem repetição, ordenada; 0 = DOMINGO, 1 = segunda ... 6 = sábado, como o
      Date.getDay() do navegador) e "data" ("AAAA-MM-DD"; data que já passou = 400, salvo item que já a
      tinha). Um item que já existia (mesmo tipo e alvo) continua aceito mesmo que a música tenha sumido,
      tenha mudado para um formato que não toca ou passado de 10 min, a lista tenha sido apagada ou a data
      tenha passado (fica "alvo_ok": false e o histórico diz "não tocou"): regravar a lista inteira não
      pode falhar por OUTRO horário. Só criar ou ALTERAR o alvo exige que ele exista e seja válido.
      "origem" ("local" | "remoto"): SOMENTE LEITURA, o servidor grava de onde veio a última criação ou
      alteração do item (o cliente não pode mandar o campo: 400). O painel mostra "criado pelo celular".
  * Limite de áudio por dia: a soma das durações dos itens "audio" ATIVOS não passa de 60 min em nenhum
    dia da semana (áudio sem duração conta 10 min; data única conta no dia da semana dela): POST que
    PIORA um dia acima disso = 400 "limite_diario" (desativar ou apagar horários sempre passa).
    "limites" traz "audio_dia_max_s". Em POST /api/auth/pin e /api/auth/revogar (só locais) a resposta
    ganha "programacao_remota": quantos horários foram criados/alterados por aparelhos remotos (eles
    continuam valendo depois de trocar o PIN; o painel avisa para o dono revisar).
  * Armazenamento: programacao.json AO LADO do config.json (mesmo motivo das listas), gravação atômica +
    DACL restrita, cópia "programacao.json.corrompido-*" se ilegível, item torto descartado. O último
    instante visto vai em programacao.visto.json (arquivo minúsculo, a cada ~30 s e a cada execução).
    Os dois estão no .gitignore. Até 100 itens; versão ÚNICA do documento (+1 a cada gravação).
  * GET  /api/programacao -> {ok, versao, agora:{iso,data,hora:"HH:MM",dia_semana,fuso}, limites,
        acervo_pronto, tocador_vivo, itens:[item + rotulo, alvo_nome|null, alvo_ok:true|false|null,
        alvo_motivo:"sem_musica"|"formato"|"audio_longo"|"lista_inexistente"|null,
        proxima:ISO|null], proximas:[{id,quando,em_s,tipo,rotulo,hora}] (até 10, só ativos, calculadas
        no servidor), historico:[até 50, mais novo primeiro], tocando:{id,nome,tipo,origem,desde,
        interrompivel}|null}. "agora" é a hora do PC da loja (não a do aparelho que pede). "rotulo" =
        o nome, senão o título da música / nome da lista, senão "Anúncio das HH:MM".
  * POST /api/programacao {"versao","itens":[...]} SUBSTITUI a lista inteira (sem "id" = item novo, o
        servidor gera 8 hex; com "id" tem de existir agora) -> o mesmo documento do GET. Ordem: 400,
        429 (20 gravações por minuto por IP; um 400 não gasta cota), 409 {"codigo":"versao_mudou"}.
        Corpo máx. 60 KB. Todo 400 traz "campo" e, quando é de um item, "indice" (0-based).
  * POST /api/programacao/<id>/testar {} = "Tocar agora" (também de item inativo; mesmo pausar/retomar).
        202 {ok, execucao:{id,tipo,nome}} e toca em segundo plano (resultado no histórico). 404 (id /
        horário / música sumida "sem_musica"), 400, 429 (6 por minuto por IP), 409: "ocupado"
        ("Programação tocando: <nome>" ou "Já existe um anúncio tocando. Aguarde terminar."),
        "sem_tocador" (lista com a aba Tocador fechada), "desligando", "audio_longo"/"formato".
  * POST /api/programacao/parar {} interrompe o ÁUDIO/ANÚNCIO da programação que está tocando (sinal de
        parada lido pelo laço de reprodução na thread de áudio; a música é retomada normalmente).
        200 {ok, parando:true}; voz do Windows falando direto (SAPI, sem internet) não tem como ser
        cortada: 200 {ok, parando:false, aviso}; nada tocando: 409 "nada_tocando". 429 (20/min).
  * Relógio (thread "programacao", tick de 2 s, relógio LOCAL do PC): cada horário dispara UMA vez
    (dedupe por id + data do horário + "HH:MM", 3 dias). Tolerância de 60 s de atraso; passou disso =
    "perdido" no histórico. Servidor desligado no horário: ao ligar, o que passou da tolerância (até 24 h
    atrás) vira "perdido" (motivo "servidor_desligado") e o que ainda está dentro dela toca; religar logo
    depois de um disparo não repete (o instante visto é gravado no disparo). Relógio do PC para TRÁS (fim
    do horário de verão, ajuste manual): nada dispara de novo; para FRENTE (ou PC suspenso): o que foi
    saltado e passou da tolerância vira "perdido" (motivo "relogio"), nunca uma rajada. Itens que vencem
    entram numa fila e UMA thread ("programacao-exec") executa um por vez (dois horários no mesmo minuto
    tocam um depois do outro). A tolerância de 60 s vale para o INÍCIO: quem esperou na fila só é
    descartado ("atrasado") se passou mais de 60 s depois de o item anterior terminar (um áudio de 2 min
    não derruba o horário seguinte) ou se já passaria de 12 min do horário. Mais de 20 horários perdidos
    numa mesma varredura: 20 entram no histórico e uma linha "Mais N horários" resume o resto.
  * Execução. "audio" e "anuncio" tocam PELO CAMINHO DOS ANÚNCIOS: trava _anuncio_lock (um som por vez),
    thread de áudio única (executar_no_audio), saída fixada em "Som da loja" quando houver
    (motor de saídas) ou o mixer padrão; funcionam com o navegador fechado. Com um anúncio manual tocando
    no horário a execução espera até 60 s (tenta a cada ~1 s) e depois registra "não tocou: ocupado". O
    áudio tem TETO PRÓPRIO (até 10 min; o laço de reprodução usa min(duração x 1,15 + 10 s, 600 s) porque
    a duração de um mp3 VBR sem cabeçalho pode ser subestimada, e o executor espera isso + 20 s) SEM mexer nos tetos dos anúncios comuns (laço de 80 s, executor de 90 s); o
    arquivo da pasta de músicas nunca é apagado. O anúncio de texto usa _anunciar/_anunciar_na_saida de
    sempre, trocando o "abaixar a música" por PAUSAR e retomar. PAUSAR/RETOMAR (_musica_pausada): a aba
    Tocador (comando "pause" / "play", só se estava tocando e só se continua a MESMA faixa) e o Spotify
    (PUT pause / play no aparelho salvo, só se estava tocando; até 3 tentativas de play; "restriction
    violated" com o player já tocando vale como ok); tudo no `finally`, mesmo com erro ou parada. Cada
    problema na música vira "aviso" no histórico. O arquivo de áudio é conferido de novo na hora de tocar
    (como no streaming: dentro da pasta, arquivo comum, sem link) e toca pelo caminho real. "lista" =
    tocar_lista() (TROCA a música atual): espera a 1ª leitura da pasta (até 10 s) e, sem aba Tocador
    viva, até 15 s por ela (acabou de religar) e então registra "não tocou: Tocador da loja fechado"
    (motivo "tocador_fechado"). Se o Spotify estiver tocando, é PAUSADO e não volta (a lista o
    substitui); se a lista não chega a tocar, o Spotify é devolvido.
    Durante um áudio/anúncio agendado, POST /api/announce e /api/som/testar recebem 409 ("Programação
    tocando: <nome>" no /api/announce); POST /api/sistema/desligar também (a mensagem diz qual e que dá
    para Parar). Depois de um desligar aceito nada da programação toca ("desligando") e o relógio para
    junto com o servidor (grava o instante visto).
  * Histórico (memória, últimos 50, não sobrevive a reinício): {quando (ISO local), id, nome, tipo, hora,
    origem "agendado"|"manual", status "ok"|"nao_tocou"|"perdido"|"erro", motivo, texto, interrompido,
    aviso}. motivo: tocador_fechado, ocupado, atrasado, desligando, sem_musica, formato, audio_longo,
    lista_sem_musicas, lista_inexistente, lendo_pasta, sem_internet, sem_saida/ausente/em_uso_exclusivo/
    motor_contaminado (saída de som), motor_travado, falha_audio, falha (erro inesperado; o detalhe só
    no log: só o tipo da exceção, nunca caminho nem texto), servidor_desligado e relogio (perdido).
    "texto" é a frase em português para o painel.
  * Auditoria ("proclamare.som.auditoria"): acao (programacao_salvar|programacao_testar|programacao_parar),
    IP, local/remoto e QUANTOS itens; cada execução registra acao=programacao_executar com origem, tipo,
    status e motivo. Nome, texto do anúncio, ids e caminhos NUNCA vão para o log.

Contrato v2.5 (Mensagens prontas editáveis + Desligar pelo painel)
-----------------------------------------------------------------
  Só ACRESCENTA rotas e um campo; nada do que já existia muda. Todas as rotas abaixo ficam atrás do
  PIN (PC local é livre), com 415 (não JSON) e 403 (Origin de outro host) como as demais.
  * Mensagens prontas (os botões de atalho do painel). Ficam no config.json (chave "mensagens", lista
    de {id, titulo, texto}) com um contador "mensagens_versao"; sem a chave vale a lista de FÁBRICA
    (6 mensagens, constante MENSAGENS_DE_FABRICA, ids "fab-1".."fab-6"). Gravação atômica sob o lock
    do config (as outras chaves ficam intactas).
  * GET  /api/mensagens -> {ok, versao:int, de_fabrica:bool, mensagens:[{id, titulo, texto}]}.
  * POST /api/mensagens {"versao":int, "mensagens":[{"id"?, "titulo", "texto"}]} substitui a lista
    INTEIRA: 0..24 itens; titulo 1..40 e texto 1..500 caracteres (500 = o mesmo teto do /api/announce)
    depois do strip; título em uma linha; texto pode ter quebra de linha (CRLF vira LF);
    controle (Cc), formato (Cf, menos ZWJ/ZWNJ), substituto (Cs) e uso privado (Co) são recusados
    ("Cn", ainda não atribuído, passa: o Unicode do Python 3.10-3.13 varia). Esquema estrito: chave desconhecida em qualquer
    nível = 400. "id" opcional: se vier, tem de ser de uma mensagem que existe agora (senão 400) e
    não pode repetir; sem "id" o servidor gera um (8 hex) e ele não muda enquanto a mensagem existir.
    Ordem das respostas: 400, 429 (20 gravações por minuto por IP), 409. 409 {"ok":false,"error",
    "codigo":"versao_mudou"} quando "versao" não é a atual (outro aparelho salvou antes). Todo 400
    traz "campo" ("versao"|"mensagens"|"titulo"|"texto"|"id"|"chave") e, quando é de um item, "indice"
    (0-based). Resposta de sucesso: o mesmo formato do GET (com a versão nova). Corpo máx. 60 KB.
  * POST /api/mensagens/restaurar {"versao":int} volta à lista de fábrica (remove a chave "mensagens";
    a versão sobe). Mesmo formato de resposta, mesmos 400/409/429. Já estando na fábrica não grava.
  * Auditoria (logger "proclamare.som.auditoria"): acao, IP, local/remoto e QUANTAS mensagens; nunca o
    conteúdo.
  * POST /api/sistema/desligar {}   SÓ LOCAL (403 para qualquer aparelho remoto, mesmo com PIN).
    Responde 202 {"ok":true} e, ~0,5 s depois (thread "desligar-painel"), encerra o servidor de forma
    graciosa: o laço do waitress sai, a porta é fechada, SomService/vigia, varredura de músicas e as
    threads do Spotify/YouTube param, o arquivo de PID é removido e o processo termina (um vigia
    força a saída se algo prender por mais de 10 s). O PID só some DEPOIS de a porta fechar (um desligar
    seguido de ligar não encontra a porta ocupada). Loga "Desligado pelo painel (PC local)". Antes de
    responder, enfileira uma "pause" para o Tocador da loja de qualquer aba/aparelho (o Spotify NÃO é
    pausado: ele toca no aplicativo Spotify do PC e continua lá). Com um anúncio (ou teste de saída)
    tocando: 409 "Espere o anúncio terminar"; com envio de músicas em processamento: 409 "Há músicas
    sendo enviadas. Espere terminar." (um envio cujo corpo ainda está chegando pela rede não é visto
    e é cortado); nada é desligado. Depois de aceito, novos anúncios dão 409 e novos envios 503 (o lock
    de anúncio fica preso até o fim do processo). Um 2º pedido
    enquanto desliga repete o 202. Para ligar de novo: duplo clique em Proclamare.pyw (o pid file já
    sumiu, então o lançador sobe um servidor novo; "Desligar Proclamare" do Menu Iniciar responde
    "não está ligado", sem erro). Corpo: só {} (chave extra = 400).
  * GET /api/auth/status ganha "pode_desligar": true só para o PC local (o painel mostra o botão ⏻).
  * "Local" (PC da loja) agora exige também a AUSÊNCIA de qualquer cabeçalho de proxy (CABECALHOS_DE_PROXY: Via,
    Forwarded, X-Forwarded-*, X-Real-IP...; lista completa na v2.7.1), no Flask e no filtro de corpo do
    waitress: um proxy reverso no PC (ex.: Apache) que reescreve o Host não faz mais a rede inteira
    parecer local. O navegador nunca manda esses cabeçalhos. (Na v2.5 isto só valia para Via e X-Real-IP: o
    waitress apagava os outros antes do Flask. Corrigido na v2.7.1, item P01.)
  * Corpo JSON aninhado demais (RecursionError) = 400, sem traceback no log.

Contrato v2.4.2 (Spotify: erros com causa, reconexão e volume do anúncio)
------------------------------------------------------------------------
  Só ACRESCENTA campos/rotas de resposta; nenhuma rota nova. Regras do Spotify (2026): o dono do
  app em Development Mode precisa de Premium; até 5 usuários em User Management (fora da lista =
  403 em tudo); o refresh token expira ~6 meses após a autorização (400 invalid_grant: reautorizar);
  Transfer/Start Playback exigem Premium; 404 NO_ACTIVE_DEVICE é comum com o app desktop ocioso.
  * Código de erro ("codigo", lista FIXA) + "error" legível, em /devices, /now-playing, /search e
    nos comandos (play/pause/next/previous/volume), sempre com HTTP 200 e "ok": false (exceto o 400
    de "nenhum dispositivo selecionado"): "premium" (403) | "sem_dispositivo" (404/NO_ACTIVE_DEVICE)
    | "reconectar" (sem token/401) | "limite" (429, com "espera_s") | "indisponivel" (rede/5xx/timeout).
  * GET /api/spotify/status -> {"connected", "reason": "ok"|"reconectar"|"rede"|"sem_config"|
    "sem_token", "device_id", "api_ok": true|false|null, "api_codigo", "api_error",
    "volume_pendente"}. Falha de REDE na renovação mantém connected:true, reason "rede" (blip de
    internet não vira "Conecte o Spotify"). invalid_grant/invalid_client na renovação DESCARTA os
    tokens, grava spotify_auth_erro="reconectar" (limpo no próximo callback de sucesso) e para de
    tentar. "api_ok": último resultado conhecido da API (falha de comando persistente — premium,
    sem_dispositivo, reconectar — vale 10 min; transitória — limite, indisponivel — 60 s; de leitura 2 min).
  * GET /api/spotify/config ganha "configured" (Client ID e Secret presentes). POST: Client ID e
    Secret são limpos (espaços/aspas/invisíveis nas pontas) e validados como 32 hexadecimais; formato
    errado = 400 {"ok": false, "error": "Cole só o código (32 letras e números).", "campo"}.
  * GET /spotify/login nunca devolve texto: redireciona para "/?spotify=nocfg|local|rate".
    GET /spotify/callback: sucesso "/?spotify=connected"; falha "/?spotify=error&motivo=negado|state|
    credenciais|rede|redirect" (lista fixa). 200 sem refresh_token é erro.
  * GET /api/spotify/devices: cada aparelho ganha "is_restricted" e "supports_volume".
    POST /api/spotify/activate -> {"ok": true, "transferred": bool, "warning": str|null}. Nome e tipo
    do aparelho são guardados: num 404 "Device not found" o servidor relê os aparelhos, reencontra
    por nome/tipo e repete o comando uma vez.
  * now-playing: podcast usa show.name. Falha real (premium, limite...) = "ok": false + error/codigo
    (os campos de sempre seguem presentes, vazios).
  * Anúncio: duck só devolve o volume anterior se o PUT funcionou; o restore tenta até 4 vezes
    (pausa 1,5 s, ignora o backoff 429 mas espera o Retry-After se couber no orçamento, confirma pelo
    estado do player 1,2 s depois; 2 PUTs aceitos com a leitura ainda divergente = leitura defasada,
    vale como restaurado) numa thread própria e o
    anúncio espera no máximo 8 s por ele; se não confirmar, o volume vai para "spotify_volume_pendente"
    no config.json (reaplicado a cada 30 s por até 30 min, só se o aparelho ainda estiver no volume
    do duck, e no duck do próximo anúncio). POST /api/announce ganha "musica_aviso"
    ("musica_nao_restaurada"|"musica_nao_abaixada") e "musica_aviso_texto" quando aplicável.
    Pause e o volume do restore são comandos críticos (não esperam o backoff 429); o pause da
    ROTA fura o backoff no máximo 1 vez a cada 2 s. Um anúncio novo cancela o restore de fundo do
    anterior. Os comandos (play/pause/next/previous/volume/activate) têm limite de 20 por 10 s por
    IP (429 "Muitos comandos seguidos"). /activate de aparelho restrito não o grava como salvo.

Contrato v2.4.1 (tempo da música no "Tocando agora")
---------------------------------------------------
  Só ACRESCENTA campos/ação ao contrato v2.4; nada do que já existia muda. Todas as rotas
  continuam atrás do PIN, com 415 (não JSON) e 403 (Origin de outro host) como as demais.
  * POST /api/youtube/state (heartbeat da aba Tocador) aceita também:
      "posicao_s"  número >= 0 e <= 86400 (int ou float; arredondado a milissegundos);
      "duracao_s"  número > 0 e <= 86400, ou null (duração desconhecida / vídeo ao vivo).
    String, bool, NaN, Infinity, negativo, zero (só a duração) ou acima de 86400 = 400, e um
    heartbeat recusado não altera NADA do estado. Ausentes = o valor anterior é mantido, EXCETO
    quando o heartbeat troca de faixa (source/video_id/music_id diferentes): aí posição e
    duração antigas são descartadas, para o tempo de uma música nunca aparecer na outra.
  * GET /api/youtube/state devolve "posicao_s", "duracao_s" e "posicao_idade_ms": a posição como
    chegou + os ms desde o heartbeat que a trouxe (relógio MONOTÔNICO do servidor). O controle
    remoto interpola: exibida = posicao_s + (is_playing ? idade/1000 : 0), limitada a duracao_s.
    Os três vêm null se o tocador está offline, se a posição nunca chegou ou se ela tem mais de
    YOUTUBE_PLAYER_TIMEOUT (6 s) - a mesma régua de "tocador vivo"; dado velho não é exibido.
  * POST /api/youtube/command ganha a ação de cliente "seek" com params {"segundos": 0..86400}
    (número, validado; ausente/inválido = 400; chaves extras são ignoradas e NUNCA repassadas).
    A aba Tocador aplica no que estiver tocando e responde com um heartbeat; "duck"/"restore"
    continuam internos do servidor.
  * Limite de taxa em POST /api/youtube/command (429): 30 comandos / 10 s por aparelho e, para
    "seek", 10 / 4 s. Com o buffer de comandos cheio (50), o servidor descarta o mais antigo que
    NÃO seja "duck"/"restore": uma enxurrada nunca faz a música ficar alta (ou abaixada) por engano.

Contrato v2.4 ("Minhas músicas": arquivos de áudio na fila do Tocador da loja)
-----------------------------------------------------------------------------
  Módulo musicas.py (acervo, varredura, metadados, envio). O áudio NUNCA toca pelo pygame:
  quem toca é um <audio> da aba "Tocador da loja", na MESMA fila do YouTube.
  * Pasta: PROCLAMARE_MUSICAS_DIR ou <Músicas do Windows>/"Músicas do Proclamare" (criada se
    não existir; fica fora da pasta do programa). Varredura em thread própria
    ("musicas-varredura") a cada ~15 s + sob demanda; recursiva até 3 níveis; ignora ocultos,
    "_removidas", arquivos vazios, atalhos/symlinks/junctions; .mp3 .m4a .aac .ogg .opus .wav
    .flac; teto de 5000 arquivos ("Enviadas" é lida por último). Arquivo com data no futuro ou
    ainda sendo copiado só entra quando o tamanho se repete entre duas varreduras. Ids OPACOS
    (16 hex); o cliente nunca envia nem recebe caminho absoluto (exceção: "pasta_local" no
    GET /api/musicas, só para requisições locais). Tag ID3 patológica (milhares de quadros)
    não é lida (o mutagen é quadrático nela): o arquivo aparece só com o nome.
  * GET  /api/musicas?q=&pasta=&offset=&limit=(1..100)&ordem=(recentes|titulo)
         "q": PALAVRAS (todas precisam aparecer, qualquer ordem, sem acento/caixa).
         -> {ok, versao, total, total_acervo, pastas:[{nome,total}], itens:[{id, titulo, artista,
         album, duracao_s, pasta, formato, tem_capa, adicionada_em}], varrendo, truncado,
         metadados (false = falta o mutagen no PC da loja), envio:{max_arquivos,
         max_bytes_arquivo, max_bytes_total, formatos, habilitado}, pasta_local? (só local)}
  * GET  /api/musicas/<id>/audio   arquivo com Range (206); id inexistente/forjado = 404.
  * GET  /api/musicas/<id>/capa    imagem embutida (jpeg|png, ≤ 2 MB) ou 404. ETag sem caminho;
         If-None-Match válido = 304 sem abrir o arquivo; cache do navegador de 1 h.
  * POST /api/musicas/enviar       multipart/form-data (campo de arquivo: qualquer nome, ex.
         "arquivos"). ÚNICA exceção à regra "POST só JSON": exige Origin do mesmo host (ou
         Sec-Fetch-Site: same-origin), PIN (se remoto), até 20 arquivos de 100 MB cada, o corpo
         INTEIRO da requisição ≤ 100 MB (+4 MB de multipart; o painel divide em lotes por
         max_bytes_total), extensão + assinatura + mutagen. Resposta {ok, enviados:[itens],
         recusados:[{nome, motivo}]}; 200 se ao menos um entrou, senão 422 (413 cota da pasta -
         medida no DISCO, inclui "_removidas" - ou "Enviadas" cheia, 507 disco cheio, 503 sem o
         mutagen instalado). 429 com envio em andamento ou em excesso. PROCLAMARE_MUSICAS_ENVIO=0
         desliga a rota (403).
         TETO DE CORPO NO WAITRESS (waitress_limites.py): qualquer outra rota/pessoa fica em 64 KB;
         só quem é local ou traz cookie de sessão válido pode mandar o corpo grande desta rota. O
         waitress responde 413 pelos cabeçalhos, antes de guardar o corpo em disco.
  * POST /api/musicas/remover {"id"}   SÓ LOCAL (403): MOVE para "_removidas", não apaga.
  * POST /api/musicas/abrir-pasta {}   SÓ LOCAL (403): abre o Explorer na pasta.
  * POST /api/musicas/atualizar {}     pede varredura imediata (202; 429 em excesso). Com
         {"aguardar": true} espera (≤ 8 s) a varredura que começa DEPOIS do pedido terminar e
         responde 200 {ok, concluida, versao}.
  * Fila (/api/youtube/queue, teto 200): {"op":"add","music_id"} (alternativa a "url") cria item
    kind "arquivo" {id, kind, music_id, title, artista, duracao_s, tem_capa, added_at, video_id:
    null, list_id: null, index: null, start: null}; {"op":"add_many","music_ids":[≤200],
    "posicao":"fim"|"inicio","embaralhar"?,"substituir"?,"tirar_primeiro"?} -> {adicionados,
    ignorados, cortados, queue_version, primeiro?}. "substituir" esvazia a fila e insere TUDO sob o
    mesmo lock (se nenhuma música existir mais: 404 e a fila antiga fica); "tirar_primeiro" (só
    com "substituir") devolve o 1º item novo já fora da fila em "primeiro" - é o "Tocar pasta"
    numa chamada só. play_now/pop/heartbeat/GET state entendem kind "arquivo": o comando "load"
    leva SÓ {"music_id"} (validado; nunca caminho). play_now de música que sumiu = 404;
    play_now de item do YouTube com o player do YouTube da aba não carregado = 409 (o item fica).
  * POST /api/youtube/command "load" também aceita params {"music_id"}.
  * POST/GET /api/youtube/state ganham "source" ("youtube"|"arquivo") e "music_id"; o POST aceita
    "yt_pronto" (bool; a aba diz se o player do YouTube carregou - ausente = true); o GET devolve
    ainda "musica" (item público da música tocando ou null) e "yt_pronto".

Contrato v2.3 (F1: saídas de som)
---------------------------------
  Detalhes e motivos: docs/PLANO-SAIDAS-DE-SOM.md. Pacote som/ (modelo, local, motor, vigia).
  * Sem a chave "som" no config.json (ou sem "saida_anuncio") tudo funciona EXATAMENTE
    como na v2.2.1: pygame.mixer.init() padrão, voz offline direta pelo SAPI.
  * Todas as rotas /api/som/* exigem PIN para aparelhos remotos (nenhuma é pública).
    POST: JSON (415) e Origin do mesmo host (403); esquema estrito (chave desconhecida
    em qualquer nível = 400). Erros: {"ok": false, "error", "erro", "codigo"?}.
  * GET  /api/som/estado       tile: motor_ok, saida_anuncio, padrao_windows, alertas, preferencias...
  * GET  /api/som/saidas       lista de saídas (visão com lista branca; sem IDs internos)
  * POST /api/som/descobrir    {"tipos": ["local","spotify"]} -> 202 (varredura em 2º plano)
  * POST /api/som/saida        {"sid", aprovada?, apelido?, confirmada?, esquecer?,
                               usar_para_anuncios?, aceitar_nova_ref?, rack?}. Campos que
                               só valem no PC (BT, HDMI, fone) = 403; troca remota exige
                               /testar da MESMA sessão nos 5 min anteriores (412
                               "teste_necessario"); 409 com anúncio tocando. Auditoria e
                               alerta "saida_alterada_remoto" nas trocas remotas.
  * POST /api/som/testar       {"sid", "abaixar_musica"?} bipe + "Teste do Proclamare: ..."
                               (nunca o nome do sistema). 3/min por IP e 10/10 min no total
                               (429); 409 com anúncio/teste tocando; 503 com "codigo"
                               ausente | em_uso_exclusivo | motor_contaminado.
  * POST /api/som/preferencias merge parcial: se_indisponivel, abaixar.spotify_api/youtube,
                               teste, monitor.ativo/alerta_silencio_min/horario.
  * POST /api/announce         com "saida_anuncio": resolve e ABRE a saída antes de
                               abaixar a música; a música é restaurada sempre; fallback
                               conforme se_indisponivel; 503 com "codigo" sem saída. A
                               resposta ganha saidas_usadas, parcial, sinal_detectado
                               (sessão do próprio processo; null se não deu para medir) e,
                               no fallback, aviso. Voz offline: SAPI -> WAV -> mesmo motor.
  * Vigia de progresso: teto da reprodução min(duração + 3 s, 80 s) < TIMEOUT_AUDIO_S; se o
    executor de áudio for substituído, o motor fica "contaminado" (503 até reiniciar).
  * /api/som/zonas NÃO existe (F3); 409 tarefa_agendada_tocando e "forcar" são F2.

Contrato v2.2.1 (fila do YouTube)
---------------------------------
  * O servidor interpreta o link (parse_youtube_url): vídeo, playlist, índice
    (&index=, 1-based no link -> 0-based) e início (t=/start=: 90, 90s, 1m30s...).
    Mix automático (list=RD... ou start_radio) é ignorado e toca só o vídeo; link
    só de Mix é recusado (400). Link inválido -> 400 em português.
  * POST /api/youtube/command  "load" recebe {"url"} e repassa ao tocador SÓ
    {"video_id","list_id","index","start"} validados (nunca a URL crua). Nova ação
    "play_index" {"index": 0..199} (pula para um item da playlist em reprodução).
  * POST /api/youtube/queue    fila do Proclamare (máx. 200 desde a v2.4, SÓ em memória: some se o
    servidor reiniciar). {"op": "add","url"} | {"op":"remove","item_id"} |
    {"op":"clear"} | {"op":"move","item_id","to"} | {"op":"play_now","item_id"}
    (tira da fila e manda "load" ao tocador) | {"op":"pop"} (tira e devolve o 1º).
    Item: {id, kind, video_id, list_id, index, start, title, added_at}.
    Erros: 400 (op/link), 404 (item), 409 (fila cheia).
  * POST /api/youtube/state    (aba tocadora) aceita também video_id, list_id,
    playlist_ids (até 200) e playlist_index; valores inválidos -> 400.
  * GET /api/youtube/state     devolve também video_id, list_id, queue,
    queue_version e playlist {index, items:[{video_id,title}]} | null (até 15
    próximas). Títulos vêm do oEmbed público do YouTube, buscados em segundo plano
    (title=null até resolver; cache LRU ~500, falhas ~10 min).
  * Todas as rotas novas ficam atrás do PIN, como o resto de /api/*.

O que mudou na v2.2 (resumo)
----------------------------
  * Autenticação por PIN (6 a 8 dígitos) para aparelhos remotos: hash PBKDF2,
    cookie de sessão assinado (HMAC, 14 dias com renovação), limite de
    tentativas por IP e global (com escalada), revogação de sessões.
  * "Local" = loopback + Host de loopback + sem cabeçalho Via (proxy/túnel no
    PC da loja não vira "local"). PROCLAMARE_CONFIAR_LOOPBACK=0 desliga o "local".
  * CLI sem subir o servidor:  python server.py --definir-pin | --remover-pin
  * Lançador de duplo clique (Proclamare.pyw): log rotativo (PROCLAMARE_LOG_FILE)
    e arquivo de PID (PROCLAMARE_PID_FILE) para desligar o processo certo.
  * Porta de escuta exclusiva (SO_EXCLUSIVEADDRUSE no Windows): um 2º servidor
    na mesma porta falha em vez de roubar conexões.
  * config.json ilegível é preservado (cópia "corrompido") e, no Windows, grava
    com permissões só para o usuário atual, SYSTEM e Administradores.

Da v2.1 (continua valendo)
----------------------------
  * Configuração (config.json) em cache na memória, com escrita atômica.
  * Sessão HTTP única (pool de conexões/keep-alive) para a API do Spotify,
    com timeouts curtos, renovação de token sem corrida e tratamento de 429.
  * Estado do player do Spotify em cache compartilhado (~2 s): N clientes
    abertos geram no máximo ~1 chamada a cada 2 s à API do Spotify.
  * TODO o áudio (pygame + pyttsx3) roda em UMA thread dedicada, com o mixer
    inicializado uma única vez (e reaproveitado).
  * Anúncio: o mp3 é gerado ANTES de abaixar a música (menos tempo abaixada).
  * Relé do YouTube com deque + threading.Condition (long-poll).
  * Servidor de produção (waitress) quando instalado.
  * Cabeçalhos de segurança, lista branca de Host (anti DNS rebinding),
    checagem de Origin/Sec-Fetch-Site/Content-Type (CSRF),
    validação rigorosa de todos os campos e erros genéricos ao cliente.

Variáveis de ambiente (todas opcionais)
---------------------------------------
  (os nomes antigos ANUNCIADOR_* continuam valendo como alternativa; se os dois
   existirem, PROCLAMARE_* tem prioridade)
  PROCLAMARE_HOST        endereço de escuta (padrão 0.0.0.0)
  PROCLAMARE_PORT        porta de escuta (padrão 8502)
  PROCLAMARE_LOG_LEVEL   nível de log: DEBUG, INFO, WARNING... (padrão INFO)
  PROCLAMARE_LOG_FILE    caminho de um arquivo de log (com rotação: 5 MB, 3 antigos).
                         Sem ela, o log vai para o console. Datas ISO 8601 com fuso.
  PROCLAMARE_AUDIT_FILE  arquivo da trilha de auditoria (padrão: auditoria.log ao lado do
                         PROCLAMARE_LOG_FILE; rotação por dia, 90 dias). Sem log em arquivo, a
                         auditoria sai no console, junto do log comum.
  PROCLAMARE_PID_FILE    caminho de um arquivo JSON onde o servidor registra
                         {"pid", "porta", "criado_em", "executavel"} ao começar a
                         servir (usado pelo lançador para desligar o processo certo);
                         é removido ao encerrar normalmente.
  PROCLAMARE_SPOTIFY_REDIRECT
                         Redirect URI do Spotify (padrão
                         http://127.0.0.1:<porta>/spotify/callback). O Spotify só
                         aceita http para IP de loopback; fora disso use https://.
  PROCLAMARE_MUSICAS_DIR pasta das músicas (padrão: <Músicas do Windows>/Músicas do Proclamare)
  PROCLAMARE_MUSICAS_ENVIO
                         "0" desliga o envio de músicas pelo painel (e volta o limite de corpo
                         do servidor a 64 KB para todos). Padrão "1": o corpo grande só vale para
                         o PC da loja e para aparelhos com sessão (PIN), nesta rota.
  PROCLAMARE_ALLOWED_HOSTS
                         nomes de host extras aceitos no cabeçalho Host, separados
                         por vírgula (defesa contra DNS rebinding). IPs literais,
                         localhost e o nome deste computador já são aceitos.
  PROCLAMARE_CONFIAR_LOOPBACK
                         "auto" (padrão): conexões de loopback com Host 127.0.0.1/localhost/::1
                         e sem cabeçalho de proxy são "locais" (sem PIN), EXCETO enquanto a vigia
                         de túneis achar um encaminhamento para a porta (netsh portproxy,
                         cloudflared, ngrok, ssh -L/-R...): aí ninguém é local até ele sair.
                         "1": o loopback é SEMPRE o PC da loja (sem a vigia).
                         "0": ninguém é local — todos precisam de PIN e a troca do
                         PIN / config do Spotify pela web ficam indisponíveis (use
                         a CLI). OBRIGATÓRIO se houver proxy reverso (Caddy/nginx), túnel
                         (SSH, cloudflared, tailscale serve) ou redirecionamento de porta
                         (netsh portproxy, WSL) no PC da loja que a vigia não enxergue: todo
                         encaminhamento TCP faz qualquer aparelho se passar pelo PC da loja.

Veja docs/GUIA.md para instalação, execução, acesso remoto e PIN.
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import contextlib
import copy
import functools
import getpass
import hashlib
import hmac
import ipaddress
import logging
import logging.handlers
import mimetypes
import math
import random
import signal
import sys
import os
import re
import secrets
import shutil
import socket
import tempfile
import threading
import _thread
import time
import uuid
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturoTimeout
from contextlib import contextmanager
from datetime import date, datetime
from http.cookiejar import DefaultCookiePolicy
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, urlencode, urlsplit

import json

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, Response, has_request_context, jsonify, redirect, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.http import parse_cookie

# Subsistema "Saídas de som" (contrato v2.3). O pacote só importa stdlib na carga;
# pycaw/pygame entram de forma tardia e tolerante dentro dele.
import som as som_pkg
from som import modelo as som_modelo
from som.motor import frase_de_teste, gerar_bipe, texto_para_sapi

# Acervo "Minhas músicas" (contrato v2.4). O módulo só usa stdlib na carga (mutagen entra
# de forma tardia); nada toca o disco até a primeira consulta ou o main().
import musicas
import programacao
import waitress_limites

log = logging.getLogger("proclamare")
# Trilha de auditoria (quem fez o quê): logger próprio, com arquivo, rotação e fuso próprios quando há
# PROCLAMARE_LOG_FILE (veja _configurar_log). Só chave=valor com números e tokens curtos: NUNCA texto,
# nome, título, link, id nem segredo.
auditoria = logging.getLogger("proclamare.som.auditoria")


def _env(nome: str, padrao: str = "") -> str:
    """Lê PROCLAMARE_<nome>; se ausente, cai para o nome antigo ANUNCIADOR_<nome>."""
    # "or": variável vazia (comum em .env/systemd) também cai para o nome antigo
    valor = os.environ.get(f"PROCLAMARE_{nome}") or os.environ.get(f"ANUNCIADOR_{nome}")
    return padrao if valor is None else valor

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
AUDIO_DIR = APP_DIR / "audios_temp"
AUDIO_DIR.mkdir(exist_ok=True)
MAX_ARQUIVOS_TEMP = 20
INTERVALO_LIMPEZA_S = 3600.0  # a limpeza de mp3 órfãos é só uma "rede de segurança"
CONFIG_PATH = APP_DIR / "config.json"


_PORTA_INVALIDA = False


def _porta_do_ambiente() -> int:
    """Lê PROCLAMARE_PORT (1..65535); valor inválido volta ao padrão 8502."""
    bruto = _env("PORT", "8502").strip()
    try:
        porta = int(bruto)
    except ValueError:
        porta = 0
    if not 1 <= porta <= 65535:
        global _PORTA_INVALIDA
        _PORTA_INVALIDA = True  # o aviso é emitido em main(), depois de configurar o log
        return 8502
    return porta


PORT = _porta_do_ambiente()

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
mimetypes.add_type("font/woff2", ".woff2")   # o Windows nem sempre conhece: sem isto o nosniff bloquearia a fonte local
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024  # 16 KB: todos os corpos JSON são minúsculos
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = None  # estáticos: ETag + "no-cache" (revalida com 304)
app.json.sort_keys = False  # não gasta CPU ordenando chaves de cada resposta


# ---------------------------------------------------------------------------
# Erros de API, validação de entrada e cabeçalhos de segurança
# ---------------------------------------------------------------------------
class ErroApi(Exception):
    """Erro de validação/uso: a mensagem (já genérica) vai ao cliente."""

    def __init__(self, mensagem: str, status: int = 400) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


def _resp_erro(mensagem: str, status: int) -> tuple[Response, int]:
    return jsonify({"ok": False, "error": mensagem}), status


@app.errorhandler(ErroApi)
def _tratar_erro_api(e: ErroApi) -> tuple[Response, int]:
    return _resp_erro(e.mensagem, e.status)


@app.errorhandler(HTTPException)
def _tratar_http(e: HTTPException) -> Any:
    # Redirecionamentos (ex.: barra final) são "exceções" com código < 400.
    if e.code is None or e.code < 400:
        return e
    return _resp_erro("Requisição inválida." if e.code < 500 else "Erro interno.", e.code)


@app.errorhandler(Exception)
def _tratar_excecao(e: Exception) -> tuple[Response, int]:
    # Detalhe e stack trace só no log do servidor; o cliente recebe algo genérico.
    # %r: o path vem do cliente; repr escapa quebras de linha (evita linha forjada no log).
    log.exception("Erro não tratado em %r %r", request.method, request.path)
    return _resp_erro("Erro interno do servidor.", 500)


def corpo_json() -> dict:
    """Devolve o corpo JSON da requisição, que obrigatoriamente é um objeto."""
    try:
        dados = request.get_json(silent=True)
    except RecursionError:
        # JSON aninhado demais ("[[[[..."): o get_json(silent=True) só captura ValueError. Sem isto
        # viraria 500 com traceback no log a cada requisição (CWE-674/779).
        raise ErroApi("Corpo JSON inválido.") from None
    if not isinstance(dados, dict):
        raise ErroApi("Corpo JSON inválido.")
    return dados


def exigir_chaves(corpo: dict, permitidas: Any) -> None:
    """
    Esquema estrito (v2.7.1, P44): qualquer chave fora da lista branca = 400. Evita que um `update(body)`
    futuro vire atribuição em massa. As rotas v2.5+ já eram estritas; as antigas passam por aqui.
    """
    if set(corpo) - set(permitidas):
        raise ErroApi("Campo desconhecido.")


def validar_int(valor: Any, nome: str, minimo: int, maximo: int, *, limitar: bool = False) -> int:
    """
    Converte `valor` em int de forma segura (sem exceções soltas -> 500).
    Aceita int, float inteiro ou string de dígitos; bool é rejeitado.
    Fora do intervalo: limita (limitar=True) ou responde 400.
    """
    if isinstance(valor, bool):
        raise ErroApi(f"Campo '{nome}' inválido.")
    if isinstance(valor, int):
        numero = valor
    elif isinstance(valor, float) and valor.is_integer():
        numero = int(valor)
    elif isinstance(valor, str) and re.fullmatch(r"-?\d{1,18}", valor.strip()):
        numero = int(valor.strip())
    else:
        raise ErroApi(f"Campo '{nome}' inválido.")
    if limitar:
        return max(minimo, min(maximo, numero))
    if not minimo <= numero <= maximo:
        raise ErroApi(f"Campo '{nome}' fora do intervalo permitido.")
    return numero


def validar_str(valor: Any, nome: str, max_len: int, *, min_len: int = 0, strip: bool = True) -> str:
    """Garante que `valor` é string e respeita o tamanho (após strip, se pedido)."""
    if not isinstance(valor, str):
        raise ErroApi(f"Campo '{nome}' inválido.")
    if strip:
        valor = valor.strip()
    if not min_len <= len(valor) <= max_len:
        raise ErroApi(f"Campo '{nome}' com tamanho inválido.")
    return valor


SEGUNDOS_MAX = 86400  # v2.4.1: teto de posição/duração/seek (24 h)


def validar_segundos(valor: Any, nome: str, *, positivo: bool = False) -> float:
    """
    v2.4.1 — tempo de mídia em segundos: número JSON (int ou float), 0 <= valor <= 86400
    (`positivo=True`: > 0, para durações). Devolve float arredondado a milissegundos.
    Recusam-se (400): bool (que em Python é int), string, null, NaN/Infinity (o json do
    Python os aceita!), negativos e acima do teto. Sem exceções soltas (-> 500): um int
    gigante é comparado ANTES de virar float, então não há OverflowError.
    """
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ErroApi(f"Campo '{nome}' inválido.")
    if isinstance(valor, float) and not math.isfinite(valor):
        raise ErroApi(f"Campo '{nome}' inválido.")
    if valor < 0 or valor > SEGUNDOS_MAX or (positivo and valor <= 0):
        raise ErroApi(f"Campo '{nome}' fora do intervalo permitido.")
    arredondado = round(float(valor), 3) + 0.0   # "+ 0.0" troca -0.0 por 0.0
    if positivo and arredondado <= 0:             # 0.0004 s não é uma duração
        raise ErroApi(f"Campo '{nome}' fora do intervalo permitido.")
    return arredondado


CSP = (
    "default-src 'self'; "
    "script-src 'self' https://www.youtube.com https://s.ytimg.com; "
    "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
    "style-src 'self'; "
    "font-src 'self'; "
    "img-src 'self' data: https://i.scdn.co https://*.scdn.co "
    "https://*.spotifycdn.com https://i.ytimg.com; "
    "connect-src 'self'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'self'; object-src 'none'"
)
CABECALHOS_SEGURANCA = {
    "X-Content-Type-Options": "nosniff",
    # Com "same-origin" o iframe do YouTube (cross-origin) não recebe Referer e
    # o player mostra o erro 153; esta política envia só a ORIGEM (sem path/query).
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "SAMEORIGIN",
    "Content-Security-Policy": CSP,
    # v2.7.1 (P16): reforços baratos do navegador. Permissions-Policy fecha o que o painel nunca usa
    # (câmera, microfone, localização...) e DELEGA ao YouTube só o que o iframe do Tocador precisa
    # (autoplay e encrypted-media); o que não é nomeado aqui segue o padrão do navegador (tela cheia,
    # picture-in-picture, wake lock...). CORP/COOP "same-origin": outro site não embute o áudio/as
    # capas do painel nem mantém uma referência à janela dele (o painel não abre popups). NÃO se usa
    # COEP: ele quebraria o iframe do YouTube e as capas do Spotify.
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(), hid=(), "
        "bluetooth=(), midi=(), display-capture=(), "
        'autoplay=(self "https://www.youtube.com" "https://www.youtube-nocookie.com"), '
        'encrypted-media=(self "https://www.youtube.com" "https://www.youtube-nocookie.com")'
    ),
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
}
METODOS_ESCRITA = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# --- Lista branca de Host (defesa contra DNS rebinding) -----------------------
# O navegador envia "Host: <nome digitado>". Num ataque de rebinding o nome é do
# atacante (evil.com) mas resolve para o IP da loja; Origin e Host passam a bater,
# então comparar os dois NÃO basta. Aceitamos: IPs literais (rebinding sempre usa
# um nome), localhost, o nome deste computador e extras via variável de ambiente.
def _hosts_extras_do_ambiente() -> frozenset[str]:
    bruto = _env("ALLOWED_HOSTS", "")
    return frozenset(h.strip().lower() for h in bruto.split(",") if h.strip())


def _montar_hosts_permitidos() -> frozenset[str]:
    nomes = {"localhost"} | set(_hosts_extras_do_ambiente())
    with contextlib.suppress(OSError):
        nomes.add(socket.gethostname().lower())
    return frozenset(nomes)


HOSTS_PERMITIDOS = _montar_hosts_permitidos()


def host_permitido(host_header: str) -> bool:
    """True se o cabeçalho Host (com ou sem porta) é um nome/IP aceito."""
    try:
        nome = urlsplit("//" + host_header).hostname  # remove porta e colchetes de IPv6
    except ValueError:
        return False
    if not nome:
        return False
    if nome in HOSTS_PERMITIDOS:
        return True
    try:
        ipaddress.ip_address(nome.split("%", 1)[0])  # IP literal (v4 ou v6)
        return True
    except ValueError:
        return False


# --- Limite de taxa simples por IP (janela deslizante, em memória) -------------
_taxa_lock = threading.Lock()
_taxa_hist: dict[str, deque] = {}
_taxa_janela: dict[str, float] = {}      # chave -> janela (s) usada por ela
_TAXA_MAX_CHAVES = 512
_TAXA_GLOBAIS = frozenset({"testar:global", "oembed"})   # tetos da loja toda: nunca são descartados


def excedeu_limite(chave: str, maximo: int, janela_s: float) -> bool:
    """Registra uma ocorrência de `chave`; True se passou de `maximo` na janela."""
    agora = time.monotonic()
    with _taxa_lock:
        if len(_taxa_hist) >= _TAXA_MAX_CHAVES:  # evita crescer sem limite
            # Limpa pela janela de CADA chave (não pela da chamada atual) e nunca as globais.
            for k in [k for k, h in _taxa_hist.items()
                      if k not in _TAXA_GLOBAIS
                      and (not h or agora - h[-1] > _taxa_janela.get(k, janela_s))]:
                del _taxa_hist[k]
                _taxa_janela.pop(k, None)
            if len(_taxa_hist) >= _TAXA_MAX_CHAVES:
                # ainda cheio (inundação por muitos IPs): descarta as mais antigas, sem clear() geral
                antigas = sorted((k for k in _taxa_hist if k not in _TAXA_GLOBAIS),
                                 key=lambda k: _taxa_hist[k][-1] if _taxa_hist[k] else 0.0)
                for k in antigas[: _TAXA_MAX_CHAVES // 4]:
                    del _taxa_hist[k]
                    _taxa_janela.pop(k, None)
        hist = _taxa_hist.setdefault(chave, deque())
        _taxa_janela[chave] = max(janela_s, _taxa_janela.get(chave, 0.0))
        while hist and agora - hist[0] > janela_s:
            hist.popleft()
        if len(hist) >= maximo:
            return True
        hist.append(agora)
        return False


HOSTS_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def _modo_loopback() -> str:
    """
    PROCLAMARE_CONFIAR_LOOPBACK: "0" = ninguém é local; "1" = o loopback é sempre local; qualquer outro
    valor (ou ausente) = "auto" (v2.7.1, P06): local, EXCETO enquanto houver um encaminhador TCP
    apontando para esta porta (netsh portproxy, cloudflared, ngrok, ssh -L/-R...), que faria uma
    conexão de fora chegar por 127.0.0.1 e se passar pelo PC da loja.
    """
    valor = _env("CONFIAR_LOOPBACK", "auto").strip().lower()
    return valor if valor in ("0", "1") else "auto"


def _tunel_suspende_loopback() -> bool:
    """
    A vigia achou um encaminhamento FORTE E o modo é "auto"? Então o loopback deixa de valer como o PC da loja,
    COM OU SEM PIN (sem PIN, manter a confiança deixaria quem entra pelo túnel com acesso total: fail-open).
    O dono não fica trancado: o status público traz "loopback_suspenso" e o 403 "pin_nao_configurado" desse
    estado leva o codigo "loopback_suspenso" com a saída (fechar o túnel, `python server.py --definir-pin` ou
    PROCLAMARE_CONFIAR_LOOPBACK=1).
    """
    return bool(_modo_loopback() == "auto" and _tuneis_estado["fortes"])


def _confiar_loopback(ignorar_tunel: bool = False) -> bool:
    """O loopback vale como "PC da loja"? (veja _modo_loopback e a vigia de túneis)"""
    modo = _modo_loopback()
    if modo == "auto":
        return ignorar_tunel or not _tunel_suspende_loopback()
    return modo == "1"


# Cabeçalhos que só um proxy/túnel acrescenta (o navegador nunca manda). Qualquer um deles faz a
# requisição deixar de ser "local": um proxy reverso no PC (ex.: Apache mod_proxy) pode reescrever o
# Host para loopback sem acrescentar Via, e todo aparelho da rede passaria por "o próprio PC".
# v2.7.1 (P01): o waitress APAGAVA X-Forwarded-*/Forwarded antes do Flask (clear_untrusted_proxy_headers
# padrão); agora o create_server os preserva (sem trusted_proxy, nenhum é interpretado) e a lista cobre
# também Server/Proto/Port/By e os cabeçalhos de CDN/túnel.
CABECALHOS_DE_PROXY = (
    "Via", "Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Server", "X-Forwarded-Proto",
    "X-Forwarded-Port", "X-Forwarded-By", "X-Real-IP", "CF-Connecting-IP", "True-Client-IP",
    "Tailscale-User-Login",
)


# --- Vigia de túneis (P06) ----------------------------------------------------------------------
# Procura, SÓ POR LEITURA, encaminhadores que apontam para a porta do Proclamare. "fortes" = apontam
# para esta porta (no modo "auto" o loopback deixa de valer como PC da loja); "fracos" = há um processo
# de túnel, mas nada diz que é para esta porta (só aviso no painel local). Nunca se loga nem se
# guarda a linha de comando de nenhum processo.
_TUNEL_PREFIXOS = ("cloudflared", "ngrok", "frpc", "frps", "chisel", "bore", "devtunnel", "zrok")   # casa por PREFIXO do nome
_TUNEL_PROCESSOS = frozenset({"socat", "ncat", "lt", "localtunnel"})                                  # casa pelo nome exato
_TUNEL_PROCESSOS_SSH = frozenset({"ssh", "plink"})
# ssh/plink que ENCAMINHA porta: -R/-L/-D sozinhos, juntos com outras letras (-fNR, -NL, -R8502:host:80) ou como
# opção (-oRemoteForward=..., -oLocalForward=..., -oDynamicForward=...). "-o" comum (-oStrictHostKeyChecking) não conta.
_RE_SSH_ENCAMINHA = re.compile(r"^-(?!o)[A-Za-z]*[RLD]")
_RE_SSH_OPCAO_ENCAMINHA = re.compile(r"^(?:-o)?(?:Remote|Local|Dynamic)Forward", re.I)
TUNEL_VERIFICAR_S = 60.0
_tuneis_estado: dict[str, Any] = {"fortes": [], "fracos": [], "em": 0.0}


def _portproxy_para(porta: int) -> list[str]:
    """Entradas do `netsh interface portproxy` (registro, só leitura) cujo destino é a nossa porta."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    achados: list[str] = []
    for familia in ("v4tov4", "v4tov6", "v6tov4", "v6tov6"):
        chave = "SYSTEM\\CurrentControlSet\\Services\\PortProxy\\" + familia + "\\tcp"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, chave) as k:
                i = 0
                while True:
                    try:
                        _nome, valor, _tipo = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    if isinstance(valor, str) and valor.rsplit("/", 1)[-1].strip() == str(porta):
                        achados.append("portproxy")
        except OSError:
            continue
    return achados


def _processos_de_tunel(porta: int) -> tuple[list[str], list[str]]:
    """(fortes, fracos) entre os processos em execução (psutil; sem psutil = nada)."""
    try:
        import psutil
    except ImportError:
        return [], []
    fortes: list[str] = []
    fracos: list[str] = []
    cita = re.compile(r"(?<!\d)" + str(porta) + r"(?!\d)")   # 8502 sim; 18502 e 85021 não
    try:
        processos = list(psutil.process_iter(["name"]))   # só o NOME: a linha de comando é cara e só vale para os suspeitos
    except Exception:  # noqa: BLE001
        return [], []
    for proc in processos:
        try:
            nome = (proc.info.get("name") or "").lower()
        except Exception:  # noqa: BLE001
            continue
        base = nome[:-4] if nome.endswith(".exe") else nome
        canonico = next((p for p in _TUNEL_PREFIXOS if base.startswith(p)), None) or (
            base if base in _TUNEL_PROCESSOS or base in _TUNEL_PROCESSOS_SSH else None)
        if canonico is None:
            continue
        base = canonico
        try:
            argumentos = [a for a in (proc.cmdline() or []) if isinstance(a, str)]
        except Exception:  # noqa: BLE001 - AccessDenied / processo que sumiu: não dá para ver a porta -> só aviso
            fracos.append(base)
            continue
        cita_porta = any(cita.search(a) for a in argumentos)
        if base in _TUNEL_PROCESSOS_SSH:
            encaminha = any(_RE_SSH_ENCAMINHA.match(a) or _RE_SSH_OPCAO_ENCAMINHA.match(a) for a in argumentos)
            if encaminha and cita_porta:
                fortes.append(base)
            elif encaminha:
                fracos.append(base)
        elif cita_porta:
            fortes.append(base)
        else:
            fracos.append(base)
    return sorted(set(fortes)), sorted(set(fracos))


def detectar_tuneis(porta: int) -> dict[str, Any]:
    """{"fortes": [...], "fracos": [...]} com nomes genéricos (portproxy, cloudflared...), sem detalhes."""
    pf, pw = _processos_de_tunel(porta)
    fortes = sorted(set(_portproxy_para(porta) + pf))
    return {"fortes": fortes, "fracos": sorted(set(pw) - set(fortes))}


def _atualizar_tuneis() -> None:
    """Uma rodada da vigia: atualiza o estado e loga UMA vez cada mudança (nunca por rodada)."""
    achado = detectar_tuneis(PORT)
    anterior = list(_tuneis_estado["fortes"])
    _tuneis_estado.update(fortes=achado["fortes"], fracos=achado["fracos"], em=time.time())
    if achado["fortes"] == anterior:
        return
    if achado["fortes"] and _modo_loopback() == "auto":
        log.warning("Encaminhamento de rede para a porta do Proclamare detectado (%s): o acesso pelo "
                    "loopback deixou de valer como o PC da loja; todos precisam de PIN. (1) Feche o "
                    "encaminhamento (volta sozinho em até 1 min). (2) Para definir um PIN: desligue o Proclamare, "
                    "rode .venv\\Scripts\\python.exe server.py --definir-pin e ligue de novo. (3) "
                    "PROCLAMARE_CONFIAR_LOOPBACK=1 só se o encaminhamento for seu: quem entrar por ele terá "
                    "acesso total sem PIN.", ", ".join(achado["fortes"]))
    elif achado["fortes"]:
        log.warning("Encaminhamento de rede para a porta do Proclamare detectado (%s).",
                    ", ".join(achado["fortes"]))
    else:
        log.info("Nenhum encaminhamento de rede para a porta do Proclamare foi encontrado agora.")


def _laco_vigia_tuneis() -> None:
    while not _desligando.wait(TUNEL_VERIFICAR_S):
        try:
            _atualizar_tuneis()
        except Exception:  # noqa: BLE001 - a vigia nunca pode cair
            log.debug("Falha na vigia de túneis.", exc_info=True)


def _iniciar_vigia_tuneis() -> None:
    """Primeira checagem síncrona (antes de atender) + thread de fundo."""
    with contextlib.suppress(Exception):
        _atualizar_tuneis()
    threading.Thread(target=_laco_vigia_tuneis, name="vigia-tunel", daemon=True).start()


def _local_por(remote_addr: str, host: str, tem_via: bool, ignorar_tunel: bool = False) -> bool:
    """
    A regra "é o próprio computador" sobre os três dados que a definem (usada pelo Flask e pelo
    filtro de corpo do waitress, que ainda não tem um `request`): endereço de origem em
    loopback E cabeçalho Host de loopback E sem cabeçalho de proxy (`tem_via`: qualquer um de
    CABECALHOS_DE_PROXY, não só o Via).
    """
    if not _confiar_loopback(ignorar_tunel) or tem_via:
        return False
    try:
        if not ipaddress.ip_address(remote_addr or "").is_loopback:
            return False
        nome = urlsplit("//" + host).hostname
    except ValueError:
        return False
    return (nome or "").lower() in HOSTS_LOOPBACK


def _requisicao_local() -> bool:
    """
    True só se a requisição vem MESMO do próprio computador: endereço de origem
    em loopback E cabeçalho Host de loopback E sem cabeçalho Via. Um proxy, túnel
    ou "netsh portproxy" no PC da loja faz todo mundo chegar por loopback, mas
    normalmente com Host de rede/nome ou Via; sem isso o PIN seria contornado.
    """
    return _local_por(request.remote_addr or "", request.host,
                      any(h in request.headers for h in CABECALHOS_DE_PROXY))


# --- Trilha de auditoria (P05) ---------------------------------------------------------------------
_RE_FORA_DO_TOKEN = re.compile(r"[^A-Za-z0-9_.:\-]")
AUDITORIA_AGREGADO_JANELA_S = 60.0
_agregado_lock = threading.Lock()
_agregado: dict[tuple[str, str, str], int] = {}   # (acao, ip, origem) -> eventos já contados na janela


def _token_auditoria(valor: Any) -> str:
    """Valor seguro para uma linha chave=valor: sem espaço, aspas nem quebra de linha (anti log forging)."""
    return _RE_FORA_DO_TOKEN.sub("_", str(valor))[:40] or "-"


def _origem_da_requisicao() -> str:
    return "local" if _requisicao_local() else "remoto"


def _quem() -> tuple[str, str]:
    """(ip, origem) da requisição atual; ("-", "-") fora de uma requisição (threads de fundo)."""
    if not has_request_context():
        return "-", "-"
    return _token_auditoria(request.remote_addr or "?"), _origem_da_requisicao()


def auditar(acao: str, **campos: Any) -> None:
    """
    Uma linha chave=valor na trilha de auditoria: acao, ip, origem (local|remoto) e os `campos` (só
    números e tokens curtos como resultado=ok: NUNCA texto, nome, título, link, id nem segredo). Os
    valores passam por _token_auditoria. Campos com None são omitidos.
    """
    ip, origem = _quem()
    partes = [f"acao={_token_auditoria(acao)}", f"ip={ip}", f"origem={origem}"]
    partes += [f"{_token_auditoria(k)}={_token_auditoria(v)}" for k, v in campos.items() if v is not None]
    auditoria.info("%s", " ".join(partes))


def auditar_agregado(acao: str) -> None:
    """
    Para o que acontece o tempo todo (fila, comandos de música, play/volume do Spotify): a PRIMEIRA ação de
    cada (acao, ip, origem) numa janela de 60 s vai na hora e o resto vira UMA linha de resumo
    (quantidade=N) quando a janela fecha. Assim quem tem o PIN aparece no registro sem inundar o arquivo.
    """
    ip, origem = _quem()
    chave = (acao, ip, origem)
    with _agregado_lock:
        if chave in _agregado:
            _agregado[chave] += 1
            return
        _agregado[chave] = 0
    auditar(acao, quantidade=1, janela_s=int(AUDITORIA_AGREGADO_JANELA_S))
    timer = threading.Timer(AUDITORIA_AGREGADO_JANELA_S, _fechar_agregado, args=(chave,))
    timer.daemon = True
    timer.start()


def _fechar_agregado(chave: tuple[str, str, str]) -> None:
    with _agregado_lock:
        restante = _agregado.pop(chave, 0)
    if restante:
        acao, ip, origem = chave
        auditoria.info("acao=%s ip=%s origem=%s quantidade=%d janela_s=%d resumo=1", acao, ip, origem,
                       restante, int(AUDITORIA_AGREGADO_JANELA_S))


# Rotas que só entram na trilha de forma AGREGADA (quando respondem com sucesso e a requisição é de quem
# tem acesso): acao -> rota. Anúncio, envio de músicas, PIN, revogação etc. têm linha própria, por chamada.
AUDITORIA_ROTAS_AGREGADAS = {
    "/api/youtube/queue": "fila_musica", "/api/youtube/command": "comando_musica",
    "/api/spotify/play": "spotify_comando", "/api/spotify/pause": "spotify_comando",
    "/api/spotify/next": "spotify_comando", "/api/spotify/previous": "spotify_comando",
    "/api/spotify/volume": "spotify_comando", "/api/spotify/activate": "spotify_comando",
}


@app.before_request
def _verificar_requisicao() -> tuple[Response, int] | None:
    """
    Defesas baratas, nesta ordem: Host na lista branca (anti DNS rebinding);
    Origin igual ao host (CSRF); Sec-Fetch-Site cross-site bloqueado em /api/* e
    /spotify/login (GET "no-cors" não envia Origin); POST /api/* só JSON, com UMA exceção
    (v2.4): POST /api/musicas/enviar é multipart/form-data e passa por
    _verificar_envio_multipart (exige Origin do mesmo host ou Sec-Fetch-Site same-origin).
    """
    if not host_permitido(request.host):
        return _resp_erro("Host não permitido.", 421)
    origin = request.headers.get("Origin")
    if origin is not None:
        try:
            origem_ok = urlsplit(origin).netloc.lower() == request.host.lower()
        except ValueError:
            origem_ok = False
        if not origem_ok:
            return _resp_erro("Origem não permitida.", 403)
    if request.headers.get("Sec-Fetch-Site") == "cross-site" and (
        request.path.startswith("/api/") or request.path == "/spotify/login"
    ):
        return _resp_erro("Requisição entre sites não permitida.", 403)
    if request.method in METODOS_ESCRITA and request.path.startswith("/api/"):
        if request.path == ROTA_ENVIO_MUSICAS and request.method == "POST":
            # ÚNICA exceção à regra "POST só JSON" (v2.4): envio de músicas em multipart.
            erro = _verificar_envio_multipart()
            if erro is not None:
                return erro
        elif request.mimetype != "application/json":
            return _resp_erro("Content-Type deve ser application/json.", 415)
    # v2.2: depois de todas as checagens acima, /api/* (exceto as públicas)
    # exige PIN quando o cliente não está no próprio computador.
    return _exigir_sessao()


@app.after_request
def _aplicar_cabecalhos(resp: Response) -> Response:
    """Cabeçalhos de segurança em toda resposta; cache correto por tipo de rota."""
    for chave, valor in CABECALHOS_SEGURANCA.items():
        resp.headers[chave] = valor
    acao_agregada = AUDITORIA_ROTAS_AGREGADAS.get(request.path)
    if acao_agregada is not None and request.method == "POST" and resp.status_code < 400:
        auditar_agregado(acao_agregada)
    if request.path.startswith("/api/"):
        # Áudio das músicas: cache só do navegador, sempre revalidando (ETag/Range). Capa: a lista
        # pede uma por linha, então o navegador a guarda por 1 h (o ETag muda se o arquivo mudar).
        # O resto da API nunca é guardado.
        if request.endpoint == "musicas_capa" and resp.status_code < 400:
            resp.headers["Cache-Control"] = "private, max-age=3600"
        elif request.endpoint == "musicas_audio" and resp.status_code < 400:
            resp.headers["Cache-Control"] = "private, no-cache"
        else:
            resp.headers["Cache-Control"] = "no-store"
    elif request.endpoint in ("static", "home"):
        # ETag + no-cache: o navegador revalida e recebe 304 (barato) se não mudou.
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------------------------------------------------------------------------
# Configuração persistida (client id/secret do Spotify, tokens, dispositivo)
# Cache em memória + escrita atômica (arquivo temporário + os.replace).
# ---------------------------------------------------------------------------
_config_lock = threading.RLock()
_config_cache: dict = {}
_config_assinatura: tuple[int, int] | None = None  # (mtime_ns, tamanho) do arquivo em cache


def _assinatura_arquivo() -> tuple[int, int] | None:
    try:
        st = CONFIG_PATH.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


_corrompidos_copiados: set[tuple[int, int]] = set()  # (hash do conteúdo, tamanho)


def _preservar_config_corrompido() -> None:
    """
    Se o config.json existe mas não é um JSON-objeto válido, copia-o UMA vez para
    config.json.corrompido-<timestamp> (antes de qualquer gravação por cima).
    """
    if _assinatura_arquivo() is None:
        return
    try:
        dados = CONFIG_PATH.read_bytes()
        # chave inclui o conteúdo: duas corrupções seguidas de mesmo tamanho/mtime não se confundem
        assinatura = (hash(dados), len(dados))
        if assinatura in _corrompidos_copiados:
            return
        try:
            if isinstance(json.loads(dados.decode("utf-8")), dict):
                return  # está íntegro
        except ValueError:
            pass
        # P46: cria vazio, aplica a ACL e só então escreve; mantém só as 3 cópias mais novas
        destino = programacao.salvar_copia_protegida(CONFIG_PATH, dados, _restringir_acl_windows)
        _corrompidos_copiados.add(assinatura)
        log.error("config.json corrompido: cópia guardada em %s.", destino.name)
    except OSError as exc:
        log.error("Não foi possível copiar o config.json corrompido (%s).", type(exc).__name__)


def _restringir_acl_windows(caminho: Path) -> None:
    """
    (Windows, best-effort) Deixa o arquivo acessível só ao usuário atual, ao SYSTEM
    e aos Administradores, sem herdar permissões da pasta. Falhas só são logadas.
    """
    if os.name != "nt":
        return
    try:
        import ntsecuritycon as con  # type: ignore[import-not-found]
        import win32api  # type: ignore[import-not-found]
        import win32security  # type: ignore[import-not-found]

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), con.TOKEN_QUERY)
        sid_usuario = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        dacl = win32security.ACL()
        for sid in (
            sid_usuario,
            win32security.ConvertStringSidToSid("S-1-5-18"),      # SYSTEM
            win32security.ConvertStringSidToSid("S-1-5-32-544"),  # Administradores
        ):
            dacl.AddAccessAllowedAce(win32security.ACL_REVISION, con.FILE_ALL_ACCESS, sid)
        win32security.SetNamedSecurityInfo(
            str(caminho), win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, dacl, None,
        )
    except Exception as exc:  # noqa: BLE001  (pywin32 ausente, sem permissão...)
        log.warning("Não foi possível restringir as permissões de %s (%s).", caminho.name, type(exc).__name__)


def load_config() -> dict:
    """
    Devolve uma CÓPIA da configuração. Só relê o disco se o arquivo mudou
    (um os.stat por chamada é muito mais barato que ler + parsear o JSON).
    """
    global _config_cache, _config_assinatura
    with _config_lock:
        assinatura = _assinatura_arquivo()
        if assinatura != _config_assinatura:
            novo: dict = {}
            if assinatura is not None:
                try:
                    lido = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                    if not isinstance(lido, dict):
                        raise ValueError("config.json não é um objeto")
                    novo = lido
                except (ValueError, OSError):  # JSONDecodeError/UnicodeDecodeError são ValueError
                    # Arquivo ilegível: guarda uma cópia (uma vez), registra ERROR e
                    # MANTÉM o último dicionário válido em memória (não zera segredos).
                    _preservar_config_corrompido()
                    log.error(
                        "config.json ilegível; mantendo a última configuração válida em memória. "
                        "Uma cópia do arquivo foi guardada como config.json.corrompido-*."
                    )
                    _config_assinatura = assinatura
                    return dict(_config_cache)
            _config_cache = novo
            _config_assinatura = assinatura
        return dict(_config_cache)


def _gravar_config_atomico(cfg: dict) -> None:
    """Grava em arquivo temporário (0600) na mesma pasta e troca com os.replace."""
    global _config_cache, _config_assinatura
    _preservar_config_corrompido()  # nunca sobrescreve um arquivo ilegível sem guardar cópia
    fd, tmp_nome = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), prefix=".config.", suffix=".tmp")
    try:
        # DACL restrita ANTES de escrever os dados; o os.replace mantém a DACL do temporário.
        _restringir_acl_windows(Path(tmp_nome))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        # No Windows o replace pode falhar por instantes (antivírus/indexador).
        for tentativa in range(5):
            try:
                os.replace(tmp_nome, CONFIG_PATH)
                break
            except PermissionError:
                if tentativa == 4:
                    raise
                time.sleep(0.05)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_nome)
        raise
    with contextlib.suppress(OSError):  # no Windows o chmod é quase um no-op
        os.chmod(CONFIG_PATH, 0o600)
    _config_cache = dict(cfg)
    _config_assinatura = _assinatura_arquivo()


def save_config(cfg: dict) -> None:
    with _config_lock:
        _gravar_config_atomico(cfg)


def update_config(**changes: Any) -> dict:
    """Aplica alterações e grava só se algo realmente mudou."""
    with _config_lock:
        cfg = load_config()
        if any(cfg.get(k) != v for k, v in changes.items()):
            cfg.update(changes)
            _gravar_config_atomico(cfg)
        return cfg


# ---------------------------------------------------------------------------
# Autenticação por PIN (contrato v2.2)
# O computador da loja (local: veja _requisicao_local) NUNCA pede PIN. Aparelhos
# remotos precisam de uma sessão (cookie assinado com HMAC) obtida digitando o
# PIN. Sem PIN configurado, o acesso remoto à API fica desligado.
# config.json guarda: acesso_pin_hash, acesso_chave (segredo HMAC) e
# acesso_versao (incrementa ao trocar/remover o PIN ou ao revogar; derruba todas
# as sessões). Esses campos NUNCA são devolvidos em respostas nem vão para o log.
# ---------------------------------------------------------------------------
COOKIE_SESSAO = "proclamare_sessao"
SESSAO_DURACAO_S = 14 * 24 * 3600  # 14 dias
SESSAO_TETO_S = 90 * 24 * 3600  # teto absoluto desde o login (a renovação não passa disso)
SESSAO_RENOVAR_ABAIXO_S = 7 * 24 * 3600  # renovação deslizante: reemite se faltar menos que isto
PBKDF2_ITERACOES = 200_000
RE_PIN = re.compile(r"[0-9]{6,8}")
MSG_PIN_FORMATO = "PIN inválido: use de 6 a 8 números."
# Rotas /api/ abertas mesmo para aparelhos remotos sem sessão. /api/auth/pin e
# /api/auth/revogar ficam aqui só para o remoto receber o 403 "somente local" da
# própria rota (e não um 401 que sugira que o PIN resolveria).
API_PUBLICAS = frozenset({
    "/api/status", "/api/auth/status", "/api/auth/login", "/api/auth/logout",
    "/api/auth/pin", "/api/auth/revogar",
})

# Limite de tentativas de login (em memória, com limpeza; o que importa para a escalada também vai ao disco)
# v2.7.1 (P07, rodada 4): três camadas, nenhuma delas derruba as sessões abertas nem o PC da loja.
#   1. POR IP (a defesa principal): 5 PINs errados -> bloqueio de 5 min, que DOBRA a cada bloqueio seguido (10 min,
#      20 min ... até 24 h) e só desce um nível a cada 24 h sem bloqueio. O nível de cada IP é gravado no config.json
#      (seguranca.niveis, no máximo 200 IPs): reiniciar o servidor não devolve ao atacante os bloqueios curtos.
#   2. POR FAIXA /24 (IPv4; /64 no IPv6): 20 erros em 10 min somando os IPs da faixa -> bloqueio FIXO de 10 min, SEM
#      escalada (uma faixa é a Wi-Fi inteira: escalar deixaria um atacante derrubar o login da equipe por horas).
#      Aparelhos com COOKIE DE APARELHO válido (nunca o IP) FICAM FORA da faixa: não contam para ela nem são
#      barrados por ela; seguem só o limite por IP (camada 1).
#   3. TETO GERAL DIÁRIO: 300 erros em 24 h no total -> o login pela rede TRAVA para aparelhos DESCONHECIDOS.
#      "Conhecido" é provado por um COOKIE DE APARELHO assinado (emitido no login certo; nunca pelo IP, que é
#      reaproveitável e forjável na LAN). Aparelho conhecido ainda tem um ORÇAMENTO FIXO de 5 erros por trava (não
#      renova; esgotou = 429 travado como os outros) e segue o limite/escalada por IP. Erros de conhecidos entram no
#      histórico mas NÃO prolongam a trava. A 1ª trava numa janela móvel de 7 dias acaba sozinha 24 h depois;
#      uma 2ª trava dentro de 7 dias fica até o dono liberar no PC (fail-closed) - o histórico de travas (só datas)
#      é gravado e sobrevive a reinício. Liberar (POST /api/auth/liberar) destrava qualquer uma.
# "Liberar" zera os bloqueios e o trava, mas NÃO o nível de escalada por IP, a menos que peça {"zerar_escalada": true};
# trocar o PIN ou revogar zera tudo, inclusive os níveis. Logins CERTOS também têm limite: 10/min por IP.
LOGIN_IP_MAX_ERROS = 5
LOGIN_IP_BLOQUEIO_S = 300.0     # 1º bloqueio de um IP (e janela de contagem dos erros dele)
LOGIN_REDE_MAX_ERROS = 20
LOGIN_REDE_JANELA_S = 600.0
LOGIN_REDE_BLOQUEIO_S = 600.0   # bloqueio da FAIXA: fixo, nunca escala
LOGIN_BLOQUEIO_MAX_S = 24 * 3600.0
LOGIN_DECAI_S = 24 * 3600.0     # desce um nível de escalada a cada período sem bloqueio
LOGIN_DIA_MAX_ERROS = 300
LOGIN_TRAVA_ORCAMENTO = 5        # erros que um aparelho CONHECIDO (cookie válido) ainda pode dar durante uma trava
TRAVA_REINCIDENCIA_S = 7 * 24 * 3600.0   # 2ª trava dentro deste prazo: só o dono destrava
SEG_TRAVAS_MAX = 10              # histórico de travas gravado (só datas)
LOGIN_OK_MAX_POR_MIN = 10
LOGIN_LOCAL_MAX = 30  # tentativas de login por 5 min vindas do "PC da loja" (que nem precisa de PIN)
LOGIN_IP_MAX_CHAVES = 1024
FALHAS_JANELA_S = 24 * 3600.0
_login_lock = threading.Lock()
# chave ("ip:<ip>" | "rede:<prefixo>") -> {"t": deque[monotonic], "bloq": monotonic, "nivel": int, "fim": monotonic}
_login_ips: dict[str, dict[str, Any]] = {}
_login_dia: deque = deque()          # monotonic de cada PIN errado nas últimas 24 h (teto geral diário)
_login_travado = False               # True: teto diário estourou; desconhecidos barrados até expirar ou o dono liberar
_login_trava_ate = 0.0               # monotonic: a trava AUTOMÁTICA expira aqui (24 h depois do último erro que a causou)
_login_trava_manual = False          # True: 2ª trava em menos de 7 dias; não expira, só o dono libera no PC
_login_trava_orcamento: dict[str, int] = {}   # id do aparelho conhecido -> erros gastos NESTA trava (não renova)
_login_travas: list[float] = []      # epoch de cada trava (histórico, também gravado em seguranca.travas)
_login_falhas: deque = deque(maxlen=5000)  # time.time() de cada PIN errado (remoto), p/ o painel
_versao_vista: int | None = None

def _b64u(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).decode("ascii").rstrip("=")


def _b64u_dec(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _hash_pin(pin: str, salt: bytes, iteracoes: int = PBKDF2_ITERACOES) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iteracoes)


def _novo_hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    return f"pbkdf2_sha256${PBKDF2_ITERACOES}${_b64u(salt)}${_b64u(_hash_pin(pin, salt))}"


def _ler_hash_pin(armazenado: Any) -> tuple[int, bytes, bytes] | None:
    """Decodifica 'pbkdf2_sha256$it$salt$hash'; None se ausente/malformado."""
    if not isinstance(armazenado, str):
        return None
    partes = armazenado.split("$")
    if len(partes) != 4 or partes[0] != "pbkdf2_sha256":
        return None
    try:
        it = int(partes[1])
        salt, esperado = _b64u_dec(partes[2]), _b64u_dec(partes[3])
    except (ValueError, TypeError):
        return None
    if not 1 <= it <= 5_000_000 or not salt or not esperado:
        return None
    return it, salt, esperado


def _chave_acesso(cfg: dict) -> bytes | None:
    try:
        chave = _b64u_dec(cfg.get("acesso_chave", ""))
    except (ValueError, TypeError):
        return None
    return chave if len(chave) >= 16 else None


def pin_configurado(cfg: dict | None = None) -> bool:
    cfg = load_config() if cfg is None else cfg
    return _ler_hash_pin(cfg.get("acesso_pin_hash")) is not None and _chave_acesso(cfg) is not None


def _versao_acesso(cfg: dict) -> int:
    v = cfg.get("acesso_versao", 0)
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _assinar(chave: bytes, parte: str) -> str:
    return _b64u(hmac.new(chave, parte.encode("ascii"), hashlib.sha256).digest())


def _emitir_token(cfg: dict, iat: float | None = None) -> tuple[str, int]:
    """
    Emite o token e devolve (token, segundos_de_vida). `iat` = momento do login
    original (mantido nas renovações); exp = min(agora + 14 dias, iat + 90 dias).
    """
    chave = _chave_acesso(cfg)
    assert chave is not None
    agora = int(time.time())
    iat_i = agora if iat is None else int(iat)
    exp = min(agora + SESSAO_DURACAO_S, iat_i + SESSAO_TETO_S)
    carga = json.dumps({"exp": exp, "iat": iat_i, "v": _versao_acesso(cfg)}, separators=(",", ":"))
    parte = _b64u(carga.encode("utf-8"))
    return parte + "." + _assinar(chave, parte), max(0, exp - agora)


def _expiracao_token(token: str | None, cfg: dict) -> float | None:
    carga = _carga_token(token, cfg)
    return None if carga is None else carga[0]


def _carga_token(token: str | None, cfg: dict) -> tuple[float, float] | None:
    """
    Confere formato, assinatura (tempo constante), expiração, versão e o teto de
    90 dias; devolve (exp, iat). Tokens sem `iat` são inválidos.
    """
    if not token or len(token) > 400 or not pin_configurado(cfg):
        return None
    chave = _chave_acesso(cfg)
    partes = token.split(".")
    if chave is None or len(partes) != 2:
        return None
    try:
        if not hmac.compare_digest(partes[1].encode("ascii"), _assinar(chave, partes[0]).encode("ascii")):
            return None
        carga = json.loads(_b64u_dec(partes[0]))
    except (ValueError, TypeError, UnicodeError):
        return None
    if not isinstance(carga, dict):
        return None
    exp, iat, v = carga.get("exp"), carga.get("iat"), carga.get("v")
    for n in (exp, iat):
        if isinstance(n, bool) or not isinstance(n, (int, float)):
            return None
    agora = time.time()
    if agora >= exp or agora >= iat + SESSAO_TETO_S or iat > agora + 300:
        return None
    if not (isinstance(v, int) and not isinstance(v, bool) and v == _versao_acesso(cfg)):
        return None
    return float(exp), float(iat)


def _token_valido_sessao(token: str | None, cfg: dict) -> bool:
    return _expiracao_token(token, cfg) is not None


def _sessao_ok(cfg: dict | None = None) -> bool:
    """True se o cliente é local OU traz um cookie de sessão válido."""
    if _requisicao_local():
        return True
    cfg = load_config() if cfg is None else cfg
    return _token_valido_sessao(request.cookies.get(COOKIE_SESSAO), cfg)


def _exigir_sessao() -> tuple[Response, int] | None:
    """Chamado no before_request: bloqueia /api/* protegidas para remotos sem sessão."""
    if not request.path.startswith("/api/") or request.path in API_PUBLICAS:
        return None
    if _requisicao_local():
        return None
    cfg = load_config()
    if _token_valido_sessao(request.cookies.get(COOKIE_SESSAO), cfg):
        return None
    return _resp_sem_sessao(cfg)


MSG_LOOPBACK_SUSPENSO = (
    "O Proclamare detectou um encaminhamento de rede (túnel) para este computador e bloqueou o acesso sem PIN. "
    "(1) Feche o encaminhamento: o acesso volta sozinho em até 1 minuto. "
    "(2) Para definir um PIN: desligue o Proclamare (ícone ou Menu Iniciar, \"Desligar Proclamare\"), rode "
    ".venv\\Scripts\\python.exe server.py --definir-pin na pasta do programa e ligue de novo. "
    "(3) Só se você tem certeza de que o encaminhamento é seu: inicie com PROCLAMARE_CONFIAR_LOOPBACK=1 - "
    "ATENÇÃO: quem entrar pelo encaminhamento terá acesso total sem PIN.")


def _resp_sem_sessao(cfg: dict) -> tuple[Response, int]:
    if not pin_configurado(cfg):
        if _tunel_suspende_loopback() and _local_por(
                request.remote_addr or "", request.host,
                any(h in request.headers for h in CABECALHOS_DE_PROXY), ignorar_tunel=True):
            return jsonify({"ok": False, "auth": "pin_nao_configurado", "codigo": "loopback_suspenso",
                            "error": MSG_LOOPBACK_SUSPENSO}), 403
        return jsonify({
            "ok": False, "auth": "pin_nao_configurado",
            "error": "Acesso pela rede desativado. Defina um PIN no computador da loja.",
        }), 403
    return jsonify({"ok": False, "auth": "pin_requerido", "error": "Digite o PIN para continuar."}), 401


def _definir_cookie_sessao(resp: Response, token: str, max_age: int = SESSAO_DURACAO_S) -> None:
    resp.set_cookie(
        COOKIE_SESSAO, token, max_age=max_age,
        httponly=True, samesite="Strict", path="/",
    )


@app.after_request
def _renovar_sessao(resp: Response) -> Response:
    """
    Renovação deslizante: numa requisição /api/ remota com sessão válida, se faltar
    menos de 7 dias para expirar, reemite o cookie (mesmos atributos) por mais 14 dias.
    """
    if (
        request.path.startswith("/api/")
        and request.path not in ("/api/auth/login", "/api/auth/logout")
        and resp.status_code < 400
        and not _requisicao_local()
    ):
        cfg = load_config()
        carga = _carga_token(request.cookies.get(COOKIE_SESSAO), cfg)
        if carga is not None and carga[0] - time.time() < SESSAO_RENOVAR_ABAIXO_S:
            novo, vida = _emitir_token(cfg, iat=carga[1])
            if vida > carga[0] - time.time() + 60:  # no teto de 90 dias não há o que renovar
                _definir_cookie_sessao(resp, novo, vida)
    return resp


# --- PIN: validação e gravação (usadas pela API e pela CLI) -----------------------
# Sequências de teclado/padrões muito comuns (além das regras gerais de _pin_trivial)
PINS_TECLADO = frozenset({
    "112233", "147258", "159753", "102030", "246810", "000123", "258369", "147369",
    "741852", "852963", "963852", "789456", "321654", "135790",
})


def _pin_trivial(pin: str) -> bool:
    """
    PIN fácil de adivinhar: todos iguais; sequência crescente/decrescente (com
    volta 9->0, ex.: 123456, 654321, 789012); repetição de bloco de 1 a 3 dígitos
    (121212, 123123, 112112); espelho/palíndromo (123321, 112211).
    """
    if len(set(pin)) == 1:
        return True
    passos = {(int(b) - int(a)) % 10 for a, b in zip(pin, pin[1:])}
    if passos == {1} or passos == {9}:
        return True
    for k in (1, 2, 3):
        if k < len(pin) and pin == (pin[:k] * len(pin))[:len(pin)]:
            return True
    if pin == pin[::-1] or pin in PINS_TECLADO:
        return True
    # Grupos de 2 ou 3 posições em progressão: 112233 / 222333 (um dígito repetido por
    # grupo, em passo fixo) e 102030 / 121416 (números de 2 ou 3 dígitos em passo fixo).
    for k in (2, 3):
        if len(pin) % k or len(pin) // k < 3:
            continue
        grupos = [pin[i:i + k] for i in range(0, len(pin), k)]
        if all(len(set(g)) == 1 for g in grupos):
            nums = [int(g[0]) for g in grupos]
            if len({(b - a) % 10 for a, b in zip(nums, nums[1:])}) == 1:
                return True
        nums = [int(g) for g in grupos]
        if nums[1] != nums[0] and len({b - a for a, b in zip(nums, nums[1:])}) == 1:
            return True
    return False


def validar_pin_novo(pin: Any) -> str:
    """Valida o formato e rejeita PINs triviais (ErroApi com mensagem amigável)."""
    if not isinstance(pin, str) or not RE_PIN.fullmatch(pin):
        raise ErroApi(MSG_PIN_FORMATO)
    if _pin_trivial(pin):
        raise ErroApi(
            "PIN fácil demais de adivinhar. Evite números repetidos (111111), "
            "sequências (123456), blocos repetidos (121212) e espelhos (123321)."
        )
    return pin


def _marcar_versao_vista(cfg: dict) -> None:
    """
    Esta troca de acesso_versao foi FEITA POR AQUI (e zera o login logo depois): registra a versão como "já vista", senão o
    primeiro login seguinte acharia que a versão mudou "por fora" (CLI) e zeraria DE NOVO os erros e a trava acumulados
    no intervalo.
    """
    global _versao_vista
    _versao_vista = _versao_acesso(cfg)


def aplicar_pin(pin: str | None) -> bool:
    """
    Define/troca (str já validada) ou remove (None) o PIN: nova chave HMAC e
    acesso_versao + 1 (desconecta todos os aparelhos). Devolve pin_configurado.
    """
    novo_hash = _novo_hash_pin(pin) if pin is not None else None  # custo fora do lock
    with _config_lock:
        cfg = load_config()
        if novo_hash is None:
            cfg.pop("acesso_pin_hash", None)
            cfg.pop("acesso_chave", None)
        else:
            cfg["acesso_pin_hash"] = novo_hash
            cfg["acesso_chave"] = _b64u(secrets.token_bytes(32))
        cfg["acesso_versao"] = _versao_acesso(cfg) + 1
        _gravar_config_atomico(cfg)
        configurado = pin_configurado(cfg)
        _marcar_versao_vista(cfg)
    _login_resetar_global()
    return configurado


# --- Limite de tentativas ---------------------------------------------------------
# --- Cookie de APARELHO (rodada final) -----------------------------------------------------------
# "Aparelho conhecido" é provado por um cookie assinado, NÃO pelo IP (IP é reaproveitável/forjável na LAN). Emitido no
# login certo (só remoto), HttpOnly, SameSite=Strict, Path=/api/auth, 180 dias deslizantes, assinado com uma chave DERIVADA da chave
# de sessão e amarrado à acesso_versao: trocar o PIN (a chave gira) ou revogar invalida todos. NÃO dá acesso a nada:
# só muda o limite de tentativas de login (sai da faixa /24 e entra no orçamento fixo da trava diária).
COOKIE_APARELHO = "proclamare_aparelho"
APARELHO_DURACAO_S = 180 * 24 * 3600          # vida útil; renovado (deslizante) a cada GET /api/auth/status com sessão
APARELHO_RENOVAR_ABAIXO_S = 30 * 24 * 3600    # renova quando faltar menos que isto


def _chave_aparelho(cfg: dict) -> bytes | None:
    chave = _chave_acesso(cfg)
    return None if chave is None else hmac.new(chave, b"proclamare-aparelho-v1", hashlib.sha256).digest()


def _emitir_cookie_aparelho(cfg: dict, aparelho_id: str | None = None) -> str:
    """Token do aparelho: {id, exp, v}. Reaproveita o id de um cookie válido (a identidade do aparelho não muda)."""
    chave = _chave_aparelho(cfg)
    assert chave is not None
    carga = json.dumps({"id": aparelho_id or secrets.token_hex(8), "exp": int(time.time()) + APARELHO_DURACAO_S,
                        "v": _versao_acesso(cfg)}, separators=(",", ":"))
    parte = _b64u(carga.encode("utf-8"))
    return parte + "." + _assinar(chave, parte)


def _aparelho_valido(token: str | None, cfg: dict) -> str | None:
    """O id do aparelho se o cookie é legítimo (assinatura, validade, versão de acesso); senão None."""
    info = _aparelho_info(token, cfg)
    return None if info is None else info[0]


def _aparelho_info(token: str | None, cfg: dict) -> tuple[str, float] | None:
    """(id, exp) do cookie de aparelho legítimo (assinatura, validade, versão de acesso); senão None."""
    if not token or len(token) > 300 or not pin_configurado(cfg):
        return None
    chave = _chave_aparelho(cfg)
    partes = token.split(".")
    if chave is None or len(partes) != 2:
        return None
    try:
        if not hmac.compare_digest(partes[1].encode("ascii"), _assinar(chave, partes[0]).encode("ascii")):
            return None
        carga = json.loads(_b64u_dec(partes[0]))
    except (ValueError, TypeError, UnicodeError):
        return None
    if not isinstance(carga, dict):
        return None
    ident, exp, v = carga.get("id"), carga.get("exp"), carga.get("v")
    if (not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{16}", ident) or isinstance(exp, bool)
            or not isinstance(exp, (int, float)) or time.time() >= exp
            or not (isinstance(v, int) and not isinstance(v, bool) and v == _versao_acesso(cfg))):
        return None
    return ident, float(exp)


def _renovar_cookie_aparelho(resp: Response, cfg: dict) -> None:
    """
    Quem tem SESSÃO válida já provou o PIN: se o cookie de aparelho falta, venceu ou vence em menos de 30 dias,
    emite/renova (mantendo o id se o cookie atual ainda é válido). Cobre sessões abertas antes desta função
    existir e o teto de 90 dias da sessão (que antes derrubava o cookie junto). Chamado no GET /api/auth/status.
    """
    info = _aparelho_info(request.cookies.get(COOKIE_APARELHO), cfg)
    if info is None or info[1] - time.time() < APARELHO_RENOVAR_ABAIXO_S:
        _definir_cookie_aparelho(resp, _emitir_cookie_aparelho(cfg, info[0] if info else None))


def _definir_cookie_aparelho(resp: Response, token: str) -> None:
    resp.set_cookie(COOKIE_APARELHO, token, max_age=APARELHO_DURACAO_S, httponly=True, samesite="Strict",
                    path="/api/auth")


def _login_resetar_global() -> None:
    """
    Zera TUDO do login (bloqueios, contadores, níveis de escalada - também no disco -, a trava diária e o histórico
    de travas) em troca/remoção do PIN, revogação local ou mudança de acesso_versao por fora.
    """
    global _login_travado, _login_trava_ate, _login_trava_manual
    with _login_lock:
        _login_ips.clear()
        _login_dia.clear()
        _login_travado = False
        _login_trava_ate = 0.0
        _login_trava_manual = False
        _login_trava_orcamento.clear()
        _login_travas.clear()
    _seg_marcar_liberado(zerar_niveis=True, zerar_travas=True)


def _login_liberar(zerar_escalada: bool = False) -> None:
    """
    "Liberar login pela rede" (só o PC da loja): tira todos os bloqueios ativos e a trava diária (mesmo a manual).
    MANTÉM o nível de escalada de cada IP (salvo `zerar_escalada`) e o histórico de travas (uma nova trava em menos
    de 7 dias continua sendo manual). Nunca mexe nas sessões.
    """
    global _login_travado, _login_trava_ate, _login_trava_manual
    with _login_lock:
        for reg in _login_ips.values():
            reg["t"].clear()
            reg["bloq"] = 0.0
            if zerar_escalada:
                reg["nivel"] = 0
                reg["fim"] = 0.0
        _login_dia.clear()
        _login_travado = False
        _login_trava_ate = 0.0
        _login_trava_manual = False
        _login_trava_orcamento.clear()
    _seg_marcar_liberado(zerar_niveis=zerar_escalada)


def _sincronizar_versao(cfg: dict) -> None:
    """Se a versão de acesso mudou por fora (CLI), zera o bloqueio global."""
    global _versao_vista
    v = _versao_acesso(cfg)
    if _versao_vista is not None and v != _versao_vista:
        _login_resetar_global()
    _versao_vista = v


def _chave_rede(ip: str) -> str | None:
    """Faixa do IP para o limite por rede: /24 no IPv4, /64 no IPv6 (None se não for um IP). IPv4 mapeado em IPv6 (::ffff:a.b.c.d) conta como o IPv4."""
    try:
        endereco = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return None
    endereco = getattr(endereco, "ipv4_mapped", None) or endereco
    prefixo = 24 if endereco.version == 4 else 64
    return "rede:" + str(ipaddress.ip_network(f"{endereco}/{prefixo}", strict=False))


def _login_registro(chave: str) -> dict[str, Any]:
    return _login_ips.setdefault(chave, {"t": deque(), "bloq": 0.0, "nivel": 0, "fim": 0.0})


def _login_limpar(agora: float) -> None:
    """(com _login_lock) Descarta registros vencidos; limita o tamanho do dicionário; expira a trava AUTOMÁTICA."""
    global _login_travado
    while _login_dia and agora - _login_dia[0] > FALHAS_JANELA_S:
        _login_dia.popleft()
    if _login_travado and not _login_trava_manual and agora >= _login_trava_ate:   # 24 h desde o último erro que a causou
        _login_travado = False
        _login_dia.clear()
        _login_trava_orcamento.clear()
        log.info("A trava do login pela rede expirou (24 h desde o início da trava).")
    for chave in list(_login_ips):
        reg = _login_ips[chave]
        janela = LOGIN_IP_BLOQUEIO_S if chave.startswith("ip:") else LOGIN_REDE_JANELA_S
        t = reg["t"]
        while t and agora - t[0] > janela:
            t.popleft()
        esquecido = reg["nivel"] == 0 or (reg["fim"] and agora - reg["fim"] > LOGIN_DECAI_S * reg["nivel"])
        if not t and agora >= reg["bloq"] and esquecido:
            del _login_ips[chave]
    while len(_login_ips) >= LOGIN_IP_MAX_CHAVES:  # só sob ataque de muitos IPs
        del _login_ips[min(_login_ips, key=lambda k: (_login_ips[k]["bloq"], _login_ips[k]["nivel"]))]
    while _login_falhas and time.time() - _login_falhas[0] > FALHAS_JANELA_S:
        _login_falhas.popleft()


def _segundos(restante: float) -> int:
    return max(1, int(restante + 0.999))


def _login_bloquear(reg: dict[str, Any], agora: float, escalar: bool = True) -> int:
    """
    (com _login_lock) Bloqueia o IP com duração escalonada (5 min, 10, 20 ... 24 h) ou, com `escalar` falso (a
    FAIXA), por um tempo fixo de LOGIN_REDE_BLOQUEIO_S sem mexer em nível nenhum. Devolve os segundos.
    """
    if not escalar:
        reg["bloq"] = agora + LOGIN_REDE_BLOQUEIO_S
        reg["t"].clear()
        return int(LOGIN_REDE_BLOQUEIO_S)
    if reg["nivel"] > 0 and reg["fim"]:   # decaimento: -1 nível por 24 h sem bloqueio
        reg["nivel"] = max(0, reg["nivel"] - max(0, int((agora - reg["fim"]) // LOGIN_DECAI_S)))
    duracao = min(LOGIN_IP_BLOQUEIO_S * (2 ** reg["nivel"]), LOGIN_BLOQUEIO_MAX_S)
    reg["nivel"] += 1
    reg["bloq"] = agora + duracao
    reg["fim"] = reg["bloq"]
    reg["t"].clear()   # a contagem recomeça depois do bloqueio
    return int(duracao)


def _login_reservar(ip: str, aparelho: str | None = None) -> tuple[bool, int, str | None]:
    """
    Registra uma tentativa ANTES de verificar o PIN (uma rajada paralela não passa do limite). Devolve
    (permitida, segundos_bloqueado, motivo) com motivo "ip" | "rede" | "travado" | None. Tentativa já bloqueada não
    é contada nem logada (sem inundar o log).
    `aparelho` = id de um cookie de aparelho VÁLIDO (None = desconhecido): fica FORA do bloqueio por faixa e, durante a
    trava diária, tem um orçamento FIXO de LOGIN_TRAVA_ORCAMENTO erros por trava (sem renovar). Desconhecido na trava:
    (False, segundos até expirar - ou 24 h se for manual -, "travado").
    """
    agora = time.monotonic()
    rede = None if aparelho else _chave_rede(ip)
    with _login_lock:
        _login_limpar(agora)
        gasta_orcamento = False
        if _login_travado:
            esgotado = aparelho is None or _login_trava_orcamento.get(aparelho, 0) >= LOGIN_TRAVA_ORCAMENTO
            if esgotado:
                resta = 0 if _login_trava_manual else _segundos(_login_trava_ate - agora)
                return False, resta, "travado"
            gasta_orcamento = True
        reg_ip = _login_registro("ip:" + ip)
        reg_rede = _login_registro(rede) if rede else None
        if agora < reg_ip["bloq"]:
            return False, _segundos(reg_ip["bloq"] - agora), "ip"
        if reg_rede is not None and agora < reg_rede["bloq"]:
            return False, _segundos(reg_rede["bloq"] - agora), "rede"
        if len(reg_ip["t"]) >= LOGIN_IP_MAX_ERROS:   # tentativas pendentes já esgotaram o limite do IP
            return False, _segundos(LOGIN_IP_BLOQUEIO_S), "ip"
        reg_ip["t"].append(agora)
        if reg_rede is not None:
            reg_rede["t"].append(agora)
        if gasta_orcamento:   # RESERVA a tentativa antes do PBKDF2: rajadas concorrentes não passam de 5 por aparelho
            _login_trava_orcamento[aparelho] = _login_trava_orcamento.get(aparelho, 0) + 1
        return True, 0, None


def _login_esta_travado() -> bool:
    with _login_lock:
        return _login_travado


def _login_trava_e_manual() -> bool:
    with _login_lock:
        return _login_travado and _login_trava_manual


def _login_falhou(ip: str, aparelho: str | None = None) -> tuple[int, int, str | None]:
    """
    PIN errado. Devolve (segundos_de_bloqueio, tentativas_restantes, motivo). Se ESTA tentativa dispara o bloqueio
    (do IP, da faixa ou a trava diária), os segundos vêm > 0 e o cliente recebe 429 já agora. Loga uma vez por
    bloqueio, nunca por requisição (e SEM o IP: o IP fica só na auditoria). Durante a trava o erro de um aparelho
    conhecido entra no histórico e gasta o orçamento dele, mas NÃO prolonga a trava.
    """
    global _login_travado, _login_trava_ate, _login_trava_manual
    agora = time.monotonic()
    bloqueado = 0
    motivo: str | None = None
    nova_trava = False
    with _login_lock:
        _login_falhas.append(time.time())
        _login_dia.append(agora)
        reg = _login_registro("ip:" + ip)
        restantes = max(0, LOGIN_IP_MAX_ERROS - len(reg["t"]))
        if len(reg["t"]) >= LOGIN_IP_MAX_ERROS and agora >= reg["bloq"]:
            bloqueado = _login_bloquear(reg, agora)
            motivo = "ip"
            log.warning("Login remoto bloqueado por %d min: muitos PINs errados de um aparelho.", bloqueado // 60)
        chave_rede = None if aparelho else _chave_rede(ip)
        if chave_rede:
            rede = _login_registro(chave_rede)
            if len(rede["t"]) >= LOGIN_REDE_MAX_ERROS and agora >= rede["bloq"]:
                dur = _login_bloquear(rede, agora, escalar=False)
                if not bloqueado:
                    bloqueado, motivo = dur, "rede"
                log.warning("Login remoto bloqueado por %d min: muitos PINs errados vindos da mesma rede.", dur // 60)
        if not _login_travado and len(_login_dia) >= LOGIN_DIA_MAX_ERROS:   # (o orçamento já foi gasto no _login_reservar)
            agora_epoch = time.time()
            # 2ª trava dentro de 7 dias: fica até o dono liberar (fail-closed); a 1ª expira sozinha em 24 h
            _login_trava_manual = any(agora_epoch - t < TRAVA_REINCIDENCIA_S for t in _login_travas)
            _login_travas.append(agora_epoch)
            del _login_travas[:-SEG_TRAVAS_MAX]
            _login_travado = True
            _login_trava_ate = agora + FALHAS_JANELA_S
            _login_trava_orcamento.clear()
            nova_trava = True
            if not aparelho:
                bloqueado, motivo = int(FALHAS_JANELA_S), "travado"
            log.warning("%d PINs errados em 24 h: o login pela rede está TRAVADO para aparelhos desconhecidos%s.",
                        LOGIN_DIA_MAX_ERROS,
                        " até o dono liberar no PC da loja (2ª trava em menos de 7 dias)" if _login_trava_manual
                        else " (expira sozinha em 24 h)")
    if nova_trava:
        _seg_persistir_travas()
    return bloqueado, restantes, motivo


def _login_sucesso(ip: str, aparelho: str | None = None) -> None:
    """
    Acerto remoto: zera o contador DAQUELE IP e devolve a tentativa reservada ao contador da faixa. NÃO zera a
    escalada nem o teto diário (só ação local ou decaimento).
    """
    chave_rede = None if aparelho else _chave_rede(ip)
    with _login_lock:
        if _login_travado and aparelho and _login_trava_orcamento.get(aparelho, 0) > 0:
            _login_trava_orcamento[aparelho] -= 1   # acerto devolve a tentativa reservada (só erro gasta orçamento)
        reg = _login_ips.get("ip:" + ip)
        if reg is not None:
            reg["t"].clear()
        rede = _login_ips.get(chave_rede) if chave_rede else None
        if rede is not None and rede["t"]:
            rede["t"].pop()


def _login_niveis_para_disco() -> dict[str, dict[str, Any]]:
    """Níveis de escalada por IP (só IPs com nível > 0), com o fim do bloqueio em epoch; no máximo SEG_NIVEIS_MAX."""
    agora_mono, agora = time.monotonic(), time.time()
    with _login_lock:
        itens = [(c[3:], r["nivel"], r["fim"]) for c, r in _login_ips.items()
                 if c.startswith("ip:") and r["nivel"] > 0 and r["fim"]]
    itens.sort(key=lambda x: x[2], reverse=True)
    return {ip: {"n": nivel, "fim": int(agora + (fim - agora_mono))} for ip, nivel, fim in itens[:SEG_NIVEIS_MAX]}


def _login_restaurar_do_disco() -> None:
    """
    Na partida (e nos testes): refaz o contador diário a partir de seguranca.falhas das últimas 24 h, o histórico de
    travas (seguranca.travas), a trava vigente (a última trava registrada depois da última liberação que ainda não
    expirou - ou é manual) e os níveis de escalada por IP. O orçamento dos aparelhos conhecidos na trava recomeça
    (só memória). Sem isto, reiniciar o servidor devolveria 300 tentativas e os bloqueios curtos a quem já escalou.
    """
    global _login_travado, _login_trava_ate, _login_trava_manual
    seg = _seg_ler()
    agora_mono, agora = time.monotonic(), time.time()
    desde = max(agora - FALHAS_JANELA_S, seg.get("liberado_em", 0.0))
    recentes = [t for t in seg["falhas"] if t > desde]
    travas = [x for x in seg["travas"]]
    ativa = next((x for x in reversed(travas) if x["t"] > seg.get("liberado_em", 0.0)
                  and (x["manual"] or agora - x["t"] < FALHAS_JANELA_S)), None)
    registrar = False
    if ativa is None and len(recentes) >= LOGIN_DIA_MAX_ERROS:   # trava sem registro (versão anterior): registra agora
        manual = any(agora - x["t"] < TRAVA_REINCIDENCIA_S for x in travas)
        ativa = {"t": max(recentes), "manual": manual}
        travas.append(ativa)
        registrar = True
    with _login_lock:
        _login_dia.clear()
        _login_dia.extend(agora_mono - (agora - t) for t in sorted(recentes))
        _login_travas[:] = [x["t"] for x in travas][-SEG_TRAVAS_MAX:]
        _login_travado = ativa is not None
        _login_trava_manual = bool(ativa and ativa["manual"])
        # (o prazo restante nunca passa de 24 h, mesmo com uma data estranha no arquivo)
        _login_trava_ate = agora_mono + min(FALHAS_JANELA_S, max(0.0, ativa["t"] + FALHAS_JANELA_S - agora)) if ativa else 0.0
        _login_trava_orcamento.clear()
        for ip, info in seg.get("niveis", {}).items():
            reg = _login_registro("ip:" + ip)
            reg["nivel"] = info["n"]
            reg["fim"] = agora_mono + (info["fim"] - agora)
            reg["bloq"] = reg["fim"] if info["fim"] > agora else 0.0
    if registrar:
        _seg_persistir_travas(travas)
    if _login_travado:
        log.warning("O login pela rede continua TRAVADO para aparelhos desconhecidos%s. Libere no PC da loja.",
                    " (trava manual)" if _login_trava_manual else "")


def _apagar_cookie(resp: Response) -> Response:
    resp.set_cookie(COOKIE_SESSAO, "", max_age=0, expires=0, httponly=True, samesite="Strict", path="/")
    return resp


def _resp_429(segundos: int, travado: bool = False, motivo: str | None = None,
              suspenso: bool = False, manual: bool = False) -> tuple[Response, int]:
    """
    429 de login. `travado`: teto diário ("travado": true). `motivo` "rede": a FAIXA está bloqueada ("codigo":
    "bloqueio_rede"). `suspenso`: o pedido veio do loopback mas o túnel suspendeu a confiança - repete o aviso do
    túnel ("codigo": "loopback_suspenso"), porque é assim que o dono no PC da loja descobre o motivo.
    """
    minutos = max(1, -(-segundos // 60))
    corpo: dict[str, Any] = {"ok": False, "bloqueado_por_s": None if (travado and manual) else segundos}
    if travado:
        corpo["travado"] = True
        corpo["trava_manual"] = manual
        corpo["error"] = ("Muitas tentativas erradas. O login pela rede foi travado: peça ao dono para liberar "
                          "no PC da loja." if manual else
                          "Muitas tentativas erradas. O login pela rede foi travado por segurança: tente de novo "
                          "mais tarde ou peça ao dono para liberar no PC da loja.")
    elif motivo == "rede":
        corpo["codigo"] = "bloqueio_rede"
        corpo["error"] = f"Muitas tentativas vindas desta rede. Tente novamente em {minutos} min."
    else:
        corpo["error"] = f"Muitas tentativas. Tente novamente em {minutos} min."
    if suspenso:
        corpo["codigo"] = "loopback_suspenso"
        corpo["error"] += " " + MSG_LOOPBACK_SUSPENSO
    return jsonify(corpo), 429


# --- Alertas de segurança persistidos (P12) ------------------------------------------------------------
# config.json, chave "seguranca": {"falhas": [epoch...], "logins": [{"t","ip","novo"}], "ips": {ip: epoch},
# "visto_ate": epoch}. Sobrevive a reinício (o contador antigo era só memória). Só o PC da loja lê (via
# /api/auth/status). Retenção curta de propósito: falhas 24 h; IPs conhecidos 90 dias (no máx. 100);
# últimos logins 20. Nunca há PIN, token nem texto aqui; o IP é dado pessoal, por isso o prazo.
SEG_FALHAS_MAX = 500
SEG_LOGINS_MAX = 20
SEG_IPS_MAX = 100
SEG_IPS_RETENCAO_S = 90 * 24 * 3600.0
SEG_ALERTA_FALHAS = 5
SEG_NIVEIS_MAX = 200         # IPs com nível de escalada guardado no disco
SEG_FALHAS_FLUSH_S = 30.0    # PINs errados são gravados em lote: no máximo 1 gravação do config a cada 30 s
SEG_NOVOS_MAX = 100          # logins de IP novo ainda não vistos (guardados até o "visto" do dono)
SEG_IP_REGISTRO_S = 3600.0   # login certo de um IP já conhecido há menos que isto não regrava o config


def _seg_ip(ip: Any) -> str:
    try:
        return str(ipaddress.ip_address(str(ip).split("%", 1)[0]))
    except ValueError:
        return "?"


def _numero_epoch(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or v > 4e9:
        return None
    return float(v)


def _seg_sanear(bruto: Any, agora: float | None = None) -> dict[str, Any]:
    """Documento "seguranca" válido a partir do que está no config (config editado à mão não derruba nada)."""
    agora = time.time() if agora is None else agora
    bruto = bruto if isinstance(bruto, dict) else {}
    lista_f = bruto.get("falhas") if isinstance(bruto.get("falhas"), list) else []
    falhas = sorted(t for t in (_numero_epoch(x) for x in lista_f[-SEG_FALHAS_MAX * 2:])
                    if t is not None and agora - t <= FALHAS_JANELA_S)[-SEG_FALHAS_MAX:]
    logins: list[dict[str, Any]] = []
    for e in (bruto.get("logins") if isinstance(bruto.get("logins"), list) else [])[-SEG_LOGINS_MAX:]:
        t = _numero_epoch(e.get("t")) if isinstance(e, dict) else None
        if t is not None:
            logins.append({"t": t, "ip": _seg_ip(e.get("ip")), "novo": e.get("novo") is True})
    ips: dict[str, float] = {}
    brutos_ips = bruto.get("ips") if isinstance(bruto.get("ips"), dict) else {}
    for ip, t in sorted(brutos_ips.items(), key=lambda kv: _numero_epoch(kv[1]) or 0.0)[-SEG_IPS_MAX:]:
        visto = _numero_epoch(t)
        if visto is not None and agora - visto <= SEG_IPS_RETENCAO_S and _seg_ip(ip) != "?":
            ips[_seg_ip(ip)] = visto
    visto_ate = _numero_epoch(bruto.get("visto_ate")) or 0.0
    # Logins de IP NOVO ainda não vistos ficam numa lista À PARTE, que a rotação dos "últimos logins" não toca:
    # 20 logins comuns não podem apagar o alerta. Só o "visto" do dono a esvazia.
    novos: list[dict[str, Any]] = []
    for e in (bruto.get("novos") if isinstance(bruto.get("novos"), list) else [])[-SEG_NOVOS_MAX:]:
        t = _numero_epoch(e.get("t")) if isinstance(e, dict) else None
        if t is not None and t > visto_ate:
            novos.append({"t": t, "ip": _seg_ip(e.get("ip"))})
    niveis: dict[str, dict[str, Any]] = {}
    brutos_n = bruto.get("niveis") if isinstance(bruto.get("niveis"), dict) else {}
    for ip, info in list(brutos_n.items())[:SEG_NIVEIS_MAX]:
        fim = _numero_epoch(info.get("fim")) if isinstance(info, dict) else None
        n = info.get("n") if isinstance(info, dict) else None
        if (fim is not None and isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 20
                and _seg_ip(ip) != "?" and agora - fim <= LOGIN_DECAI_S * n):
            niveis[_seg_ip(ip)] = {"n": n, "fim": fim}
    liberado_em = _numero_epoch(bruto.get("liberado_em")) or 0.0
    travas: list[dict[str, Any]] = []
    for e in (bruto.get("travas") if isinstance(bruto.get("travas"), list) else [])[-SEG_TRAVAS_MAX:]:
        t = _numero_epoch(e.get("t")) if isinstance(e, dict) else None
        if t is None or t > agora + 300:   # data do futuro (relógio errado ou arquivo adulterado): descarta
            continue
        manual = e.get("manual") is True
        # a trava MANUAL vigente (depois da última liberação) nunca cai pelo corte de 30 dias: só o dono a tira
        if agora - t <= 30 * 24 * 3600.0 or (manual and t > liberado_em):
            travas.append({"t": t, "manual": manual})
    return {"falhas": falhas, "logins": logins, "ips": ips, "visto_ate": visto_ate, "novos": novos,
            "niveis": niveis, "liberado_em": liberado_em, "travas": travas}


def _seg_ler(cfg: dict | None = None) -> dict[str, Any]:
    return _seg_sanear((load_config() if cfg is None else cfg).get("seguranca"))


def _seg_mutar(fn: Callable[[dict[str, Any]], None]) -> None:
    """Lê, altera e grava o documento sob o lock do config. Falha de disco só vira aviso no log."""
    try:
        with _config_lock:
            cfg = load_config()
            seg = _seg_sanear(cfg.get("seguranca"))
            fn(seg)
            cfg["seguranca"] = seg
            _gravar_config_atomico(cfg)
    except OSError as exc:
        log.warning("Não foi possível gravar os alertas de segurança (%s).", type(exc).__name__)


_seg_pend_lock = threading.Lock()
_seg_falhas_pend: list[float] = []     # PINs errados ainda não gravados no config.json
_seg_flush_ultimo = 0.0                # monotonic da última gravação em lote
_seg_flush_timer: threading.Timer | None = None


def _seg_registrar_falha() -> None:
    """
    Anota um PIN errado. Gravar o config.json (que tem os segredos) a cada erro de um ataque seria uma gravação
    por tentativa: os erros ficam em memória e vão ao disco em LOTE, no máximo 1 gravação a cada
    SEG_FALHAS_FLUSH_S (e no desligamento: _seg_flush_falhas). A contagem do painel inclui os pendentes.
    """
    global _seg_flush_timer
    agora = time.time()
    with _seg_pend_lock:
        _seg_falhas_pend.append(agora)
        vencido = time.monotonic() - _seg_flush_ultimo >= SEG_FALHAS_FLUSH_S
        if not vencido and _seg_flush_timer is None:
            _seg_flush_timer = threading.Timer(SEG_FALHAS_FLUSH_S, _seg_flush_falhas)
            _seg_flush_timer.daemon = True
            _seg_flush_timer.start()
    if vencido:
        _seg_flush_falhas()


def _seg_flush_falhas() -> None:
    """Grava no config.json os PINs errados pendentes (uma gravação só). Idempotente; chamado também ao desligar."""
    global _seg_flush_timer, _seg_flush_ultimo
    with _seg_pend_lock:
        itens = list(_seg_falhas_pend)
        _seg_falhas_pend.clear()
        _seg_flush_ultimo = time.monotonic()
        if _seg_flush_timer is not None:
            _seg_flush_timer.cancel()
            _seg_flush_timer = None
    if itens:
        _seg_mutar(lambda seg: seg.update(falhas=(seg["falhas"] + itens)[-SEG_FALHAS_MAX:]))


def _seg_reset_pendentes() -> None:
    """(testes) Descarta os PINs errados pendentes e o temporizador."""
    global _seg_flush_timer, _seg_flush_ultimo
    with _seg_pend_lock:
        _seg_falhas_pend.clear()
        _seg_flush_ultimo = 0.0
        if _seg_flush_timer is not None:
            _seg_flush_timer.cancel()
            _seg_flush_timer = None


def _seg_marcar_liberado(zerar_niveis: bool, zerar_travas: bool = False) -> None:
    """
    O dono liberou o login (ou trocou o PIN / revogou): os PINs errados de ANTES não podem religar o trava quando o
    servidor reiniciar (seguranca.liberado_em) e, com `zerar_niveis`, os níveis de escalada guardados saem do disco.
    """
    with _seg_pend_lock:
        _seg_falhas_pend.clear()
    seg = _seg_ler()
    if not (seg["falhas"] or seg["travas"] or (zerar_niveis and seg["niveis"])):
        return   # nada a esquecer: não regrava o config à toa
    agora = time.time()

    def _aplicar(doc: dict[str, Any]) -> None:
        doc["liberado_em"] = agora
        if zerar_niveis:
            doc["niveis"] = {}
        if zerar_travas:
            doc["travas"] = []

    _seg_mutar(_aplicar)


def _seg_persistir_travas(travas: list[dict[str, Any]] | None = None) -> None:
    """Grava o histórico de travas (só datas e se foi manual; no máximo SEG_TRAVAS_MAX). Sem argumento, acrescenta a última."""
    if travas is None:
        with _login_lock:
            nova = {"t": _login_travas[-1] if _login_travas else time.time(), "manual": _login_trava_manual}
        _seg_mutar(lambda doc: doc.update(travas=(doc["travas"] + [nova])[-SEG_TRAVAS_MAX:]))
    else:
        _seg_mutar(lambda doc: doc.update(travas=travas[-SEG_TRAVAS_MAX:]))


def _seg_persistir_niveis() -> None:
    """Grava os níveis de escalada por IP (poucos bytes, no máximo SEG_NIVEIS_MAX) depois de um bloqueio."""
    niveis = _login_niveis_para_disco()
    _seg_mutar(lambda doc: doc.update(niveis=niveis))


def _seg_registrar_login(ip: str) -> bool:
    """
    Registra um login remoto bem-sucedido; True se o IP nunca tinha sido visto (login novo). Um IP já conhecido
    que entrou de novo há menos de 1 h não muda nada e NÃO regrava o config.json (o arquivo tem os segredos do
    Spotify: cada gravação a menos é uma janela a menos).
    """
    agora = time.time()
    ip = _seg_ip(ip)
    ultimo = _seg_ler().get("ips", {}).get(ip)
    if ultimo is not None and 0 <= agora - ultimo < SEG_IP_REGISTRO_S:
        return False
    resultado = {"novo": False}

    def _aplicar(seg: dict[str, Any]) -> None:
        resultado["novo"] = ip not in seg["ips"]
        seg["ips"][ip] = agora
        seg["logins"].append({"t": agora, "ip": ip, "novo": resultado["novo"]})
        seg["logins"] = seg["logins"][-SEG_LOGINS_MAX:]
        if resultado["novo"]:
            seg["novos"].append({"t": agora, "ip": ip})
            seg["novos"] = seg["novos"][-SEG_NOVOS_MAX:]
        if len(seg["ips"]) > SEG_IPS_MAX:
            for velho in sorted(seg["ips"], key=lambda k: seg["ips"][k])[:len(seg["ips"]) - SEG_IPS_MAX]:
                del seg["ips"][velho]

    _seg_mutar(_aplicar)
    return resultado["novo"]


def _iso_local(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def _seg_resumo(cfg: dict, bloqueio_global_s: int | None, travado: bool = False, bloqueados: int = 0,
                trava_manual: bool = False) -> dict[str, Any]:
    """
    O que o painel LOCAL mostra como aviso (campo "seguranca" do /api/auth/status). "alerta" liga quando há
    5+ PINs errados desde a última vez que o dono marcou como visto, um bloqueio global ativo, um login
    de IP novo ainda não visto, ou um encaminhamento de rede para a porta.
    """
    agora = time.time()
    seg = _seg_ler(cfg)
    with _seg_pend_lock:
        seg["falhas"] = sorted(seg["falhas"] + list(_seg_falhas_pend))   # (os ainda não gravados também contam)
    visto = seg["visto_ate"]
    falhas_novas = sum(1 for t in seg["falhas"] if t > visto)
    novos = [e for e in seg["novos"] if e["t"] > visto]
    tuneis = {"fortes": list(_tuneis_estado["fortes"]), "fracos": list(_tuneis_estado["fracos"]),
              "confianca_loopback": _confiar_loopback(), "modo": _modo_loopback()}
    pendentes = _revisao_contar()
    return {
        "alerta": (falhas_novas >= SEG_ALERTA_FALHAS or travado or bloqueados > 0 or bool(novos)
                   or bool(tuneis["fortes"])),
        "login_travado": travado,
        "login_trava_manual": bool(travado and trava_manual),
        "bloqueios_ativos": bloqueados,
        "pendentes": pendentes,
        "falhas_24h": sum(1 for t in seg["falhas"] if agora - t <= FALHAS_JANELA_S),
        "falhas_novas": falhas_novas,
        "bloqueio_global_s": bloqueio_global_s,
        "logins_novos": [{"em": _iso_local(e["t"]), "ip": e["ip"]} for e in novos[-10:]],
        "ultimos_logins": [{"em": _iso_local(e["t"]), "ip": e["ip"], "novo": e["novo"]}
                           for e in reversed(seg["logins"][-10:])],
        "visto_em": _iso_local(visto) if visto else None,
        "tuneis": tuneis,
        "pendentes_remotos": pendentes["total"],
    }


@app.get("/api/auth/status")
def auth_status() -> Response:
    cfg = load_config()
    _sincronizar_versao(cfg)
    local = _requisicao_local()
    resp: dict[str, Any] = {
        "local": local,
        "pin_configurado": pin_configurado(cfg),
        "autenticado": local or _token_valido_sessao(request.cookies.get(COOKIE_SESSAO), cfg),
        # v2.5: o botão "Desligar o Proclamare" só existe no PC da loja (a rota é somente local).
        "pode_desligar": local,
        # v2.7.1: True quando o endereço E o Host são de loopback mas o painel NÃO é tratado como o PC da loja
        # porque a vigia achou um encaminhamento de rede para a porta (com PIN configurado). Só o booleano:
        # nenhum nome de processo sai numa rota pública.
        "loopback_suspenso": bool(
            not local and _tunel_suspende_loopback()
            and _local_por(request.remote_addr or "", request.host,
                           any(h in request.headers for h in CABECALHOS_DE_PROXY), ignorar_tunel=True)),
    }
    if local:  # só o PC da loja vê sinais de ataque
        agora = time.monotonic()
        with _login_lock:
            _login_limpar(agora)
            travado = _login_travado
            bloqueados = sum(1 for r in _login_ips.values() if agora < r["bloq"])
            manual = _login_travado and _login_trava_manual
            restante_trava = _segundos(_login_trava_ate - agora) if travado and not manual else 0
        # bloqueio_global_s: segundos até a trava AUTOMÁTICA expirar; 0 sem trava; null na trava MANUAL (só o dono destrava)
        bloqueio = None if manual else restante_trava
        seg = _seg_resumo(cfg, bloqueio, travado, bloqueados, manual)
        # falhas_recentes/bloqueio_global_s: os campos da v2.2 (agora persistidos, 24 h)
        resp["falhas_recentes"] = seg["falhas_24h"]
        resp["bloqueio_global_s"] = bloqueio
        resp["seguranca"] = seg
    resposta = jsonify(resp)
    if not local and resp["autenticado"]:   # sessão válida = PIN provado: emite/renova o cookie de aparelho
        _renovar_cookie_aparelho(resposta, cfg)
    return resposta


@app.post("/api/auth/login")
def auth_login() -> Any:
    pin = corpo_json().get("pin")
    if not isinstance(pin, str) or not RE_PIN.fullmatch(pin):
        raise ErroApi(MSG_PIN_FORMATO)
    cfg = load_config()
    _sincronizar_versao(cfg)
    if not pin_configurado(cfg):
        return _resp_sem_sessao(cfg)
    ip = request.remote_addr or "?"
    local = _requisicao_local()
    restantes = LOGIN_IP_MAX_ERROS
    # Aparelho CONHECIDO = cookie de aparelho válido (nunca o IP): fica fora da faixa /24 e tem orçamento na trava
    aparelho = _aparelho_valido(request.cookies.get(COOKIE_APARELHO), cfg) if not local else None
    suspenso = (not local and _tunel_suspende_loopback() and _local_por(
        ip, request.host, any(h in request.headers for h in CABECALHOS_DE_PROXY), ignorar_tunel=True))
    if local:
        # O PC da loja não precisa de PIN, então quase nunca chega aqui; um limite folgado só trava quem
        # se passa por ele (túnel/encaminhador que o detector não viu) tentando PINs sem parar.
        if excedeu_limite("login-local", LOGIN_LOCAL_MAX, 300.0):
            return _resp_429(300)
    else:
        permitida, bloqueado_s, motivo = _login_reservar(ip, aparelho)
        if not permitida:
            return _resp_429(bloqueado_s, motivo == "travado", motivo, suspenso, _login_trava_e_manual())
    it, salt, esperado = _ler_hash_pin(cfg["acesso_pin_hash"])  # type: ignore[misc]
    # Mesmo custo de PBKDF2 em acerto e erro; comparação em tempo constante.
    certo = hmac.compare_digest(_hash_pin(pin, salt, it), esperado)
    if not certo:
        if not local:
            bloqueado_s, restantes, motivo = _login_falhou(ip, aparelho)
            _seg_registrar_falha()
            auditar("login", resultado="pin_errado", bloqueado_s=bloqueado_s or None, motivo=motivo)
            if bloqueado_s:
                if motivo == "ip":
                    _seg_persistir_niveis()   # o nível de escalada sobrevive a reinício
                return _resp_429(bloqueado_s, motivo == "travado", motivo, suspenso, _login_trava_e_manual())
        else:
            auditar("login", resultado="pin_errado")
        log.warning("Login com PIN incorreto (origem %s).", "local" if local else "remoto")   # o IP fica só na auditoria
        return jsonify({"ok": False, "error": "PIN incorreto.", "tentativas_restantes": restantes}), 401
    novo = False
    if not local:
        _login_sucesso(ip, aparelho)
        if excedeu_limite("login-ok:" + ip, LOGIN_OK_MAX_POR_MIN, 60.0):   # logins CERTOS em rajada também têm teto
            auditar("login", resultado="limite")
            return _resp_429(60, suspenso=suspenso)
        novo = _seg_registrar_login(ip)
    auditar("login", resultado="ok", ip_novo=int(novo) if not local else None)
    log.info("Login com PIN bem-sucedido (%s)%s.", "local" if local else "remoto",
             " - aparelho NOVO" if novo else "")   # o IP fica só na auditoria
    resp = jsonify({"ok": True})
    novo_token, vida = _emitir_token(cfg)
    _definir_cookie_sessao(resp, novo_token, vida)
    if not local:   # o aparelho passa a ser "conhecido" (só afeta o limite de tentativas de login; não dá acesso)
        _definir_cookie_aparelho(resp, _emitir_cookie_aparelho(cfg, aparelho))
    return resp


@app.post("/api/auth/logout")
def auth_logout() -> Response:
    return _apagar_cookie(jsonify({"ok": True}))


# Opções da "revisão do que os aparelhos remotos deixaram" (v2.7.1, P04), aceitas por /api/auth/pin e
# /api/auth/revogar. Padrão: só desativar os horários remotos (não destrói nada).
_OPCOES_REVISAO_PADRAO = {"desativar_remotos": True, "remover_remotos": False,
                          "restaurar_mensagens": False, "limpar_fila": False}


def _opcoes_de_revisao(dados: dict, extras: frozenset[str] = frozenset()) -> dict[str, bool]:
    """Valida as opções do corpo (chave desconhecida ou valor que não é bool = 400). Tudo antes de gravar."""
    desconhecidas = set(dados) - set(_OPCOES_REVISAO_PADRAO) - extras
    if desconhecidas:
        raise ErroApi("Campo desconhecido.")
    opcoes: dict[str, bool] = {}
    for nome, padrao in _OPCOES_REVISAO_PADRAO.items():
        valor = dados.get(nome, padrao)
        if not isinstance(valor, bool):
            raise ErroApi(f"Campo '{nome}' inválido.")
        opcoes[nome] = valor
    return opcoes


def _revisar_apos_acesso(opcoes: dict[str, bool]) -> dict[str, Any]:
    """Aplica a revisão depois de trocar o PIN / revogar. Uma falha aqui nunca desfaz a revogação."""
    try:
        return _aplicar_revisao_remotos(opcoes)
    except Exception:  # noqa: BLE001
        log.exception("Falha ao revisar o que os aparelhos remotos deixaram.")
        return {"falhou": True}


@app.post("/api/auth/pin")
def auth_pin() -> Response:
    """
    Define/troca (str) ou remove (null) o PIN. Somente no computador da loja. v2.7.1: aceita as opções de
    revisão (desativar_remotos, remover_remotos, restaurar_mensagens, limpar_fila) para desfazer o que
    aparelhos remotos deixaram (horários, mensagens, listas, atalhos), e responde com o que foi feito.
    """
    if not _requisicao_local():
        raise ErroApi("Defina o PIN no computador da loja.", 403)
    dados = corpo_json()
    if "pin" not in dados:
        raise ErroApi("Campo 'pin' ausente.")
    opcoes = _opcoes_de_revisao(dados, frozenset({"pin"}))
    pin = dados["pin"]
    novo_pin = None if pin is None else validar_pin_novo(pin)
    configurado = aplicar_pin(novo_pin)
    log.info("PIN de acesso pela rede %s.", "definido/trocado" if pin is not None else "removido")
    revisao = _revisar_apos_acesso(opcoes)
    auditar("pin", resultado="definido" if pin is not None else "removido",
            desativados=revisao.get("horarios_desativados"), removidos=revisao.get("itens_removidos"))
    return jsonify({"ok": True, "pin_configurado": configurado,
                    "programacao_remota": _prog_contar_remotos(), "revisao": revisao})


@app.post("/api/auth/revogar")
def auth_revogar() -> Response:
    """
    Derruba todas as sessões (acesso_versao + 1) sem trocar o PIN. Somente local. v2.7.1: as mesmas opções
    de revisão de /api/auth/pin (padrão: desativar os horários criados/alterados por aparelhos remotos).
    """
    if not _requisicao_local():
        raise ErroApi("Revogue as sessões no computador da loja.", 403)
    opcoes = _opcoes_de_revisao(corpo_json())
    with _config_lock:
        cfg = load_config()
        cfg["acesso_versao"] = _versao_acesso(cfg) + 1
        _gravar_config_atomico(cfg)
        _marcar_versao_vista(cfg)
    _login_resetar_global()
    log.info("Todas as sessões de acesso pela rede foram revogadas.")
    revisao = _revisar_apos_acesso(opcoes)
    auditar("revogar", desativados=revisao.get("horarios_desativados"), removidos=revisao.get("itens_removidos"))
    return jsonify({"ok": True, "programacao_remota": _prog_contar_remotos(), "revisao": revisao})


@app.post("/api/auth/liberar")
def auth_liberar() -> Response:
    """
    "Liberar login pela rede" (v2.7.1, P07). Somente local. Zera os bloqueios de login (por IP e global) SEM
    derrubar as sessões nem trocar o PIN: quem foi barrado por um ataque espalhado volta a poder entrar.
    """
    if not _requisicao_local():
        raise ErroApi("Libere o login no computador da loja.", 403)
    corpo = corpo_json()
    exigir_chaves(corpo, ("zerar_escalada",))
    zerar = corpo.get("zerar_escalada", False)
    if not isinstance(zerar, bool):
        raise ErroApi("Campo 'zerar_escalada' inválido.")
    _login_liberar(zerar)   # zera bloqueios e o trava diário; o nível de escalada só com zerar_escalada
    auditar("login_liberar", zerar_escalada=int(zerar))
    log.info("Bloqueios de login pela rede liberados pelo PC da loja.")
    return jsonify({"ok": True})


@app.post("/api/auth/alertas/visto")
def auth_alertas_visto() -> Response:
    """Marca os alertas de segurança como vistos (o aviso do painel some até haver novidade). Só local."""
    if not _requisicao_local():
        raise ErroApi("Disponível só no PC do Proclamare.", 403)
    if corpo_json():
        raise ErroApi("Campo desconhecido.")
    agora = time.time()
    _seg_mutar(lambda seg: seg.update(visto_ate=agora, novos=[]))
    auditar("alertas_visto")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Utilidades de rede
# ---------------------------------------------------------------------------
_internet_lock = threading.Lock()
_internet_cache: dict[str, Any] = {"ok": False, "ts": None}  # ts em time.monotonic()
INTERNET_TTL_S = 10.0


def tem_internet(timeout: float = 1.5) -> bool:
    """
    Testa conectividade (TCP em 8.8.8.8:53) com cache de ~10 s.
    Só UMA thread testa por vez; as demais usam o último valor em cache.
    Usa timeout por conexão (sem socket.setdefaulttimeout, que é global) e
    fecha o socket.
    """
    def valido() -> bool:
        ts = _internet_cache["ts"]
        return ts is not None and (time.monotonic() - ts) < INTERNET_TTL_S

    if valido():
        return bool(_internet_cache["ok"])
    # Na primeiríssima checagem não há valor em cache: espera a outra thread.
    if not _internet_lock.acquire(blocking=_internet_cache["ts"] is None):
        return bool(_internet_cache["ok"])
    try:
        if valido():  # outra thread pode ter atualizado enquanto esperávamos
            return bool(_internet_cache["ok"])
        try:
            with socket.create_connection(("8.8.8.8", 53), timeout=timeout):
                ok = True
        except OSError:
            ok = False
        _internet_cache["ok"] = ok
        _internet_cache["ts"] = time.monotonic()
        return ok
    finally:
        _internet_lock.release()


def _invalidar_cache_internet() -> None:
    """Força um novo teste na próxima chamada (ex.: depois de uma falha online)."""
    _internet_cache["ts"] = -1e9  # muito no passado: o TTL já expirou


def ip_local() -> str:
    """IP da rede local (para mostrar na inicialização). Não envia pacotes."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


# ---------------------------------------------------------------------------
# Áudio — TUDO (pygame + pyttsx3) numa única thread dedicada.
# No Windows, SAPI/COM exige CoInitialize por thread; com uma thread só,
# o mixer é iniciado uma vez e reaproveitado entre anúncios.
# ---------------------------------------------------------------------------
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
# Tetos: o cliente espera ~180 s por /api/announce; a soma dos tetos do servidor
# (geração 30 s + duck/restore do Spotify + áudio) precisa ficar abaixo disso.
TIMEOUT_AUDIO_S = 90.0  # teto de espera por um anúncio (fala + reprodução)
TIMEOUT_REPRODUCAO_S = 80.0  # teto do laço de reprodução do mp3 (< TIMEOUT_AUDIO_S)
TIMEOUT_GERACAO_S = 30.0  # teto para o edge-tts gerar o mp3


def _iniciar_thread_audio() -> None:
    """Initializer do executor: prepara COM nesta thread (só Windows)."""
    try:
        import pythoncom  # type: ignore[import-not-found]

        pythoncom.CoInitialize()
    except ImportError:
        pass  # Linux/macOS (ou pywin32 ausente): nada a fazer
    except Exception:  # noqa: BLE001
        log.warning("Não foi possível inicializar COM na thread de áudio.", exc_info=True)


def _novo_executor_audio() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="audio", initializer=_iniciar_thread_audio
    )


_executor_lock = threading.Lock()
_executor_audio = _novo_executor_audio()


def _encerrar_executor_audio() -> None:
    with _executor_lock:
        _executor_audio.shutdown(wait=False, cancel_futures=True)


atexit.register(_encerrar_executor_audio)


def _substituir_executor_audio() -> None:
    """
    Descarta o executor cujo único worker está preso (SAPI/driver travado) e cria
    outro, para os próximos anúncios não ficarem enfileirados atrás da tarefa
    travada. A thread presa não pode ser morta; fica órfã até o processo reiniciar.
    """
    global _executor_audio
    with _executor_lock:
        _executor_audio.shutdown(wait=False, cancel_futures=True)
        _executor_audio = _novo_executor_audio()
    log.error(
        "Thread de áudio travada: executor substituído. Se o problema se repetir, "
        "reinicie o servidor (a thread antiga continua presa até lá)."
    )
    _sinalizar_executor_substituido()


def _sinalizar_executor_substituido() -> None:
    """
    Com o modo "saídas de som" em uso, a thread órfã pode estar dentro do pygame: o motor
    fica CONTAMINADO (ninguém mexe no pygame; 503 motor_contaminado) até reiniciar. Sem
    "saida_anuncio" e sem o motor ter sido usado, nada muda em relação à v2.2.1.
    """
    try:
        som = SOM.config_som()
        if SOM.motor_em_uso or (som is not None and som["saida_anuncio"] is not None):
            SOM.marcar_contaminado()
            log.error("Motor de áudio contaminado: reinicie o Proclamare.")
    except Exception:  # noqa: BLE001 - nunca atrapalha a troca do executor
        log.warning("Não foi possível marcar o motor de áudio como contaminado.", exc_info=True)


def executar_no_audio(func: Callable[..., Any], *args: Any, timeout: float = TIMEOUT_AUDIO_S) -> Any:
    """Roda `func` na thread de áudio e espera o resultado (propaga exceções)."""
    with _executor_lock:
        futuro = _executor_audio.submit(func, *args)
    try:
        return futuro.result(timeout=timeout)
    except FuturoTimeout:
        # cancel() só funciona se a tarefa ainda não começou (estava na fila).
        # Se já estava rodando, o worker está preso: troca o executor.
        if not futuro.cancel():
            if futuro.done():  # terminou no instante do timeout: aproveita o resultado
                return futuro.result()
            _substituir_executor_audio()
        raise TimeoutError("A reprodução de áudio excedeu o tempo limite.") from None


def _garantir_mixer(reiniciar: bool = False) -> Any:
    """(Thread de áudio) Inicia o mixer uma vez; `reiniciar` descarta e recria."""
    import pygame

    # Se o mixer ficou aberto pelo motor de "saídas de som" (teste ou anúncio anterior em
    # outra saída), fecha: o caminho antigo SEMPRE usa o dispositivo padrão do Windows.
    if SOM.motor_em_uso and SOM.motor.aberto:
        SOM.motor.fechar()
    if not SOM.motor_ok:
        raise RuntimeError("motor de áudio contaminado")
    if reiniciar and pygame.mixer.get_init():
        with som_pkg.bloqueio_sdl:   # a vigia lê a lista do SDL: o quit não pode correr junto
            pygame.mixer.quit()
    if not pygame.mixer.get_init():
        # O lock evita que a varredura de saídas (som/local.py) esteja com o SDL de
        # áudio inicializado (e SDL_AUDIODRIVER temporário) no mesmo instante.
        with som_pkg.bloqueio_sdl:
            pygame.mixer.init()
    return pygame


def _reproduzir_legado(caminho: Path, teto_s: float, parar: Any = None) -> bool:
    """
    (Thread de áudio) Toca o arquivo pelo mixer padrão (fluxo legado, sem "saídas de som") até o fim,
    até `teto_s` ou até `parar.is_set()` (v2.6: programação). Devolve True se foi cortado por `parar`.
    NÃO apaga o arquivo: quem chamou decide (o anúncio apaga o mp3 temporário; o áudio agendado toca
    um arquivo da pasta de músicas, que nunca pode ser apagado aqui).
    """
    pygame = None
    cortado = False
    try:
        for tentativa in (0, 1):
            try:
                pygame = _garantir_mixer(reiniciar=tentativa == 1)
                pygame.mixer.music.load(str(caminho))
                pygame.mixer.music.play()
                break
            except Exception:  # noqa: BLE001  (pygame.error, dispositivo sumiu...)
                if tentativa == 1:
                    raise
                log.warning("Falha ao tocar; reiniciando o mixer e tentando de novo.")
        limite = time.monotonic() + teto_s
        while pygame.mixer.music.get_busy() and time.monotonic() < limite:
            if parar is not None and parar.is_set():
                cortado = True
                break
            time.sleep(0.05)
    finally:
        if pygame is not None:
            with contextlib.suppress(Exception):
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()  # libera o arquivo (Windows trava a exclusão)
    return cortado


def _tocar_mp3(caminho: Path, parar: Any = None) -> None:
    """(Thread de áudio) Toca o mp3 do anúncio até o fim (teto de 80 s) e apaga o arquivo em seguida."""
    try:
        _reproduzir_legado(caminho, TIMEOUT_REPRODUCAO_S, parar)
    finally:
        apagar_audio(caminho)


def apagar_audio(caminho: Path | None) -> None:
    if caminho is not None:
        with contextlib.suppress(OSError):
            caminho.unlink(missing_ok=True)


_ultima_limpeza = 0.0


def limpar_audios_antigos(forcar: bool = False) -> None:
    """
    Rede de segurança: mantém no máximo MAX_ARQUIVOS_TEMP mp3/wav na pasta
    (normalmente cada mp3 já é apagado logo após tocar). Roda raramente.
    """
    global _ultima_limpeza
    agora = time.monotonic()
    if not forcar and (agora - _ultima_limpeza) < INTERVALO_LIMPEZA_S:
        return
    _ultima_limpeza = agora
    arquivos: list[tuple[float, Path]] = []
    for p in [*AUDIO_DIR.glob("anuncio_*.mp3"), *AUDIO_DIR.glob("anuncio_*.wav")]:
        try:
            arquivos.append((p.stat().st_mtime, p))
        except OSError:
            continue
    arquivos.sort()
    for _, p in arquivos[: max(len(arquivos) - MAX_ARQUIVOS_TEMP, 0)]:
        apagar_audio(p)


async def _gerar_audio_online_async(texto: str, voz: str, taxa: str, caminho: Path) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(texto, voz, rate=taxa)
    await asyncio.wait_for(communicate.save(str(caminho)), timeout=TIMEOUT_GERACAO_S)


def gerar_audio_online(texto: str, voz: str, velocidade_pct: int) -> Path:
    """Gera o mp3 com edge-tts (nome único); em caso de falha não deixa lixo."""
    taxa = f"{velocidade_pct:+d}%"
    caminho = AUDIO_DIR / f"anuncio_{uuid.uuid4().hex}.mp3"
    try:
        asyncio.run(_gerar_audio_online_async(texto, voz, taxa, caminho))
        if not caminho.exists() or caminho.stat().st_size == 0:
            raise RuntimeError("edge-tts não gerou áudio.")
    except BaseException:
        apagar_audio(caminho)
        raise
    return caminho


# --- Vozes offline (pyttsx3) -------------------------------------------------
def escolher_voz_offline_padrao(vozes: list[Any]) -> str | None:
    """Prefere voz pt-BR, depois qualquer pt; senão None (padrão do sistema)."""
    candidatos_pt_br, candidatos_pt = [], []
    for v in vozes:
        nome = (v.name or "").lower()
        try:
            idiomas = " ".join(
                lang.decode() if isinstance(lang, bytes) else str(lang)
                for lang in (v.languages or [])
            ).lower()
        except Exception:  # noqa: BLE001
            idiomas = ""
        texto = f"{nome} {idiomas} {v.id.lower()}"
        if "brazil" in texto or "pt_br" in texto or "pt-br" in texto:
            candidatos_pt_br.append(v.id)
        elif "portug" in texto or texto.strip().startswith("pt"):
            candidatos_pt.append(v.id)
    return (candidatos_pt_br or candidatos_pt or [None])[0]


def _listar_vozes_offline() -> dict:
    """(Thread de áudio) Consulta o pyttsx3 e devolve {"voices": [...], "default": ...}."""
    import pyttsx3

    engine = pyttsx3.init()
    try:
        vozes = list(engine.getProperty("voices"))
    finally:
        with contextlib.suppress(Exception):
            engine.stop()
    return {
        "voices": [{"id": str(v.id), "name": str(v.name or v.id)} for v in vozes],
        "default": escolher_voz_offline_padrao(vozes),
    }


def _falar_offline(texto: str, velocidade_wpm: int, voz_id: str | None) -> None:
    """(Thread de áudio) Fala o texto com pyttsx3 e espera terminar."""
    import pyttsx3

    engine = pyttsx3.init()
    try:
        engine.setProperty("rate", velocidade_wpm)
        if voz_id:
            engine.setProperty("voice", voz_id)
        engine.say(texto_para_sapi(texto))   # sem XML/SSML do SAPI (<audio src>, <silence>)
        engine.runAndWait()
    finally:
        with contextlib.suppress(Exception):
            engine.stop()


_vozes_lock = threading.Lock()
_vozes_cache: dict | None = None  # só guarda sucesso
_vozes_falha_ts: float | None = None
VOZES_RETRY_S = 30.0  # após falha, não tenta de novo antes disso


def obter_vozes_offline() -> dict | None:
    """Lista de vozes offline (cacheada após a 1ª chamada). None se indisponível."""
    global _vozes_cache, _vozes_falha_ts
    if _vozes_cache is not None:
        return _vozes_cache
    atual = PROGRAMACAO.tocando   # áudio agendado de vários minutos ocupa a thread de áudio: não espere 30 s
    if atual is not None and atual["tipo"] == "audio":   # nem grave "falha": é só uma janela ocupada
        return None
    with _vozes_lock:
        if _vozes_cache is not None:
            return _vozes_cache
        if _vozes_falha_ts is not None and (time.monotonic() - _vozes_falha_ts) < VOZES_RETRY_S:
            return None
        try:
            _vozes_cache = executar_no_audio(_listar_vozes_offline, timeout=30.0)
        except Exception:  # noqa: BLE001
            log.warning("Motor de voz offline indisponível.", exc_info=True)
            _vozes_falha_ts = time.monotonic()
            return None
        return _vozes_cache


def resolver_voz_offline(voz_id: Any) -> str | None:
    """Só aceita um id que exista na lista de vozes; qualquer outra coisa é ignorada."""
    if not isinstance(voz_id, str) or not voz_id or len(voz_id) > 512:
        return None
    vozes = obter_vozes_offline()
    if vozes and any(v["id"] == voz_id for v in vozes["voices"]):
        return voz_id
    return None


VOZES_ONLINE = {
    "francisca": "pt-BR-FranciscaNeural",
    "antonio": "pt-BR-AntonioNeural",
}
MOTORES = frozenset({"auto", "online", "offline"})
VELOCIDADE_OFFLINE_WPM = 165


# ---------------------------------------------------------------------------
# Spotify — Authorization Code (com client secret, guardado só no servidor)
# controlando o dispositivo via Spotify Connect, sem navegador aberto
# ---------------------------------------------------------------------------
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API = "https://api.spotify.com/v1"
SPOTIFY_SCOPES = (
    "user-read-playback-state user-modify-playback-state user-read-currently-playing"
)
SPOTIFY_TIMEOUT = (3.05, 8.0)  # (conexão, leitura) em segundos
SPOTIFY_DUCK_VOLUME = 12  # volume (%) da música durante o anúncio
PLAYER_CACHE_TTL_S = 2.0
BACKOFF_MAX_S = 120.0  # teto para respeitar Retry-After (429)

# v2.4.2 — volume da música depois do anúncio (B1): o restore tenta de novo, confirma pelo
# estado do player e nunca segura o anúncio por mais que SPOTIFY_RESTORE_ESPERA_MAX_S.
SPOTIFY_RESTORE_TENTATIVAS = 4
SPOTIFY_RESTORE_PAUSA_S = 1.5
# O /me/player do Spotify costuma devolver o estado defasado por 1 a 3 s depois de um PUT 204:
# a confirmação espera um pouco mais que a pausa entre tentativas e, depois de 2 PUTs aceitos
# (204) com a leitura ainda divergente, considera a leitura defasada (não repete até o teto).
SPOTIFY_RESTORE_CONFIRMA_ESPERA_S = 1.2
SPOTIFY_RESTORE_ACEITA_NA_TENTATIVA = 2
SPOTIFY_RESTORE_ESPERA_MAX_S = 8.0     # o que o anúncio espera pelo restore; o resto segue em 2º plano
SPOTIFY_VOLUME_TOLERANCIA = 2          # o Spotify arredonda o volume em alguns aparelhos
SPOTIFY_PENDENTE_VALIDADE_S = 30 * 60.0   # volume pendente mais velho que isto é descartado
SPOTIFY_PENDENTE_INTERVALO_S = 30.0       # pausa entre as reaplicações do volume pendente
SPOTIFY_REBUSCA_INTERVALO_S = 20.0        # no máximo uma releitura de aparelhos por 404 neste intervalo
SPOTIFY_API_LEITURA_VALIDADE_S = 120.0    # "api_ok": quanto tempo uma falha/sucesso de leitura vale
# ... e de comando: causas que NÃO se resolvem sozinhas ficam por 10 min (comandos são raros);
# falha transitória (limite 429, 5xx/rede) vale só 60 s, para o painel não ficar âmbar à toa.
SPOTIFY_API_COMANDO_VALIDADE_S = 600.0
SPOTIFY_API_COMANDO_TRANSITORIO_S = 60.0
CODIGOS_PERSISTENTES = frozenset({"premium", "sem_dispositivo", "reconectar"})
SPOTIFY_COMANDO_IP_MAX = 20               # comandos /api/spotify/* por IP ...
SPOTIFY_COMANDO_IP_JANELA_S = 10.0        # ... nesta janela (SEC-RT-SPOT-01)
SPOTIFY_PAUSA_CRITICA_INTERVALO_S = 2.0   # com 429 ativo, o bypass do pause da rota passa 1x neste intervalo

RE_DEVICE_ID = re.compile(r"[A-Za-z0-9]{1,128}")
# Client ID e Client Secret do Spotify são sempre 32 caracteres hexadecimais.
RE_CREDENCIAL_SPOTIFY = re.compile(r"[0-9a-fA-F]{32}")
MSG_CREDENCIAL_SPOTIFY = "Cole só o código (32 letras e números)."
_ASPAS_E_INVISIVEIS = "\"'`\u201c\u201d\u2018\u2019\u00ab\u00bb\u200b\u200c\u200d\ufeff\u00a0"
RE_URI_TRACK = re.compile(r"spotify:track:[A-Za-z0-9]{1,64}")
RE_URI_CONTEXTO = re.compile(r"spotify:(?:playlist|album|artist):[A-Za-z0-9]{1,64}")
# v2.7: atalhos guardados (listas.json): só playlist ou álbum, com o id de 22 caracteres base62 do Spotify
# O id é sensível a maiúsculas; o TIPO não (normalizado para minúsculas por quem lê: _uri_spotify).
_SPOTIFY_ID22 = r"[A-Za-z0-9]{22}"
_SPOTIFY_TIPO = r"(?i:playlist|album)"
RE_URI_SPOTIFY_ATALHO = re.compile(rf"spotify:({_SPOTIFY_TIPO}):({_SPOTIFY_ID22})")


def _uri_spotify(tipo: str, ident: str) -> str:
    """A URI canônica de um atalho: tipo em minúsculas, id como veio."""
    return f"spotify:{tipo.lower()}:{ident}"


def _criar_sessao_http() -> requests.Session:
    """
    Sessão única: pool de conexões + keep-alive para os hosts do Spotify.
    Repete só falhas de CONEXÃO (seguro para qualquer método). Sem cookies.
    """
    sessao = requests.Session()
    retry = Retry(total=2, connect=2, read=0, status=0, backoff_factor=0.2)
    adaptador = HTTPAdapter(pool_connections=4, pool_maxsize=16, max_retries=retry)
    sessao.mount("https://api.spotify.com", adaptador)
    sessao.mount("https://accounts.spotify.com", adaptador)
    sessao.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    sessao.headers["User-Agent"] = "Proclamare/2.2"
    return sessao


_http = _criar_sessao_http()


def _redirect_uri_do_ambiente() -> str:
    """
    Redirect URI FIXO (não derivado do Host da requisição). O Spotify só aceita
    https, ou http com IP de loopback (127.0.0.1 / [::1]; "localhost" não vale).
    Por isso o padrão é http://127.0.0.1:<porta>/spotify/callback e a conexão do
    Spotify deve ser feita no computador da loja, em http://127.0.0.1:<porta>.
    """
    padrao = f"http://127.0.0.1:{PORT}/spotify/callback"
    bruto = _env("SPOTIFY_REDIRECT", "").strip()
    if not bruto:
        return padrao
    ok = False
    try:
        partes = urlsplit(bruto)
        host = partes.hostname or ""
        _ = partes.port  # valida a porta
        ok = bool(host) and not partes.query and not partes.fragment and partes.username is None
        if ok and partes.scheme == "http":
            ok = host in ("127.0.0.1", "::1")
        elif ok:
            ok = partes.scheme == "https"
    except ValueError:
        ok = False
    if not ok:
        log.warning(
            "PROCLAMARE_SPOTIFY_REDIRECT inválida (use https:// ou http://127.0.0.1); "
            "usando o padrão."
        )
        return padrao
    return bruto


SPOTIFY_REDIRECT_URI = _redirect_uri_do_ambiente()


def spotify_redirect_uri() -> str:
    return SPOTIFY_REDIRECT_URI


def _redirect_e_loopback() -> bool:
    """True se o Redirect URI aponta para o próprio computador (loopback)."""
    return urlsplit(SPOTIFY_REDIRECT_URI).hostname in ("127.0.0.1", "::1")


# --- Token (renovação sem corrida) -------------------------------------------
_token_lock = threading.Lock()
_token_falha_ate = 0.0  # monotonic: após falha de renovação, não martela o accounts.spotify.com
TOKEN_FALHA_BACKOFF_S = 15.0
RE_CODIGO_OAUTH = re.compile(r"[a-z_]{1,40}")


def _token_valido(cfg: dict) -> str | None:
    token = cfg.get("spotify_access_token")
    if token and time.time() < cfg.get("spotify_expires_at", 0):
        return token
    return None


def spotify_get_token(stale: str | None = None) -> str | None:
    """
    Devolve um access token válido, renovando se preciso. `stale` é um token
    que a API acabou de rejeitar (401): força a renovação.
    Caminho rápido sem lock; renovação sob lock dedicado com double-check.
    """
    if stale is None:
        token = _token_valido(load_config())
        if token:
            return token
    with _token_lock:
        cfg = load_config()
        token = _token_valido(cfg)
        if token and token != stale:  # outra thread já renovou enquanto esperávamos
            return token
        return _spotify_refresh_token(cfg)


def _motivo_sem_token(cfg: dict) -> str:
    """
    Por que não há token (v2.4.2): "sem_config" (falta Client ID/Secret), "reconectar" (o Spotify
    recusou a autorização guardada: reautorizar), "sem_token" (nunca conectou) ou "rede" (há
    autorização guardada, mas a renovação falhou agora por rede/5xx/429: tentar de novo depois).
    """
    if not (cfg.get("spotify_client_id") and cfg.get("spotify_client_secret")):
        return "sem_config"
    if cfg.get("spotify_auth_erro") == "reconectar":
        return "reconectar"
    if not cfg.get("spotify_refresh_token"):
        return "sem_token"
    return "rede"


def _remover_chaves_config(*chaves: str) -> None:
    """Apaga chaves do config.json (grava só se alguma existia)."""
    with _config_lock:
        cfg = load_config()
        if any(k in cfg for k in chaves):
            for k in chaves:
                cfg.pop(k, None)
            _gravar_config_atomico(cfg)


def _erro_oauth(res: requests.Response) -> tuple[str | None, str]:
    """
    (código, descrição em minúsculas) do corpo de erro do accounts.spotify.com. O código só
    é aceito se tiver o formato [a-z_]{1,40}; a descrição serve apenas para classificar (nunca
    é logada nem devolvida ao cliente).
    """
    try:
        corpo = res.json()
    except ValueError:
        return None, ""
    if not isinstance(corpo, dict):
        return None, ""
    codigo = corpo.get("error")
    descricao = corpo.get("error_description")
    codigo = codigo if isinstance(codigo, str) and RE_CODIGO_OAUTH.fullmatch(codigo) else None
    return codigo, (descricao.lower()[:200] if isinstance(descricao, str) else "")


def _descartar_autorizacao(refresh_usado: str, codigo: str) -> None:
    """
    invalid_grant/invalid_client (v2.4.2, A2): o refresh token não serve mais (expira ~6 meses
    depois da autorização, ou foi revogado). Descarta os tokens e marca "reconectar" para a tela
    avisar, em vez de tentar a cada 15 s para sempre. Só descarta se o refresh token guardado
    ainda é o que falhou (uma reautorização feita enquanto isso não é apagada).
    """
    with _config_lock:
        cfg = load_config()
        if cfg.get("spotify_refresh_token") != refresh_usado:
            return
        for k in ("spotify_refresh_token", "spotify_access_token", "spotify_expires_at"):
            cfg.pop(k, None)
        cfg["spotify_auth_erro"] = "reconectar"
        _gravar_config_atomico(cfg)
    log.warning("O Spotify recusou a autorização guardada (%s): descartada; é preciso reconectar.", codigo)
    spotify_invalidar_cache()


def _spotify_refresh_token(cfg: dict) -> str | None:
    """Renova o token (chamar SEMPRE com _token_lock segurado)."""
    global _token_falha_ate
    refresh = cfg.get("spotify_refresh_token")
    client_id = cfg.get("spotify_client_id")
    client_secret = cfg.get("spotify_client_secret")
    if not (refresh and client_id and client_secret):
        return None
    if time.monotonic() < _token_falha_ate:
        return None
    try:
        res = _http.post(
            SPOTIFY_TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh},
            auth=(client_id, client_secret),
            timeout=SPOTIFY_TIMEOUT,
        )
    except requests.RequestException:
        # Falha de REDE: mantém o token (a internet volta) e só espera um pouco para tentar.
        log.warning("Falha de rede ao renovar o token do Spotify.")
        _token_falha_ate = time.monotonic() + TOKEN_FALHA_BACKOFF_S
        return None
    if res.status_code != 200:
        codigo, _ = _erro_oauth(res)
        if codigo in ("invalid_grant", "invalid_client"):
            _descartar_autorizacao(refresh, codigo)
            return None
        log.warning("Renovação do token do Spotify recusada (HTTP %s, %s).", res.status_code, codigo or "?")
        _token_falha_ate = time.monotonic() + TOKEN_FALHA_BACKOFF_S
        return None
    try:
        data = res.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
        log.warning("Resposta inesperada ao renovar o token do Spotify.")
        _token_falha_ate = time.monotonic() + TOKEN_FALHA_BACKOFF_S
        return None
    mudancas: dict[str, Any] = {
        "spotify_access_token": data["access_token"],
        "spotify_expires_at": time.time() + int(data.get("expires_in", 3600)) - 60,
    }
    if data.get("refresh_token"):
        mudancas["spotify_refresh_token"] = data["refresh_token"]
    with _config_lock:
        atual = load_config()
        if atual.get("spotify_refresh_token") != refresh:
            return None   # desconectado (ou reconectado) enquanto renovava: o resultado velho não ressuscita nada
        atual.update(mudancas)
        _gravar_config_atomico(atual)
    return data["access_token"]


# --- Chamadas à API (com tratamento de 401/429) ------------------------------
_backoff_ate = 0.0  # monotonic: enquanto no futuro, não chama a API (429 recente)


def _em_backoff() -> bool:
    return time.monotonic() < _backoff_ate


_critico_lock = threading.Lock()
_critico_ate = 0.0   # monotonic: até quando o bypass do backoff pela ROTA de pause fica "gasto"


def _bypass_critico_da_rota() -> bool:
    """
    O pause vindo da rota HTTP fura o backoff 429, mas no máximo 1 vez a cada
    SPOTIFY_PAUSA_CRITICA_INTERVALO_S enquanto o backoff vale (SEC-RT-SPOT-01): um cliente em
    laço não pode martelar o Spotify ignorando o Retry-After. O duck/restore internos não passam
    por aqui (são poucas chamadas, limitadas pelo próprio fluxo do anúncio).
    """
    global _critico_ate
    if not _em_backoff():
        return True
    with _critico_lock:
        agora = time.monotonic()
        if agora < _critico_ate:
            return False
        _critico_ate = agora + SPOTIFY_PAUSA_CRITICA_INTERVALO_S
        return True


def _espera_backoff_s() -> int:
    """Segundos (inteiro >= 1) que faltam para o fim do backoff 429."""
    return max(1, math.ceil(_backoff_ate - time.monotonic()))


def _registrar_429(res: requests.Response) -> None:
    global _backoff_ate
    try:
        espera = float(res.headers.get("Retry-After", "5"))
    except ValueError:
        espera = 5.0
    if not math.isfinite(espera):
        espera = 5.0
    espera = max(1.0, min(espera, BACKOFF_MAX_S))
    _backoff_ate = time.monotonic() + espera
    log.warning("Spotify respondeu 429; pausando chamadas por %.0f s.", espera)


RE_REASON_SPOTIFY = re.compile(r"[A-Z][A-Z_]{1,40}")


def _motivo_do_corpo(corpo: dict, status: int) -> str | None:
    """
    `error.reason` do corpo de erro da API do Spotify (NO_ACTIVE_DEVICE, PREMIUM_REQUIRED,
    QUOTA_EXCEEDED...), só se tiver o formato esperado. 403 sem reason cuja mensagem fala de
    Premium/cadastro no painel vira PREMIUM_REQUIRED; 404 "no active device" vira NO_ACTIVE_DEVICE.
    A mensagem do Spotify serve só para classificar; nunca é repassada ao cliente.
    """
    erro = corpo.get("error")
    reason: str | None = None
    msg = ""
    if isinstance(erro, dict):
        r = erro.get("reason")
        if isinstance(r, str) and RE_REASON_SPOTIFY.fullmatch(r.upper()):
            reason = r.upper()
        m = erro.get("message")
        msg = m.lower() if isinstance(m, str) else ""
    elif isinstance(erro, str):
        msg = erro.lower()
    if reason is None and status == 403 and any(p in msg for p in ("premium", "registered", "dashboard")):
        reason = "PREMIUM_REQUIRED"
    if reason is None and status == 404 and "no active device" in msg:
        reason = "NO_ACTIVE_DEVICE"
    return reason


# Mensagens por causa (v2.4.2, B2). "codigo" é uma lista FIXA para o front decidir o que mostrar.
CODIGOS_SPOTIFY = ("premium", "sem_dispositivo", "reconectar", "limite", "indisponivel")
MSG_SPOTIFY = {
    "reconectar": "Reconecte o Spotify.",
    "sem_dispositivo": "Abra o Spotify no PC da loja e dê play uma vez.",
    "premium": ("A conta precisa ser Premium e ser a dona do app ou estar em User Management "
                "no painel do Spotify."),
    "indisponivel": "Spotify indisponível agora.",
}
MSG_SPOTIFY_RECUSOU = "O Spotify não aceitou este comando agora."
# 403 com reason que NÃO é falta de Premium: restrição do momento/aparelho (pausar o que já está
# pausado, aparelho sem controle de volume...). Não é problema de conta.
MSG_SPOTIFY_APARELHO = "Este aparelho do Spotify não aceita esse comando (volume/controle remoto)."
_REASONS_APARELHO = frozenset({"VOLUME_CONTROL_DISALLOW", "DEVICE_NOT_CONTROLLABLE", "REMOTE_CONTROL_DISALLOW"})


def classificar_erro_spotify(status: int, reason: str | None) -> tuple[str, str, int | None]:
    """
    (codigo, mensagem, espera_s) de uma resposta de falha da API do Spotify.
    status: 0 = falha de rede/timeout, 401 = sem autorização, 403, 404, 429, 5xx...
    `reason`: error.reason do Spotify OU o motivo interno de spotify_api ("sem_token",
    "sem_config", "reconectar", "rede"). Só os códigos de CODIGOS_SPOTIFY saem daqui.
    """
    if status == 401 or reason in ("sem_token", "sem_config", "reconectar"):
        return "reconectar", MSG_SPOTIFY["reconectar"], None
    if status == 429:
        espera = _espera_backoff_s()
        return "limite", f"Aguarde {espera} s.", espera
    if reason == "NO_ACTIVE_DEVICE" or status == 404:
        return "sem_dispositivo", MSG_SPOTIFY["sem_dispositivo"], None
    if status == 403:
        if reason in (None, "PREMIUM_REQUIRED"):
            return "premium", MSG_SPOTIFY["premium"], None
        if reason in _REASONS_APARELHO:
            return "indisponivel", MSG_SPOTIFY_APARELHO, None
        return "indisponivel", MSG_SPOTIFY_RECUSOU, None
    return "indisponivel", MSG_SPOTIFY["indisponivel"], None


def _corpo_erro_spotify(status: int, reason: str | None) -> dict[str, Any]:
    """{"error", "codigo"[, "espera_s"]} para as respostas JSON de falha."""
    codigo, mensagem, espera = classificar_erro_spotify(status, reason)
    corpo: dict[str, Any] = {"error": mensagem, "codigo": codigo}
    if espera is not None:
        corpo["espera_s"] = espera
    return corpo


# Último resultado conhecido da API (alimenta "api_ok" do /api/spotify/status). Leitura (GET) e
# comando (PUT/POST) ficam em compartimentos separados: o polling de now-playing (200) não pode
# apagar a falha de um comando ("premium") a cada 2 s.
_api_resultado_lock = threading.Lock()
_api_resultado: dict[str, tuple[bool, str | None, float]] = {}   # "leitura"|"comando" -> (ok, codigo, monotonic)


def _registrar_api(method: str, status: int, reason: str | None) -> None:
    ok = status in (200, 201, 202, 204)
    codigo = None if ok else classificar_erro_spotify(status, reason)[0]
    with _api_resultado_lock:
        _api_resultado["leitura" if method == "GET" else "comando"] = (ok, codigo, time.monotonic())


def spotify_api_ok() -> tuple[bool | None, str | None]:
    """(api_ok, codigo da falha): None = nenhuma chamada recente; False = a falha mais recente vale."""
    agora = time.monotonic()
    visto = False
    pior: tuple[float, str | None] | None = None
    with _api_resultado_lock:
        for chave, (ok, codigo, ts) in _api_resultado.items():
            if chave == "leitura":
                validade = SPOTIFY_API_LEITURA_VALIDADE_S
            elif ok or codigo in CODIGOS_PERSISTENTES:
                validade = SPOTIFY_API_COMANDO_VALIDADE_S
            else:   # limite/indisponivel: passa sozinho, não deixa o painel âmbar por 10 min
                validade = SPOTIFY_API_COMANDO_TRANSITORIO_S
            if agora - ts > validade:
                continue
            visto = True
            if not ok and (pior is None or ts > pior[0]):
                pior = (ts, codigo)
    if pior is not None:
        return False, pior[1]
    return (True if visto else None), None


def spotify_api(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict | None = None,
    critico: bool = False,
    registrar_resultado: bool = True,
    rotulo_log: str | None = None,
) -> tuple[dict, int, str | None]:
    """
    Chama a API do Spotify. Devolve (json, status, reason):
      status 401 = sem autorização (reason "sem_config"|"sem_token"|"reconectar"), 0 = falha de
      rede/timeout (reason "rede"), 429 = limite (respeita Retry-After), senão o HTTP do Spotify
      com `reason` = error.reason do corpo (NO_ACTIVE_DEVICE, PREMIUM_REQUIRED...) ou None.
    Parâmetros de query SEMPRE via `params` (nunca concatenados na URL).
    `critico=True` ignora o backoff 429 (pausar a música e restaurar o volume depois do anúncio
    não podem ficar presos atrás de um 429 de leitura); um 429 novo continua sendo registrado.
    `registrar_resultado=False` (v2.7): consulta acessória (ex.: o nome de uma playlist) cuja falha não pode
    deixar o painel âmbar ("api_ok" do /api/spotify/status); o backoff 429 continua valendo.
    """
    if not critico and _em_backoff():
        return {}, 429, None
    token = spotify_get_token()
    if not token:
        motivo = _motivo_sem_token(load_config())
        if motivo == "rede":
            if registrar_resultado:
                _registrar_api(method, 0, "rede")
            return {}, 0, "rede"
        if motivo == "reconectar" and registrar_resultado:
            _registrar_api(method, 401, "reconectar")
        return {}, 401, motivo
    res: requests.Response | None = None
    for tentativa in (0, 1):
        try:
            res = _http.request(
                method,
                SPOTIFY_API + path,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=SPOTIFY_TIMEOUT,
            )
        except requests.RequestException:
            # `rotulo_log`: consultas cujo caminho leva um id (ex.: /playlists/<id>) logam só um rótulo fixo
            log.warning("Falha de rede ao chamar o Spotify (%s %s).", method, rotulo_log or path)
            if registrar_resultado:
                _registrar_api(method, 0, "rede")
            return {}, 0, "rede"
        if res.status_code == 401 and tentativa == 0:
            novo = spotify_get_token(stale=token)  # token revogado/expirado antes da hora
            if novo and novo != token:
                token = novo
                continue
        break
    assert res is not None
    status = res.status_code
    corpo: dict = {}
    if status != 204 and res.content:
        try:
            lido = res.json()
            corpo = lido if isinstance(lido, dict) else {}
        except ValueError:
            corpo = {}
    reason = _motivo_do_corpo(corpo, status) if status >= 400 else None
    if reason == "QUOTA_EXCEEDED":
        log.warning("Spotify: QUOTA_EXCEEDED (cota do app esgotada; HTTP %s).", status)
    if status == 429:
        _registrar_429(res)
        if registrar_resultado:
            _registrar_api(method, 429, reason)
        return {}, 429, reason
    if registrar_resultado:
        _registrar_api(method, status, reason)
    return corpo, status, reason


def spotify_active_device_id() -> str | None:
    return load_config().get("spotify_device_id")


# --- Cache compartilhado do estado do player ---------------------------------
_player_lock = threading.Lock()  # serializa a busca: N clientes -> 1 chamada
_player_estado: dict[str, Any] = {"data": None, "ts": None, "gen": 0, "erro": None}


def spotify_invalidar_cache() -> None:
    """Descarta o cache (chamar após qualquer comando de controle)."""
    _player_estado["gen"] += 1
    _player_estado["ts"] = None


def _cache_player_fresco() -> bool:
    ts = _player_estado["ts"]
    return ts is not None and (time.monotonic() - ts) < PLAYER_CACHE_TTL_S


def spotify_player_state(force: bool = False) -> dict | None:
    """
    GET /me/player com cache de ~2 s compartilhado entre todos os clientes.
    Em 429 (ou backoff ativo) devolve o último valor em cache em vez de
    martelar a API. Devolve {} se não há player ativo e None se indisponível
    (nesse caso `_player_estado["erro"]` guarda (status, reason) da falha).
    """
    if not force and _cache_player_fresco():
        return _player_estado["data"]
    with _player_lock:
        if not force and _cache_player_fresco():  # double-check
            return _player_estado["data"]
        if _em_backoff():
            return _player_estado["data"]
        geracao = _player_estado["gen"]
        data, status, reason = spotify_api("GET", "/me/player")
        if status == 429:
            return _player_estado["data"]
        resultado: dict | None = data if status == 200 and data else ({} if status in (200, 204) else None)
        # Só grava se nenhum comando invalidou o cache durante a chamada.
        if geracao == _player_estado["gen"]:
            _player_estado["data"] = resultado
            _player_estado["ts"] = time.monotonic()
        _player_estado["erro"] = None if resultado is not None else (status, reason)
        return resultado


_rebusca_lock = threading.Lock()
_rebusca_ate = 0.0   # monotonic: no máximo uma releitura de aparelhos por intervalo


def _reachar_dispositivo(id_antigo: str) -> str | None:
    """
    B7 — o Spotify respondeu 404 ("Device not found") ao aparelho salvo: o id mudou (app
    reinstalado/relogado). Relê os aparelhos e procura UM com o mesmo nome e tipo salvos; se
    achar, grava o id novo e o devolve. None = não achou (ou releitura recente demais).
    """
    global _rebusca_ate
    cfg = load_config()
    if cfg.get("spotify_device_id") != id_antigo:
        return None
    nome = cfg.get("spotify_device_nome")
    tipo = cfg.get("spotify_device_tipo")
    if not isinstance(nome, str) or not nome:
        return None
    with _rebusca_lock:
        if time.monotonic() < _rebusca_ate:
            return None
        _rebusca_ate = time.monotonic() + SPOTIFY_REBUSCA_INTERVALO_S
    dados, status, _ = spotify_api("GET", "/me/player/devices")
    if status in (0, 429):
        # a releitura nem chegou a acontecer (rede/limite): não gasta a janela de 20 s
        with _rebusca_lock:
            _rebusca_ate = 0.0
        return None
    if status != 200:
        return None
    achados = [
        d["id"] for d in dados.get("devices") or []
        if isinstance(d, dict) and isinstance(d.get("id"), str) and RE_DEVICE_ID.fullmatch(d["id"])
        and d["id"] != id_antigo and d.get("name") == nome and not d.get("is_restricted")
        and (not isinstance(tipo, str) or not tipo or d.get("type") == tipo)
    ]
    if len(achados) != 1:   # nenhum, ou ambíguo: não adivinha
        return None
    update_config(spotify_device_id=achados[0])
    log.info("Aparelho do Spotify reencontrado por nome/tipo (o id mudou).")
    return achados[0]


def spotify_comando(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict | None = None,
    critico: bool = False,
) -> tuple[int, str | None]:
    """
    Executa um comando de controle e invalida o cache do player. Devolve (status, reason)
    (sucesso = status 200/204; veja spotify_api). 404 com o aparelho salvo: relê os aparelhos,
    reencontra por nome/tipo e repete UMA vez com o id novo.
    """
    _, status, reason = spotify_api(method, path, params=params, json_body=json_body, critico=critico)
    if status == 404 and params and isinstance(params.get("device_id"), str):
        novo = _reachar_dispositivo(params["device_id"])
        if novo:
            _, status, reason = spotify_api(
                method, path, params={**params, "device_id": novo}, json_body=json_body, critico=critico
            )
    spotify_invalidar_cache()
    return status, reason


def _sucesso(status: int) -> bool:
    return status in (200, 204)


# --- Imagens e objetos enxutos -------------------------------------------------
def escolher_imagem(imagens: Any, largura_min: int = 64) -> str | None:
    """Menor imagem com largura >= largura_min; senão a última da lista."""
    if not isinstance(imagens, list):
        return None
    validas = [i for i in imagens if isinstance(i, dict) and i.get("url")]
    if not validas:
        return None
    grandes = [i for i in validas if isinstance(i.get("width"), int) and i["width"] >= largura_min]
    if grandes:
        return min(grandes, key=lambda i: i["width"])["url"]
    return validas[-1]["url"]


def _nomes_artistas(artistas: Any) -> str:
    if not isinstance(artistas, list):
        return ""
    return ", ".join(a["name"] for a in artistas if isinstance(a, dict) and a.get("name"))


# --- Cache pequeno de buscas (evita repetir a mesma busca digitada) ----------
_busca_lock = threading.Lock()
_busca_cache: dict[str, tuple[float, dict]] = {}
BUSCA_TTL_S = 60.0
BUSCA_MAX_ENTRADAS = 64


# --- Fluxo OAuth (state aleatório, uso único, só em memória) -----------------
_oauth_lock = threading.Lock()
_oauth_states: dict[str, tuple[float, str]] = {}  # state -> (expira em monotonic, redirect_uri)
OAUTH_STATE_TTL_S = 600.0
OAUTH_STATES_MAX = 20


def _oauth_novo_state(redirect_uri: str) -> str:
    state = secrets.token_urlsafe(24)
    agora = time.monotonic()
    with _oauth_lock:
        for k in [k for k, (exp, _) in _oauth_states.items() if exp < agora]:
            del _oauth_states[k]
        while len(_oauth_states) >= OAUTH_STATES_MAX:  # descarta o mais antigo
            del _oauth_states[min(_oauth_states, key=lambda k: _oauth_states[k][0])]
        _oauth_states[state] = (agora + OAUTH_STATE_TTL_S, redirect_uri)
    return state


def _oauth_consumir_state(recebido: str) -> str | None:
    """Valida (compare_digest) e CONSOME o state; devolve o redirect_uri usado no login."""
    agora = time.monotonic()
    achado: str | None = None
    with _oauth_lock:
        for k in list(_oauth_states):
            # compare_digest em todos, sem curto-circuito, para não vazar por tempo
            if secrets.compare_digest(k.encode(), recebido.encode()):
                achado = k
        if achado is None:
            return None
        expira, redirect_uri = _oauth_states.pop(achado)
    return redirect_uri if expira >= agora else None


# Destinos FIXOS dos redirecionamentos do fluxo OAuth (v2.4.2). Nada vindo do cliente/Spotify entra
# na URL: o app.js só reconhece estes valores.
SPOTIFY_LOGIN_FALHAS = frozenset({"nocfg", "local", "rate"})
SPOTIFY_CALLBACK_MOTIVOS = frozenset({"negado", "state", "credenciais", "rede", "redirect"})


def _redirect_login(falha: str) -> Any:
    """/spotify/login que não pode seguir: volta ao painel com /?spotify=nocfg|local|rate."""
    assert falha in SPOTIFY_LOGIN_FALHAS
    return redirect(f"/?spotify={falha}")


def _redirect_erro_callback(motivo: str) -> Any:
    """/spotify/callback que falhou: /?spotify=error&motivo=negado|state|credenciais|rede|redirect."""
    assert motivo in SPOTIFY_CALLBACK_MOTIVOS
    log.warning("Conexão com o Spotify não concluída (motivo: %s).", motivo)
    return redirect(f"/?spotify=error&motivo={motivo}")


@app.get("/spotify/login")
def spotify_login() -> Any:
    cfg = load_config()
    if not _sessao_ok(cfg):  # local OU sessão válida (o callback não exige sessão: cookie Strict)
        # Sem PIN/sessão: o painel (que é público) mostra a tela do PIN e o aviso de "só no PC".
        return _redirect_login("local")
    if not (cfg.get("spotify_client_id") and cfg.get("spotify_client_secret")):
        return _redirect_login("nocfg")
    if _redirect_e_loopback() and not _requisicao_local():
        # O Spotify devolveria o navegador para 127.0.0.1 DESTE aparelho, não da loja.
        return _redirect_login("local")
    if excedeu_limite("login:" + (request.remote_addr or "?"), 5, 60.0):
        return _redirect_login("rate")
    client_id = cfg["spotify_client_id"]
    redirect_uri = spotify_redirect_uri()
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SPOTIFY_SCOPES,
        "state": _oauth_novo_state(redirect_uri),
    }
    return redirect(SPOTIFY_AUTH_URL + "?" + urlencode(params))


@app.get("/spotify/callback")
def spotify_callback() -> Any:
    global _token_falha_ate
    state = request.args.get("state", "")
    redirect_uri = _oauth_consumir_state(state) if 0 < len(state) <= 128 else None
    if redirect_uri is None:  # state ausente/errado/expirado/já usado
        return _redirect_erro_callback("state")

    code = request.args.get("code", "")
    if request.args.get("error") or not code or len(code) > 1024:  # ex.: error=access_denied
        return _redirect_erro_callback("negado")

    cfg = load_config()
    client_id = cfg.get("spotify_client_id")
    client_secret = cfg.get("spotify_client_secret")
    if not (client_id and client_secret):
        return _redirect_erro_callback("credenciais")

    try:
        res = _http.post(
            SPOTIFY_TOKEN_URL,
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            auth=(client_id, client_secret),
            timeout=SPOTIFY_TIMEOUT,
        )
    except requests.RequestException:
        return _redirect_erro_callback("rede")
    if res.status_code != 200:
        codigo, descricao = _erro_oauth(res)
        log.warning("Troca do code do Spotify recusada (HTTP %s, %s).", res.status_code, codigo or "?")
        if codigo == "invalid_client":
            return _redirect_erro_callback("credenciais")
        if codigo == "invalid_grant":
            return _redirect_erro_callback("redirect" if "redirect" in descricao else "state")
        return _redirect_erro_callback("rede")
    try:
        data = res.json()
    except ValueError:
        data = {}
    if (not isinstance(data, dict) or not isinstance(data.get("access_token"), str)
            or not isinstance(data.get("refresh_token"), str) or not data["refresh_token"]):
        # 200 sem refresh_token não serve: a conexão morreria em 1 h.
        return _redirect_erro_callback("rede")

    with _config_lock:   # tokens novos + limpeza do aviso "reconectar", numa gravação só
        cfg = load_config()
        cfg["spotify_access_token"] = data["access_token"]
        cfg["spotify_refresh_token"] = data["refresh_token"]
        try:
            cfg["spotify_expires_at"] = time.time() + int(data.get("expires_in", 3600)) - 60
        except (TypeError, ValueError, OverflowError):
            cfg["spotify_expires_at"] = time.time() + 3540
        cfg.pop("spotify_auth_erro", None)
        _gravar_config_atomico(cfg)
    _token_falha_ate = 0.0
    with _api_resultado_lock:
        _api_resultado.clear()   # a lâmpada recomeça do zero com a autorização nova
    spotify_invalidar_cache()
    return redirect("/?spotify=connected")


@app.get("/api/spotify/config")
def spotify_config_get() -> Response:
    cfg = load_config()
    # O client_secret NUNCA é devolvido: só um booleano informando se existe.
    return jsonify(
        {
            "client_id": cfg.get("spotify_client_id", ""),
            "has_secret": bool(cfg.get("spotify_client_secret")),
            "configured": bool(cfg.get("spotify_client_id") and cfg.get("spotify_client_secret")),
            "redirect_uri": spotify_redirect_uri(),
        }
    )


def _limpar_credencial_spotify(valor: str) -> str:
    """Tira espaços, aspas (retas e tipográficas) e invisíveis das pontas (colar do painel do Spotify)."""
    for _ in range(4):
        novo = valor.strip().strip(_ASPAS_E_INVISIVEIS)
        if novo == valor:
            break
        valor = novo
    return valor


def _credencial_spotify_ou_none(bruto: Any, campo: str) -> str | None:
    """
    Client ID/Secret: 32 caracteres hexadecimais (sem espaço/aspas nas pontas). Devolve o valor
    limpo em minúsculas, None se veio vazio (= manter o atual) ou levanta ErroSpotifyCredencial.
    """
    if not isinstance(bruto, str) or len(bruto) > 256:
        raise ErroSpotifyCredencial(campo)
    limpo = _limpar_credencial_spotify(bruto)
    if not limpo:
        return None
    if not RE_CREDENCIAL_SPOTIFY.fullmatch(limpo):
        raise ErroSpotifyCredencial(campo)
    return limpo.lower()


class ErroSpotifyCredencial(ErroApi):
    """Client ID/Secret fora do formato: 400 com o campo que errou (nunca repete o valor)."""

    def __init__(self, campo: str) -> None:
        super().__init__(MSG_CREDENCIAL_SPOTIFY, 400)
        self.campo = campo


@app.errorhandler(ErroSpotifyCredencial)
def _tratar_erro_credencial(e: ErroSpotifyCredencial) -> tuple[Response, int]:
    return jsonify({"ok": False, "error": e.mensagem, "campo": e.campo}), 400


@app.post("/api/spotify/config")
def spotify_config_set() -> Response:
    global _token_falha_ate
    # O client_secret trafega em texto claro (HTTP): só aceita configurar no
    # próprio computador da loja (loopback), nunca a partir de outro aparelho.
    if not _requisicao_local():
        raise ErroApi("Configure o Spotify no computador da loja.", 403)
    data = corpo_json()
    exigir_chaves(data, ("client_id", "client_secret"))
    changes: dict[str, str] = {}
    # Valida os DOIS antes de gravar qualquer um (tudo ou nada). Vazio = manter o atual.
    if "client_id" in data:
        valor = _credencial_spotify_ou_none(data["client_id"], "client_id")
        if valor:
            changes["spotify_client_id"] = valor
    if data.get("client_secret"):
        valor = _credencial_spotify_ou_none(data["client_secret"], "client_secret")
        if valor:
            changes["spotify_client_secret"] = valor
    if changes:
        update_config(**changes)
        _token_falha_ate = 0.0  # credenciais novas: permite tentar renovar já
    auditar("spotify_config", campos=len(changes))   # só QUANTOS campos, nunca os valores
    return jsonify({"ok": True})


@app.post("/api/spotify/desconectar")
def spotify_desconectar() -> Response:
    """
    v2.7.1 (P11). SÓ LOCAL. Esquece a autorização do Spotify neste PC: apaga o refresh token, o access token
    e o dispositivo escolhido. {"apagar_credenciais": true} apaga também o Client ID e o Client Secret
    (padrão false: o dono só reconecta). O acesso do app continua listado em spotify.com/account/apps até
    ser removido lá (o painel orienta).
    """
    global _token_falha_ate
    if not _requisicao_local():
        raise ErroApi("Desconecte o Spotify no computador da loja.", 403)
    corpo = corpo_json()
    if set(corpo) - {"apagar_credenciais"}:
        raise ErroApi("Campo desconhecido.")
    apagar = corpo.get("apagar_credenciais", False)
    if not isinstance(apagar, bool):
        raise ErroApi("Campo 'apagar_credenciais' inválido.")
    chaves = ["spotify_refresh_token", "spotify_access_token", "spotify_expires_at", "spotify_auth_erro",
              "spotify_device_id", "spotify_device_nome", "spotify_device_tipo", "spotify_volume_pendente"]
    if apagar:
        chaves += ["spotify_client_id", "spotify_client_secret"]
    with _token_lock:   # espera uma renovação em andamento terminar (ela só grava se o refresh ainda for o dela)
        _remover_chaves_config(*chaves)
    _token_falha_ate = 0.0
    spotify_invalidar_cache()
    auditar("spotify_desconectar", credenciais_apagadas=int(apagar))
    log.info("Spotify desconectado pelo PC da loja%s.", " (credenciais apagadas)" if apagar else "")
    return jsonify({"ok": True})


MSG_API_ERRO_STATUS = {
    **MSG_SPOTIFY,
    "limite": "O Spotify pediu para aguardar um pouco antes de novas chamadas.",
}


def _limitar_comandos_spotify() -> None:
    """Limite por IP nas rotas de comando (cada uma chega ao api.spotify.com): 429 se passar."""
    if excedeu_limite("spcmd:" + (request.remote_addr or "?"), SPOTIFY_COMANDO_IP_MAX,
                      SPOTIFY_COMANDO_IP_JANELA_S):
        raise ErroApi("Muitos comandos seguidos. Aguarde um instante.", 429)


@app.get("/api/spotify/status")
def spotify_status() -> Response:
    """
    {"connected", "reason": ok|reconectar|rede|sem_config|sem_token, "device_id", "api_ok",
    "api_codigo", "api_error", "volume_pendente"}. "connected" é falso só quando é preciso agir
    (configurar/conectar/reconectar); falha de REDE na renovação mantém connected:true,
    reason "rede" (o painel não pode piscar para "Conecte o Spotify" num blip de internet).
    """
    cfg = load_config()
    tem_credenciais = bool(cfg.get("spotify_client_id") and cfg.get("spotify_client_secret"))
    if tem_credenciais and cfg.get("spotify_refresh_token"):
        if spotify_get_token() is not None:
            reason = "ok"
        else:
            reason = _motivo_sem_token(load_config())   # "reconectar" (descartado agora) ou "rede"
    else:
        reason = _motivo_sem_token(cfg)                 # sem_config | reconectar | sem_token
    api_ok, api_codigo = spotify_api_ok() if reason in ("ok", "rede") else (None, None)
    return jsonify(
        {
            "connected": reason in ("ok", "rede"),
            "reason": reason,
            "device_id": cfg.get("spotify_device_id"),
            "api_ok": api_ok,
            "api_codigo": api_codigo,
            "api_error": MSG_API_ERRO_STATUS.get(api_codigo) if api_codigo else None,
            "volume_pendente": _volume_pendente() is not None,
        }
    )


def _dispositivo_publico(d: dict) -> dict[str, Any]:
    """Aparelho Connect enxuto (lista branca). `supports_volume` ausente: deduz pelo volume lido."""
    suporta = d.get("supports_volume")
    return {
        "id": d.get("id"),
        "name": d.get("name"),
        "type": d.get("type"),
        "is_active": bool(d.get("is_active")),
        "volume_percent": d.get("volume_percent"),
        "is_restricted": bool(d.get("is_restricted")),
        "supports_volume": suporta if isinstance(suporta, bool) else d.get("volume_percent") is not None,
    }


def _aprender_nome_do_aparelho(dispositivos: list[dict]) -> None:
    """
    Instalações da versão anterior salvaram o id do aparelho sem nome/tipo (só o /activate os
    gravava). Se o aparelho salvo aparece na lista e ainda não há nome/tipo, grava-os agora: é o
    que permite reencontrá-lo por nome/tipo quando o Spotify trocar o id dele (404).
    """
    cfg = load_config()
    salvo = cfg.get("spotify_device_id")
    if not salvo or (cfg.get("spotify_device_nome") and cfg.get("spotify_device_tipo")):
        return
    alvo = next((d for d in dispositivos if d.get("id") == salvo), None)
    if alvo is None:
        return
    mudancas: dict[str, Any] = {}
    nome, tipo = alvo.get("name"), alvo.get("type")
    if isinstance(nome, str) and nome and not cfg.get("spotify_device_nome"):
        mudancas["spotify_device_nome"] = nome[:128]
    if isinstance(tipo, str) and tipo and not cfg.get("spotify_device_tipo"):
        mudancas["spotify_device_tipo"] = tipo[:64]
    if mudancas:
        update_config(**mudancas)


@app.get("/api/spotify/devices")
def spotify_devices() -> Response:
    data, status, reason = spotify_api("GET", "/me/player/devices")
    if status != 200:
        return jsonify({"ok": False, "devices": [], **_corpo_erro_spotify(status, reason)})
    brutos = [d for d in data.get("devices") or [] if isinstance(d, dict) and d.get("id")]
    _aprender_nome_do_aparelho(brutos)
    return jsonify({"ok": True, "devices": [_dispositivo_publico(d) for d in brutos]})


@app.post("/api/spotify/activate")
def spotify_activate() -> Response:
    """
    Salva o aparelho e tenta transferir a reprodução para ele (sem dar play).
    -> {"ok": true, "transferred": bool, "warning": str|null}: o aparelho fica salvo mesmo que a
    transferência falhe (app fechado, sem Premium...); nesse caso "warning" diz por quê.
    """
    _limitar_comandos_spotify()
    corpo = corpo_json()
    exigir_chaves(corpo, ("device_id",))
    device_id = validar_str(corpo.get("device_id"), "device_id", 128, min_len=1)
    if not RE_DEVICE_ID.fullmatch(device_id):
        raise ErroApi("Campo 'device_id' inválido.")

    aviso: str | None = None
    alvo: dict | None = None
    dados, status_lista, _ = spotify_api("GET", "/me/player/devices")
    if status_lista == 200:
        alvo = next(
            (d for d in dados.get("devices") or [] if isinstance(d, dict) and d.get("id") == device_id), None
        )
    if alvo is not None and alvo.get("is_restricted"):
        # aparelho que não aceita controle remoto: NÃO vira o aparelho salvo (play/pause/volume dariam 403)
        return jsonify({"ok": True, "transferred": False,
                        "warning": "Este aparelho não aceita controle remoto pelo Spotify. Escolha outro."})

    # Uma única gravação atômica: id + nome/tipo (permitem reencontrar o aparelho se o Spotify
    # trocar o id dele, B7). Trocar de aparelho esquece o nome/tipo do anterior.
    with _config_lock:
        cfg = load_config()
        if cfg.get("spotify_device_id") != device_id:
            cfg.pop("spotify_device_nome", None)
            cfg.pop("spotify_device_tipo", None)
        cfg["spotify_device_id"] = device_id
        if alvo is not None:
            nome, tipo = alvo.get("name"), alvo.get("type")
            if isinstance(nome, str) and nome:
                cfg["spotify_device_nome"] = nome[:128]
            if isinstance(tipo, str) and tipo:
                cfg["spotify_device_tipo"] = tipo[:64]
        _gravar_config_atomico(cfg)
    if alvo is not None and not _dispositivo_publico(alvo)["supports_volume"]:
        aviso = "Este aparelho não aceita controle de volume: a música não será abaixada nos anúncios."
    status, reason = spotify_comando(
        "PUT", "/me/player", json_body={"device_ids": [device_id], "play": False}
    )
    transferido = _sucesso(status)
    if not transferido:
        aviso = _corpo_erro_spotify(status, reason)["error"]
    return jsonify({"ok": True, "transferred": transferido, "warning": aviso})


@app.get("/api/spotify/search")
def spotify_search() -> Response | tuple[Response, int]:
    q = validar_str(request.args.get("q", ""), "q", 100, min_len=1)
    agora = time.monotonic()
    if excedeu_limite("busca:" + (request.remote_addr or "?"), 4, 2.0):  # protege a cota do Spotify
        return jsonify({"ok": False, "tracks": [], "playlists": [], "error": "Muitas buscas seguidas."}), 429
    with _busca_lock:
        hit = _busca_cache.get(q)
    if hit and (agora - hit[0]) < BUSCA_TTL_S:
        return jsonify(hit[1])

    data, status, reason = spotify_api(
        "GET", "/search", params={"q": q, "type": "track,playlist", "limit": 8}
    )
    if status != 200:
        return jsonify({"ok": False, "tracks": [], "playlists": [], **_corpo_erro_spotify(status, reason)})

    tracks = []
    for t in (data.get("tracks") or {}).get("items") or []:
        if isinstance(t, dict) and t.get("uri"):
            tracks.append(
                {
                    "name": t.get("name") or "",
                    "artists": _nomes_artistas(t.get("artists")),
                    "uri": t["uri"],
                    "image": escolher_imagem((t.get("album") or {}).get("images")),
                    "duration_ms": t.get("duration_ms") if isinstance(t.get("duration_ms"), int) else 0,
                }
            )
    playlists = []
    for p in (data.get("playlists") or {}).get("items") or []:
        if isinstance(p, dict) and p.get("uri"):
            dono = p.get("owner") or {}
            playlists.append(
                {
                    "name": p.get("name") or "",
                    "uri": p["uri"],
                    "image": escolher_imagem(p.get("images")),
                    "owner": dono.get("display_name") or dono.get("id") or "",
                }
            )
    resultado = {"ok": True, "tracks": tracks, "playlists": playlists}
    with _busca_lock:
        if len(_busca_cache) >= BUSCA_MAX_ENTRADAS:
            _busca_cache.pop(min(_busca_cache, key=lambda k: _busca_cache[k][0]), None)
        _busca_cache[q] = (agora, resultado)
    return jsonify(resultado)


def _resposta_comando(status: int, reason: str | None) -> Response:
    """
    {"ok": true} ou {"ok": false, "error", "codigo"[, "espera_s"]} (HTTP 200 nos dois casos, como
    sempre: o app.js lê data.ok). "codigo": premium | sem_dispositivo | reconectar | limite | indisponivel.
    """
    if _sucesso(status):
        return jsonify({"ok": True})
    return jsonify({"ok": False, **_corpo_erro_spotify(status, reason)})


@app.post("/api/spotify/play")
def spotify_play() -> Response | tuple[Response, int]:
    _limitar_comandos_spotify()
    body = corpo_json()
    exigir_chaves(body, ("uri", "context_uri"))
    uri = body.get("uri") or None
    context_uri = body.get("context_uri") or None
    payload: dict[str, Any] = {}
    if uri is not None:
        if not isinstance(uri, str) or not RE_URI_TRACK.fullmatch(uri):
            raise ErroApi("Campo 'uri' inválido.")
        payload["uris"] = [uri]
    if context_uri is not None:
        if not isinstance(context_uri, str) or not RE_URI_CONTEXTO.fullmatch(context_uri):
            raise ErroApi("Campo 'context_uri' inválido.")
        payload["context_uri"] = context_uri
    if uri is not None and context_uri is not None:
        raise ErroApi("Envie 'uri' ou 'context_uri', não os dois.")
    device_id = spotify_active_device_id()
    if not device_id:
        return jsonify({"ok": False, "error": "Nenhum dispositivo Spotify selecionado.",
                        "codigo": "sem_dispositivo"}), 400
    status, reason = spotify_comando(
        "PUT", "/me/player/play", params={"device_id": device_id}, json_body=payload
    )
    return _resposta_comando(status, reason)


def _comando_simples(method: str, path: str, *, critico: bool = False) -> Response:
    """pause/next/previous: mesmo formato, só muda o método e o caminho."""
    _limitar_comandos_spotify()
    corpo_json()  # valida que veio um objeto JSON
    if critico and not _bypass_critico_da_rota():
        critico = False   # bypass já usado há pouco: este pause respeita o backoff 429
    status, reason = spotify_comando(
        method, path, params={"device_id": spotify_active_device_id()}, critico=critico
    )
    return _resposta_comando(status, reason)


@app.post("/api/spotify/pause")
def spotify_pause() -> Response:
    # Pausar é crítico (silenciar a loja): não fica preso atrás de um backoff 429 de leitura.
    return _comando_simples("PUT", "/me/player/pause", critico=True)


@app.post("/api/spotify/next")
def spotify_next() -> Response:
    return _comando_simples("POST", "/me/player/next")


@app.post("/api/spotify/previous")
def spotify_previous() -> Response:
    return _comando_simples("POST", "/me/player/previous")


@app.post("/api/spotify/volume")
def spotify_volume() -> Response:
    _limitar_comandos_spotify()
    corpo = corpo_json()
    exigir_chaves(corpo, ("value",))
    value = validar_int(corpo.get("value"), "value", 0, 100)
    status, reason = spotify_comando(
        "PUT",
        "/me/player/volume",
        params={"volume_percent": value, "device_id": spotify_active_device_id()},
    )
    return _resposta_comando(status, reason)


@app.get("/api/spotify/now-playing")
def spotify_now_playing() -> Response:
    data = spotify_player_state()
    vazio = {
        "playing": False, "track": None, "title": None, "artists": None,
        "image": None, "progress_ms": None, "duration_ms": None, "volume": None, "device": None,
    }
    if data is None:
        # Falha real (sem Premium, sem conexão, limite...): "ok": false com motivo, em vez de um
        # "nada tocando" que esconde o problema. Os campos de sempre continuam presentes (vazios).
        status, reason = _player_estado.get("erro") or (0, None)
        if _em_backoff():
            status = 429
        return jsonify({"ok": False, **vazio, **_corpo_erro_spotify(status, reason)})
    if not data:
        return jsonify({"ok": True, **vazio})
    item = data.get("item") or {}
    dispositivo = data.get("device") or {}
    titulo = item.get("name") if item else None
    # podcast: o dono do show vem como `show.name` (`show.publisher` saiu da API)
    artistas = (_nomes_artistas(item.get("artists")) or (item.get("show") or {}).get("name") or "") if item else ""
    imagens = (item.get("album") or {}).get("images") or item.get("images")
    return jsonify(
        {
            "ok": True,
            "playing": bool(data.get("is_playing")),
            "track": (f"{titulo} — {artistas}" if artistas else titulo) if titulo else None,
            "title": titulo,
            "artists": artistas or None,
            "image": escolher_imagem(imagens, largura_min=300),  # capa média (300 px)
            "progress_ms": data.get("progress_ms") if isinstance(data.get("progress_ms"), int) else None,
            "duration_ms": item.get("duration_ms") if isinstance(item.get("duration_ms"), int) else None,
            "volume": dispositivo.get("volume_percent"),
            "device": dispositivo.get("name"),
        }
    )


# --- Avisos de música do anúncio em andamento (v2.4.2) --------------------------------------
# spotify_duck/spotify_restore rodam na MESMA thread da requisição /api/announce; elas anotam aqui
# o que deu errado e o announce() devolve no JSON. Fora de um anúncio (lista None) não anotam nada.
MUSICA_AVISOS = {
    "musica_nao_abaixada": "A música do Spotify não foi abaixada durante este anúncio.",
    "musica_nao_restaurada": ("O volume da música do Spotify pode não ter voltado ao normal; o "
                              "Proclamare vai tentar de novo. Se continuar baixo, ajuste no painel."),
}
_aviso_musica_tl = threading.local()


def _avisos_musica_iniciar() -> None:
    _aviso_musica_tl.avisos = []


def _aviso_musica(codigo: str) -> None:
    lista = getattr(_aviso_musica_tl, "avisos", None)
    if lista is not None and codigo not in lista:
        lista.append(codigo)


def _avisos_musica_coletar() -> list[str]:
    lista = getattr(_aviso_musica_tl, "avisos", None) or []
    _aviso_musica_tl.avisos = None
    return lista


# --- Volume pendente (restore que não confirmou) --------------------------------------------
_pendente_lock = threading.Lock()
_reaplic_lock = threading.Lock()
_reaplic_thread: threading.Thread | None = None
_reaplic_acordar = threading.Event()   # acorda a thread já (testes/encerramento); senão ela espera o intervalo


def _volume_pendente() -> int | None:
    """Volume a restaurar guardado em `spotify_volume_pendente` (None se não há ou já expirou)."""
    p = load_config().get("spotify_volume_pendente")
    if not isinstance(p, dict):
        return None
    volume, ts = p.get("volume"), p.get("ts")
    if (isinstance(volume, int) and not isinstance(volume, bool) and 0 <= volume <= 100
            and isinstance(ts, (int, float)) and 0 <= time.time() - ts <= SPOTIFY_PENDENTE_VALIDADE_S):
        return volume
    return None


def _gravar_volume_pendente(volume: int) -> None:
    update_config(spotify_volume_pendente={"volume": volume, "ts": time.time()})
    _iniciar_reaplicacao()


def _limpar_volume_pendente() -> None:
    _remover_chaves_config("spotify_volume_pendente")


def _volume_confere(volume: int) -> bool:
    """Confirma pelo estado do player. Sem leitura utilizável, confia no 204 do PUT."""
    dados, status, _ = spotify_api("GET", "/me/player", critico=True)
    if status != 200 or not dados:
        return True
    atual = (dados.get("device") or {}).get("volume_percent")
    if not isinstance(atual, int):
        return True
    return abs(atual - volume) <= SPOTIFY_VOLUME_TOLERANCIA


# Geração do restore (REV-R5): cada duck de um anúncio novo incrementa; um restore de fundo de um
# anúncio ANTERIOR (que passou dos 8 s) confere a geração antes de cada PUT e desiste se mudou,
# para não subir o volume no meio da fala do anúncio seguinte.
_restore_gen = 0
_restore_gen_lock = threading.Lock()


def _nova_geracao_restore() -> int:
    global _restore_gen
    with _restore_gen_lock:
        _restore_gen += 1
        return _restore_gen


def _geracao_restore() -> int:
    with _restore_gen_lock:
        return _restore_gen


def _restaurar_volume(volume: int, gen: int | None = None) -> bool | None:
    """
    Até SPOTIFY_RESTORE_TENTATIVAS tentativas (pausa SPOTIFY_RESTORE_PAUSA_S), com o PUT CRÍTICO
    (ignora o backoff 429) e confirmação pelo estado do player. Erro que repetir não resolve
    (sem autorização, sem Premium) encerra na hora. Devolve True (restaurado), False (falhou) ou
    None (cancelado: outro anúncio abaixou o volume depois; `gen` mudou).
    - A confirmação espera SPOTIFY_RESTORE_CONFIRMA_ESPERA_S (o player demora a refletir o PUT);
      depois de SPOTIFY_RESTORE_ACEITA_NA_TENTATIVA PUTs aceitos (204) com a leitura ainda
      divergente, a leitura é tratada como defasada (log.info) e o restore termina como sucesso.
    - 429 (REV-R7): respeita o Retry-After; se a espera não cabe no orçamento de
      SPOTIFY_RESTORE_ESPERA_MAX_S, desiste (a reaplicação em 2º plano tenta mais tarde).
    """
    inicio = time.monotonic()
    for tentativa in range(1, SPOTIFY_RESTORE_TENTATIVAS + 1):
        if gen is not None and gen != _geracao_restore():
            return None
        device_id = spotify_active_device_id()   # relido: o 404 pode ter reencontrado o aparelho
        if not device_id:
            return True
        status, reason = spotify_comando(
            "PUT", "/me/player/volume",
            params={"volume_percent": volume, "device_id": device_id}, critico=True,
        )
        pausa = SPOTIFY_RESTORE_PAUSA_S
        if _sucesso(status):
            time.sleep(SPOTIFY_RESTORE_CONFIRMA_ESPERA_S)
            if _volume_confere(volume):
                return True
            if tentativa >= SPOTIFY_RESTORE_ACEITA_NA_TENTATIVA:
                log.info("Volume do Spotify: %d PUTs aceitos e a leitura do player ainda difere "
                         "(estado defasado); considerado restaurado.", tentativa)
                return True
            log.warning("Volume do Spotify: o PUT foi aceito, mas o player ainda mostra outro "
                        "volume (tentativa %d/%d).", tentativa, SPOTIFY_RESTORE_TENTATIVAS)
        else:
            log.warning("Não foi possível restaurar o volume do Spotify (HTTP %s, %s; tentativa %d/%d).",
                        status, reason or "-", tentativa, SPOTIFY_RESTORE_TENTATIVAS)
            if status == 401 or (status == 403 and reason in (None, "PREMIUM_REQUIRED")):
                return False
            if status == 429:
                espera = float(_espera_backoff_s())
                if time.monotonic() - inicio + espera > SPOTIFY_RESTORE_ESPERA_MAX_S:
                    return False
                pausa = max(pausa, espera)
        if tentativa < SPOTIFY_RESTORE_TENTATIVAS:
            time.sleep(pausa)
    return False


def _reaplicar_pendente(volume: int) -> bool:
    """
    Uma tentativa de reaplicar o volume pendente. True = resolvido (nada mais a fazer). Só mexe no
    volume se o aparelho AINDA está no volume do duck; se alguém já mexeu, descarta a pendência.
    Com backoff 429 ativo, pula o ciclo (não insiste contra o limite).
    """
    if _em_backoff() or not spotify_active_device_id():
        return False
    dados = spotify_player_state(force=True)
    if not dados:   # None (sem leitura) ou {} (app fechado): tenta de novo mais tarde
        return False
    atual = (dados.get("device") or {}).get("volume_percent")
    if not isinstance(atual, int):
        return False
    if atual > SPOTIFY_DUCK_VOLUME:
        return True
    return _restaurar_volume_uma_vez(volume)


def _restaurar_volume_uma_vez(volume: int) -> bool:
    device_id = spotify_active_device_id()
    if not device_id:
        return False
    status, _ = spotify_comando(
        "PUT", "/me/player/volume", params={"volume_percent": volume, "device_id": device_id}, critico=True
    )
    return _sucesso(status)


def _pendente_bruto() -> dict | None:
    """O dict `spotify_volume_pendente` como está no config (ou None)."""
    p = load_config().get("spotify_volume_pendente")
    return dict(p) if isinstance(p, dict) else None


def _loop_reaplicacao() -> None:
    """
    Repete o volume pendente a cada SPOTIFY_PENDENTE_INTERVALO_S. Trava a ordem de locks
    _pendente_lock -> _reaplic_lock (a mesma de quem grava a pendência) e decide SAIR sob os dois,
    zerando `_reaplic_thread`: assim uma pendência gravada logo depois sempre religa a thread.
    """
    global _reaplic_thread
    while True:
        _reaplic_acordar.wait(SPOTIFY_PENDENTE_INTERVALO_S)
        _reaplic_acordar.clear()
        if _desligando.is_set():   # v2.5: desligando pelo painel; a pendência fica no config.json
            return
        try:
            with _pendente_lock:
                with _reaplic_lock:
                    volume = _volume_pendente()
                    if volume is None:
                        _limpar_volume_pendente()
                        _reaplic_thread = None
                        return
                    visto = _pendente_bruto()
            if _anuncio_lock.locked():   # anúncio tocando: o duck/restore dele cuida do volume
                continue
            if _reaplicar_pendente(volume):   # rede fora dos locks (não atrasa o anúncio)
                with _pendente_lock:
                    # só apaga a MESMA pendência que tratamos (um restore novo pode ter gravado outra)
                    if _pendente_bruto() == visto:
                        _limpar_volume_pendente()
                log.info("Volume pendente do Spotify resolvido.")
                _reaplic_acordar.set()   # a próxima volta confirma que não sobrou nada e encerra a thread
        except Exception:  # noqa: BLE001  (a thread nunca morre por um erro de rede)
            log.exception("Falha ao reaplicar o volume pendente do Spotify.")


def _iniciar_reaplicacao() -> None:
    """Liga (uma só) a thread que repete o volume pendente a cada SPOTIFY_PENDENTE_INTERVALO_S."""
    global _reaplic_thread
    with _reaplic_lock:
        if _reaplic_thread is not None and _reaplic_thread.is_alive():
            return
        _reaplic_thread = threading.Thread(target=_loop_reaplicacao, name="spotify-pendente", daemon=True)
        _reaplic_thread.start()


def spotify_duck() -> int | None:
    """
    Abaixa o volume do Spotify antes do anúncio; devolve o volume anterior (ou None). Só devolve o
    anterior se o PUT deu certo: do contrário não há o que restaurar e o anúncio leva o aviso
    "musica_nao_abaixada". Volume ainda preso de um restore que falhou antes: devolve o volume
    pendente, para o restore deste anúncio consertar de uma vez.
    """
    device_id = spotify_active_device_id()
    if not device_id:
        return None
    data = spotify_player_state(force=True)  # leitura fresca: precisamos do volume real
    if not data or not data.get("is_playing"):
        return None  # nada tocando: não há o que abaixar
    anterior = (data.get("device") or {}).get("volume_percent")
    if not isinstance(anterior, int):
        return None
    pendente = _volume_pendente()
    if anterior <= SPOTIFY_DUCK_VOLUME:
        # já baixo: ou o aparelho estava assim, ou o último restore falhou e o volume ficou preso
        if pendente is not None and pendente > anterior:
            _nova_geracao_restore()   # o restore deste anúncio assume; cancela o de fundo anterior
            return pendente
        return None
    if pendente is not None:
        _limpar_volume_pendente()   # o volume voltou ao normal por outro caminho
    _nova_geracao_restore()         # cancela qualquer restore de fundo de um anúncio anterior
    status, reason = spotify_comando(
        "PUT", "/me/player/volume",
        params={"volume_percent": SPOTIFY_DUCK_VOLUME, "device_id": device_id},
    )
    if _sucesso(status):
        return anterior
    log.warning("Não foi possível abaixar o volume do Spotify (HTTP %s, %s).", status, reason or "-")
    _aviso_musica("musica_nao_abaixada")
    return None


def spotify_restore(volume_anterior: int | None) -> bool:
    """
    Devolve o volume do Spotify depois do anúncio. True = restaurado (ou nada a restaurar).
    A restauração roda numa thread própria e o anúncio espera no máximo SPOTIFY_RESTORE_ESPERA_MAX_S:
    se passar disso, o anúncio termina (aviso "musica_nao_restaurada"), o volume fica registrado em
    `spotify_volume_pendente` e a thread continua tentando em segundo plano. NUNCA lança exceção
    (o `finally` do anúncio ainda precisa restaurar a música do YouTube).
    """
    if volume_anterior is None or not spotify_active_device_id():
        return True
    gen = _geracao_restore()
    estado: dict[str, Any] = {"ok": None, "pendente": False}
    pronto = threading.Event()

    def trabalho() -> None:
        resultado: bool | None = False
        try:
            resultado = _restaurar_volume(volume_anterior, gen)
        except Exception:  # noqa: BLE001
            log.exception("Falha inesperada ao restaurar o volume do Spotify.")
        finally:
            try:
                with _pendente_lock:
                    estado["ok"] = resultado is not False
                    if resultado is True:
                        _limpar_volume_pendente()
                    elif resultado is False and not estado["pendente"]:
                        _gravar_volume_pendente(volume_anterior)
                    # None = cancelado por um anúncio novo: não mexe na pendência (o duck dele cuida)
            except Exception:  # noqa: BLE001
                log.exception("Não foi possível gravar o volume pendente do Spotify.")
            finally:
                pronto.set()

    threading.Thread(target=trabalho, name="spotify-restore", daemon=True).start()
    if not pronto.wait(SPOTIFY_RESTORE_ESPERA_MAX_S):
        log.warning("O volume do Spotify demorou a voltar; segue em segundo plano.")
        try:
            with _pendente_lock:
                if estado["ok"] is None:   # ainda tentando: registra a pendência (o sucesso a apaga)
                    estado["pendente"] = True
                    _gravar_volume_pendente(volume_anterior)
        except Exception:  # noqa: BLE001  (disco cheio/antivírus: o restore do YouTube ainda precisa rodar)
            log.exception("Não foi possível gravar o volume pendente do Spotify.")
    ok = bool(estado["ok"])
    if not ok:
        log.warning("O volume da música do Spotify pode não ter sido restaurado.")
        _aviso_musica("musica_nao_restaurada")
    return ok


# ---------------------------------------------------------------------------
# YouTube — relé de comandos (precisa de uma aba "tocadora" aberta na loja)
# deque limitado + Condition: a aba tocadora usa long-poll e recebe o comando
# no instante em que ele é enfileirado.
# v2.2.1: parser de link no servidor, fila do Proclamare e títulos via oEmbed.
# ---------------------------------------------------------------------------
YOUTUBE_PLAYER_TIMEOUT = 6.0  # segundos sem heartbeat = considera tocador offline
YOUTUBE_WAIT_MAX = 25
YOUTUBE_ACOES_CLIENTE = frozenset(
    {"load", "play", "pause", "next", "prev", "volume", "play_index", "seek"}
)
YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
)
YOUTUBE_FILA_MAX = 200          # itens na fila do Proclamare (v2.4: 50 -> 200, cabem pastas inteiras)
YOUTUBE_PLAYLIST_MAX = 200      # ids de playlist aceitos no heartbeat
YOUTUBE_OEMBED_PENDENTES_MAX = 32  # buscas de título em andamento/esperando (anti-inflação)
YOUTUBE_OEMBED_TAXA_MAX = 30         # consultas ao oEmbed por minuto (global)
YOUTUBE_PROXIMAS_MAX = 15       # "próximas da playlist" devolvidas no estado
YOUTUBE_OEMBED_TIMEOUT = 4.0    # segundos
YOUTUBE_OEMBED_MAX_BYTES = 64 * 1024
YOUTUBE_OEMBED_CACHE_MAX = 500
YOUTUBE_OEMBED_FALHA_TTL = 600.0  # falhas ficam em cache ~10 min (não martelar o YouTube)
_RE_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_RE_LIST_ID = re.compile(r"[A-Za-z0-9_-]{2,64}")
_RE_TEMPO = re.compile(r"(?:(\d{1,5})h)?(?:(\d{1,5})m)?(?:(\d{1,6})s?)?")

_yt_cond = threading.Condition()
_desligando = threading.Event()   # v2.5: "Desligar" aceito; long-polls e laços de fundo saem logo
YOUTUBE_POLLS_MAX = 4  # long-polls simultâneos (uso normal: 1 aba tocadora)
_yt_poll_sem = threading.BoundedSemaphore(YOUTUBE_POLLS_MAX)
_yt_comandos: deque[dict] = deque(maxlen=50)
_yt_ultimo_id = 0
_yt_estado_lock = threading.Lock()
_yt_estado: dict[str, Any] = {
    "now_playing": "", "is_playing": False, "volume": 70, "last_heartbeat": None,
    "video_id": None, "list_id": None, "playlist_ids": [], "playlist_index": None,
    "source": "youtube", "music_id": None,   # v2.4: o que a aba tocadora está tocando
    "yt_pronto": True,   # v2.4: o player do YouTube da aba tocadora carregou (False = sem internet/API)
    # v2.4.1: tempo da faixa. "posicao_em" = time.monotonic() do heartbeat que trouxe a posição.
    "posicao_s": None, "duracao_s": None, "posicao_em": None,
}

# Fila do Proclamare: SÓ em memória (não sobrevive a reinício do servidor).
_yt_fila_lock = threading.Lock()
_yt_fila: list[dict[str, Any]] = []
_yt_fila_prox_id = 0
_yt_fila_versao = 0

# Cache de títulos (oEmbed): chave = URL canônica -> (título|None, expira_em|None).
# Sucesso não expira (só sai por LRU); falha expira em ~10 min.
_yt_titulos_lock = threading.Lock()
_yt_titulos: OrderedDict[str, tuple[str | None, float | None]] = OrderedDict()
_yt_titulos_pendentes: set[str] = set()
_yt_titulos_pool: ThreadPoolExecutor | None = None


def youtube_enqueue(action: str, params: dict | None = None) -> int:
    """Enfileira um comando para a aba tocadora e acorda os long-polls."""
    global _yt_ultimo_id
    with _yt_cond:
        _yt_ultimo_id += 1
        if len(_yt_comandos) >= (_yt_comandos.maxlen or 0):
            # Buffer cheio: descarta o comando mais antigo que NÃO seja "duck"/"restore" (um anúncio
            # não pode deixar a música alta nem abaixada por causa de uma enxurrada de outros comandos).
            for velho in _yt_comandos:
                if velho["action"] not in ("duck", "restore"):
                    _yt_comandos.remove(velho)
                    break
        _yt_comandos.append({"id": _yt_ultimo_id, "action": action, "params": params or {}})
        _yt_cond.notify_all()
        return _yt_ultimo_id


# --- A) Parser de link ------------------------------------------------------
def _segundos_do_tempo(texto: str) -> int | None:
    """Converte '90', '90s', '1m30s', '1h2m3s' em segundos (0..86400) ou None."""
    texto = texto.strip().lower()
    if not texto:
        return None
    m = _RE_TEMPO.fullmatch(texto)
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    total = h * 3600 + mi * 60 + s
    return total if 0 <= total <= 86400 else None


def parse_youtube_url(valor: Any) -> dict[str, Any]:
    """
    Interpreta um link do YouTube e devolve
    {"video_id", "list_id", "index" (0-based), "start" (segundos)} (None se ausente).
    Mix/rádio automático (list=RD... ou start_radio) é IGNORADO e toca só o vídeo;
    link que é só de Mix é recusado. Levanta ErroApi(400) para link inválido.
    """
    url = validar_str(valor, "url", 300, min_len=1)
    if any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise ErroApi("Campo 'url' inválido.")
    try:
        partes = urlsplit(url)
        porta = partes.port
    except ValueError:
        raise ErroApi("Campo 'url' inválido.") from None
    host = partes.hostname or ""
    if (
        partes.scheme != "https"
        or host not in YOUTUBE_HOSTS
        or partes.username is not None
        or partes.password is not None
        or porta not in (None, 443)
    ):
        raise ErroApi("Somente links https do YouTube são aceitos.")

    consulta = parse_qs(partes.query, keep_blank_values=True)

    def _q(nome: str) -> str | None:
        """Primeiro valor do parâmetro; vazio (v=, list=) conta como ausente."""
        v = consulta.get(nome)
        return v[0] if v and v[0] != "" else None

    # --- id do vídeo: ?v= (watch) ou segmento do caminho (youtu.be, shorts...) ---
    segmentos = [s for s in partes.path.split("/") if s]
    bruto_video: str | None = None
    if host == "youtu.be":
        bruto_video = segmentos[0] if segmentos else None
    elif segmentos and segmentos[0] in ("shorts", "live", "embed"):
        bruto_video = segmentos[1] if len(segmentos) > 1 else None
    elif segmentos and segmentos[0] == "watch":
        bruto_video = _q("v")
    video_id: str | None = None
    if bruto_video is not None:
        if not _RE_VIDEO_ID.fullmatch(bruto_video):
            raise ErroApi("Link do YouTube inválido: não encontrei o vídeo.")
        video_id = bruto_video

    # --- lista: Mix automático é ignorado ---
    list_id: str | None = None
    bruto_lista = _q("list")
    mix = "start_radio" in consulta or (bruto_lista or "").upper().startswith("RD")
    if bruto_lista is not None and not mix:
        if _RE_LIST_ID.fullmatch(bruto_lista):
            list_id = bruto_lista
        elif video_id is None:
            raise ErroApi("Link do YouTube inválido: lista não reconhecida.")
    if video_id is None and list_id is None:
        if mix:
            raise ErroApi(
                "Esse link é um Mix automático do YouTube; abra uma música dele "
                "e copie o link dela."
            )
        raise ErroApi("Link do YouTube inválido: não encontrei vídeo nem playlist.")

    # --- índice (1-based no link -> 0-based) e início ---
    indice: int | None = None
    bruto_idx = _q("index")
    if list_id is not None and bruto_idx and re.fullmatch(r"\d{1,4}", bruto_idx):
        n = int(bruto_idx) - 1
        if 0 <= n < YOUTUBE_PLAYLIST_MAX:
            indice = n
    inicio: int | None = None
    frag = parse_qs(partes.fragment) if "=" in partes.fragment else {}
    bruto_t = _q("t") or _q("start") or (frag.get("t") or [None])[0]
    if bruto_t:
        inicio = _segundos_do_tempo(bruto_t)
    return {"video_id": video_id, "list_id": list_id, "index": indice, "start": inicio}


# --- B) Títulos via oEmbed (fora da thread da requisição) -------------------
def _yt_url_canonica(video_id: str | None, list_id: str | None) -> str | None:
    """URL montada SÓ a partir de ids já validados (nunca da URL do usuário)."""
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    if list_id:
        return f"https://www.youtube.com/playlist?list={list_id}"
    return None


def _limpar_titulo(valor: Any) -> str | None:
    """Título seguro: str, sem caracteres de controle, até 200 caracteres."""
    if not isinstance(valor, str):
        return None
    limpo = programacao.limpar_exibicao(valor).strip()[:200]
    return limpo or None


def _oembed_buscar(url: str) -> str | None:
    """Consulta o oEmbed público (sem chave). Qualquer problema -> None."""
    try:
        with requests.get(
            "https://www.youtube.com/oembed",
            params={"format": "json", "url": url},
            timeout=YOUTUBE_OEMBED_TIMEOUT,
            allow_redirects=False,  # não segue para outro host
            stream=True,
            headers={"User-Agent": "Proclamare/2.2"},
        ) as r:
            if r.status_code != 200:
                return None
            dados = bytearray()
            for pedaco in r.iter_content(chunk_size=8192):
                dados += pedaco
                if len(dados) > YOUTUBE_OEMBED_MAX_BYTES:
                    return None  # resposta grande demais
        obj = json.loads(bytes(dados).decode("utf-8"))
        return _limpar_titulo(obj.get("title")) if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001 — rede/JSON/encoding: o título é opcional
        log.debug("oEmbed falhou.")
        return None


def _titulo_em_cache(url: str | None) -> tuple[bool, str | None]:
    """(achou, título). Falhas vencidas saem do cache."""
    if url is None:
        return True, None
    with _yt_titulos_lock:
        entrada = _yt_titulos.get(url)
        if entrada is None:
            return False, None
        titulo, expira = entrada
        if expira is not None and expira <= time.monotonic():
            del _yt_titulos[url]
            return False, None
        _yt_titulos.move_to_end(url)
        return True, titulo


def _resolver_titulo(url: str) -> None:
    """Tarefa de segundo plano: busca e guarda o título no cache."""
    global _yt_fila_versao
    titulo = _oembed_buscar(url)
    with _yt_titulos_lock:
        expira = None if titulo else time.monotonic() + YOUTUBE_OEMBED_FALHA_TTL
        _yt_titulos[url] = (titulo, expira)
        _yt_titulos.move_to_end(url)
        while len(_yt_titulos) > YOUTUBE_OEMBED_CACHE_MAX:
            _yt_titulos.popitem(last=False)
        _yt_titulos_pendentes.discard(url)
    if titulo:
        with _yt_fila_lock:  # título novo muda o que o front mostra
            _yt_fila_versao += 1
        _titulo_resolvido_para_listas(url)


def _pedir_titulo(url: str | None) -> None:
    """Agenda a busca do título se ainda não está em cache nem em andamento."""
    global _yt_titulos_pool
    if url is None or _titulo_em_cache(url)[0]:
        return
    with _yt_titulos_lock:
        if url in _yt_titulos_pendentes:
            return
        if len(_yt_titulos_pendentes) >= YOUTUBE_OEMBED_PENDENTES_MAX:
            return  # fila de buscas cheia: descarta sem gravar falha; o próximo GET tenta de novo
        if excedeu_limite("oembed", YOUTUBE_OEMBED_TAXA_MAX, 60.0):
            return  # teto global de consultas ao YouTube por minuto
        _yt_titulos_pendentes.add(url)
        if _yt_titulos_pool is None:
            _yt_titulos_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="yt-titulo")
        pool = _yt_titulos_pool
    try:
        pool.submit(_resolver_titulo, url)
    except RuntimeError:  # pool encerrado (desligando o servidor)
        with _yt_titulos_lock:
            _yt_titulos_pendentes.discard(url)


# --- B) Fila do Proclamare --------------------------------------------------
def _item_url(item: dict[str, Any]) -> str | None:
    if item.get("kind") == "arquivo":
        return None  # arquivo local: não há título para buscar no YouTube
    return _yt_url_canonica(item["video_id"], item["list_id"])


def _item_publico(item: dict[str, Any]) -> dict[str, Any]:
    """Cópia do item com o título atual (oEmbed no YouTube; metadado no arquivo)."""
    if item.get("kind") == "arquivo":
        m = ACERVO.obter(item["music_id"])
        if m is None:  # sumiu da pasta: mantém o que foi guardado ao enfileirar
            return dict(item)
        return {**item, "title": m.titulo, "artista": m.artista, "duracao_s": m.duracao_s,
                "tem_capa": m.tem_capa}
    return {**item, "title": _titulo_em_cache(_item_url(item))[1]}


def _params_load(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("kind") == "arquivo":
        return {"music_id": item["music_id"]}  # nunca caminho: o tocador busca /api/musicas/<id>/audio
    return {k: item.get(k) for k in ("video_id", "list_id", "index", "start")}


def _item_arquivo(item_id: int, m: "musicas.Musica") -> dict[str, Any]:
    """Item de fila para um arquivo do acervo (campos do YouTube ficam null)."""
    return {
        "id": item_id, "kind": "arquivo", "music_id": m.id,
        "video_id": None, "list_id": None, "index": None, "start": None,
        "title": m.titulo, "artista": m.artista, "duracao_s": m.duracao_s,
        "tem_capa": m.tem_capa, "added_at": int(time.time()),
    }


def _item_youtube(item_id: int, alvo: dict[str, Any]) -> dict[str, Any]:
    """Item de fila para um vídeo/playlist do YouTube (os campos do item de `op: add` com url)."""
    return {
        "id": item_id, "kind": "playlist" if alvo["list_id"] else "video",
        "video_id": alvo["video_id"], "list_id": alvo["list_id"],
        "index": alvo["index"], "start": alvo["start"],
        "title": None, "added_at": int(time.time()),
    }


def _semear_titulo(url: str | None, titulo: str) -> None:
    """
    Põe no cache de títulos um título que já conhecemos (o guardado numa lista), se o cache ainda não tiver um
    TÍTULO para a URL (nem nada, nem só uma falha de oEmbed).
    """
    if url is None:
        return
    achou, atual = _titulo_em_cache(url)
    if achou and atual:
        return
    with _yt_titulos_lock:
        _yt_titulos[url] = (titulo, None)
        _yt_titulos.move_to_end(url)
        while len(_yt_titulos) > YOUTUBE_OEMBED_CACHE_MAX:
            _yt_titulos.popitem(last=False)


def _musica_ou_erro(valor: Any, nome: str = "music_id") -> "musicas.Musica":
    """Valida o formato do id (400) e exige que a música exista no acervo (404)."""
    if not isinstance(valor, str) or not musicas.RE_ID.fullmatch(valor):
        raise ErroApi(f"Campo '{nome}' inválido.")
    m = ACERVO.obter(valor)
    if m is None:
        raise ErroApi("Essa música não está mais na pasta.", 404)
    return m


def fila_purgar_musica(music_id: str) -> None:
    """Tira da fila do Proclamare as entradas de uma música removida da pasta."""
    global _yt_fila_versao
    with _yt_fila_lock:
        antes = len(_yt_fila)
        _yt_fila[:] = [i for i in _yt_fila if i.get("music_id") != music_id]
        if len(_yt_fila) != antes:
            _yt_fila_versao += 1


def _achar_item(item_id: int) -> int:
    """Posição do item na fila (chamar COM _yt_fila_lock). 404 se não existir."""
    for pos, it in enumerate(_yt_fila):
        if it["id"] == item_id:
            return pos
    raise ErroApi("Item não encontrado na fila.", 404)


def _limitar_adicoes() -> None:
    """Teto de adições à fila por IP (compartilhado por URL, music_id e add_many)."""
    if excedeu_limite("ytq:" + (request.remote_addr or "?"), 20, 10.0):
        raise ErroApi("Muitas adições seguidas. Aguarde alguns segundos.", 429)


def _adicionar_arquivos(
    achadas: "list[musicas.Musica | dict[str, Any]]", *, posicao: str = "fim", substituir: bool = False,
    tirar_primeiro: bool = False,
) -> tuple[list[dict[str, Any]], int, int, dict[str, Any] | None]:
    """
    Põe músicas do acervo na fila, TUDO sob um único _yt_fila_lock (por isso "Tocar pasta" não
    perde a fila antiga se algo falhar, e o 'ended' do tocador não corre entre o add e o pop).
    `substituir` esvazia a fila antes; `tirar_primeiro` devolve o 1º item novo já fora da fila
    (para o front mandar tocar). Devolve (novos, cortados, queue_version, primeiro|None).
    v2.7: um elemento de `achadas` pode ser um dict {video_id,list_id,index,start,...} = item do YouTube.
    """
    global _yt_fila_prox_id, _yt_fila_versao
    with _yt_fila_lock:
        if substituir:
            _yt_fila.clear()
        espaco = YOUTUBE_FILA_MAX - len(_yt_fila)
        if espaco <= 0:
            raise ErroApi(f"A fila está cheia (máximo {YOUTUBE_FILA_MAX}).", 409)
        entram = achadas[:espaco]
        novos = []
        for m in entram:
            _yt_fila_prox_id += 1
            novos.append(_item_youtube(_yt_fila_prox_id, m) if isinstance(m, dict)
                         else _item_arquivo(_yt_fila_prox_id, m))
        if posicao == "inicio":
            _yt_fila[0:0] = novos
        else:
            _yt_fila.extend(novos)
        primeiro = _yt_fila.pop(0) if tirar_primeiro and novos else None
        _yt_fila_versao += 1
        return novos, len(achadas) - len(entram), _yt_fila_versao, primeiro


_FILA_CHAVES_POR_OP = {
    "add": frozenset({"op", "url", "music_id"}),
    "add_many": frozenset({"op", "music_ids", "posicao", "embaralhar", "substituir", "tirar_primeiro"}),
    "remove": frozenset({"op", "item_id"}),
    "clear": frozenset({"op"}),
    "move": frozenset({"op", "item_id", "to"}),
    "play_now": frozenset({"op", "item_id"}),
    "pop": frozenset({"op"}),
}


@app.post("/api/youtube/queue")
def youtube_queue() -> Response:
    """Operações da fila: add (url|music_id), add_many, remove, clear, move, play_now, pop."""
    global _yt_fila_prox_id, _yt_fila_versao
    body = corpo_json()
    op = body.get("op")
    if not isinstance(op, str) or op not in _FILA_CHAVES_POR_OP:
        raise ErroApi("Operação inválida.")
    exigir_chaves(body, _FILA_CHAVES_POR_OP[op])

    if op == "add_many":
        # "Tocar pasta" / "Adicionar pasta à fila" (v2.4): vários arquivos de uma vez.
        _limitar_adicoes()
        ids = body.get("music_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= YOUTUBE_FILA_MAX:
            raise ErroApi(f"Campo 'music_ids' deve ter de 1 a {YOUTUBE_FILA_MAX} itens.")
        for i in ids:
            if not isinstance(i, str) or not musicas.RE_ID.fullmatch(i):
                raise ErroApi("Campo 'music_ids' inválido.")
        posicao = body.get("posicao", "fim")
        if posicao not in ("fim", "inicio"):
            raise ErroApi("Campo 'posicao' inválido.")
        flags = {}
        for nome in ("embaralhar", "substituir", "tirar_primeiro"):
            flags[nome] = body.get(nome, False)
            if not isinstance(flags[nome], bool):
                raise ErroApi(f"Campo '{nome}' inválido.")
        if flags["tirar_primeiro"] and not flags["substituir"]:
            raise ErroApi("'tirar_primeiro' só vale junto com 'substituir'.")
        achadas = [m for m in (ACERVO.obter(i) for i in ids) if m is not None]
        if not achadas:  # nada mudou na fila: quem pediu 'substituir' não perde a fila antiga
            raise ErroApi("Essas músicas não estão mais na pasta.", 404)
        if flags["embaralhar"]:
            random.shuffle(achadas)
        novos, cortados, versao, primeiro = _adicionar_arquivos(
            achadas, posicao=posicao, substituir=flags["substituir"],
            tirar_primeiro=flags["tirar_primeiro"])
        resp = {
            "ok": True, "adicionados": len(novos), "ignorados": len(ids) - len(achadas),
            "cortados": cortados, "queue_version": versao,
        }
        if primeiro is not None:
            resp["primeiro"] = _item_publico(primeiro)
        return jsonify(resp)

    if op == "add" and "music_id" in body:
        # Arquivo do acervo (v2.4): "music_id" e "url" são alternativas, nunca os dois.
        if "url" in body:
            raise ErroApi("Informe 'url' ou 'music_id', não os dois.")
        _limitar_adicoes()
        m = _musica_ou_erro(body.get("music_id"))
        novos, _cortados, versao, _primeiro = _adicionar_arquivos([m])
        return jsonify({"ok": True, "item": _item_publico(novos[0]), "queue_version": versao})

    if op == "add":
        _limitar_adicoes()
        alvo = parse_youtube_url(body.get("url"))  # valida ANTES de pegar o lock
        with _yt_fila_lock:
            if len(_yt_fila) >= YOUTUBE_FILA_MAX:
                raise ErroApi(f"A fila está cheia (máximo {YOUTUBE_FILA_MAX}).", 409)
            _yt_fila_prox_id += 1
            item = {
                "id": _yt_fila_prox_id,
                "kind": "playlist" if alvo["list_id"] else "video",
                "video_id": alvo["video_id"],
                "list_id": alvo["list_id"],
                "index": alvo["index"],  # extra do contrato: necessário para tocar o item
                "start": alvo["start"],
                "title": None,
                "added_at": int(time.time()),
            }
            _yt_fila.append(item)
            _yt_fila_versao += 1
            versao = _yt_fila_versao
        _pedir_titulo(_item_url(item))
        return jsonify({"ok": True, "item": _item_publico(item), "queue_version": versao})

    if op == "clear":
        with _yt_fila_lock:
            _yt_fila.clear()
            _yt_fila_versao += 1
            versao = _yt_fila_versao
        return jsonify({"ok": True, "queue_version": versao})

    if op == "pop":
        with _yt_fila_lock:
            item = _yt_fila.pop(0) if _yt_fila else None
            # arquivo que sumiu da pasta não vale a pena entregar: pula para o próximo
            while item is not None and item.get("kind") == "arquivo" \
                    and ACERVO.obter(item["music_id"]) is None:
                _yt_fila_versao += 1
                item = _yt_fila.pop(0) if _yt_fila else None
            if item is not None:
                _yt_fila_versao += 1
            versao = _yt_fila_versao
        return jsonify({
            "ok": True, "item": _item_publico(item) if item else None,
            "queue_version": versao,
        })

    item_id = validar_int(body.get("item_id"), "item_id", 0, 2**62)
    if op == "move":
        with _yt_fila_lock:
            pos = _achar_item(item_id)
            destino = validar_int(body.get("to"), "to", 0, len(_yt_fila) - 1, limitar=True)
            _yt_fila.insert(destino, _yt_fila.pop(pos))
            _yt_fila_versao += 1
            versao = _yt_fila_versao
        return jsonify({"ok": True, "queue_version": versao})

    # remove / play_now: tiram o item da fila
    if op == "play_now":
        # Só consome o item se há tocador vivo (senão o comando ficaria retido e tocaria
        # muito depois). Ordem de locks: estado -> fila (único lugar que aninha os dois).
        with _yt_estado_lock:
            beat = _yt_estado["last_heartbeat"]
            vivo = beat is not None and (time.monotonic() - beat) < YOUTUBE_PLAYER_TIMEOUT
            if not vivo:
                raise ErroApi("Nenhum tocador aberto. Abra o modo Tocador da loja primeiro.", 409)
            with _yt_fila_lock:
                pos = _achar_item(item_id)
                if _yt_fila[pos].get("kind") != "arquivo" and not _yt_estado["yt_pronto"]:
                    # tocador "vivo" só para arquivos (sem internet/API do YouTube): consumir o item
                    # faria o comando 'load' ficar parado para sempre
                    raise ErroApi("O player do YouTube desta aba ainda não carregou. Confira a "
                                  "internet ou toque uma música do computador.", 409)
                item = _yt_fila.pop(pos)
                _yt_fila_versao += 1
                versao = _yt_fila_versao
    else:
        with _yt_fila_lock:
            item = _yt_fila.pop(_achar_item(item_id))
            _yt_fila_versao += 1
            versao = _yt_fila_versao
    if op == "play_now":
        if item.get("kind") == "arquivo" and ACERVO.obter(item["music_id"]) is None:
            # o item já saiu da fila (não adianta mantê-lo): avisa que a música sumiu
            return jsonify({"ok": False, "error": "Essa música não está mais na pasta.",
                            "queue_version": versao}), 404
        cmd_id = youtube_enqueue("load", _params_load(item))
        return jsonify({"ok": True, "item": _item_publico(item), "id": cmd_id,
                        "queue_version": versao})
    return jsonify({"ok": True, "queue_version": versao})


@app.post("/api/youtube/command")
def youtube_command() -> Response:
    body = corpo_json()
    exigir_chaves(body, ("action", "params"))
    action = body.get("action")
    # "duck"/"restore" são internos do servidor: o cliente nunca pode enviá-los.
    if not isinstance(action, str) or action not in YOUTUBE_ACOES_CLIENTE:
        raise ErroApi("Ação inválida.")
    # v2.4.1: limite de taxa por aparelho (o buffer de comandos é pequeno e o seek força rede na aba
    # tocadora). Uso normal (volume com throttle de 600 ms, toques na barra) fica bem abaixo.
    ip = request.remote_addr or "?"
    if excedeu_limite("ytcmd:" + ip, 30, 10.0):
        raise ErroApi("Muitos comandos seguidos. Aguarde um instante.", 429)
    bruto = body.get("params", {})
    if bruto is None:
        bruto = {}
    if not isinstance(bruto, dict):
        raise ErroApi("Campo 'params' inválido.")
    params: dict[str, Any] = {}  # lista branca: só repassa o que foi validado
    if action == "load" and "music_id" in bruto:
        # Arquivo do acervo (v2.4): só o id validado e existente; o caminho nunca sai do servidor.
        if "url" in bruto:
            raise ErroApi("Informe 'url' ou 'music_id', não os dois.")
        params = {"music_id": _musica_ou_erro(bruto.get("music_id")).id}
    elif action == "load":
        # O servidor interpreta o link e repassa só os campos validados (nunca a URL crua).
        params = parse_youtube_url(bruto.get("url"))
        _pedir_titulo(_yt_url_canonica(params["video_id"], params["list_id"]))
    elif action == "volume":
        params["value"] = validar_int(bruto.get("value"), "value", 0, 100)
    elif action == "play_index":
        params["index"] = validar_int(bruto.get("index"), "index", 0, YOUTUBE_PLAYLIST_MAX - 1)
    elif action == "seek":
        # v2.4.1: pular para um ponto da faixa. Só o número validado segue para a aba tocadora.
        params["segundos"] = validar_segundos(bruto.get("segundos"), "segundos")
        if excedeu_limite("ytseek:" + ip, 10, 4.0):  # só saltos válidos contam
            raise ErroApi("Muitos saltos seguidos. Aguarde um instante.", 429)
    return jsonify({"ok": True, "id": youtube_enqueue(action, params)})


@app.get("/api/youtube/commands")
def youtube_commands_poll() -> Response:
    """Long-poll: responde já se houver comandos novos; senão espera até `wait` s."""
    since = validar_int(request.args.get("since", "0"), "since", 0, 2**62)
    espera = validar_int(request.args.get("wait", "0"), "wait", 0, YOUTUBE_WAIT_MAX)
    # Limita os long-polls simultâneos (cada um prende uma thread do waitress).
    # Sem vaga: responde já (sem esperar), com uma pausa curta para a aba
    # tocadora legítima não girar em laço apertado.
    segurando = espera > 0 and _yt_poll_sem.acquire(blocking=False)
    if espera > 0 and not segurando:
        espera = 0
        time.sleep(0.5)
    try:
        limite = time.monotonic() + espera
        with _yt_cond:
            if since > _yt_ultimo_id:  # servidor reiniciou (contador voltou a zero)
                since = 0
            while True:
                pendentes = [c for c in _yt_comandos if c["id"] > since]
                restante = limite - time.monotonic()
                if pendentes or restante <= 0 or _desligando.is_set():
                    break
                _yt_cond.wait(timeout=restante)
            ultimo = _yt_ultimo_id
    finally:
        if segurando:
            _yt_poll_sem.release()
    return jsonify({"ok": True, "commands": pendentes, "last_id": ultimo})


def _id_obrigatorio(valor: Any, nome: str, regex: re.Pattern[str]) -> str:
    """Como _id_opcional, mas None também é inválido."""
    if not isinstance(valor, str) or not regex.fullmatch(valor):
        raise ErroApi(f"Campo '{nome}' inválido.")
    return valor


def _id_opcional(valor: Any, nome: str, regex: re.Pattern[str]) -> str | None:
    """Id do YouTube validado por regex, ou None. Qualquer outra coisa -> 400."""
    if valor is None:
        return None
    if not isinstance(valor, str) or not regex.fullmatch(valor):
        raise ErroApi(f"Campo '{nome}' inválido.")
    return valor


_YT_STATE_CHAVES = frozenset({
    "now_playing", "is_playing", "volume", "video_id", "list_id", "playlist_ids", "playlist_index",
    "source", "music_id", "yt_pronto", "posicao_s", "duracao_s",
})


@app.post("/api/youtube/state")
def youtube_state_push() -> Response:
    # Heartbeat legítimo: ~1 a cada 3 s (+ mudanças de estado). Teto folgado anti-abuso.
    if excedeu_limite("ytstate:" + (request.remote_addr or "?"), 60, 10.0):
        raise ErroApi("Muitas atualizações seguidas.", 429)
    body = corpo_json()
    exigir_chaves(body, _YT_STATE_CHAVES)
    novo: dict[str, Any] = {}
    if "now_playing" in body:
        # v2.7.1 (P15): o título vai a TODOS os painéis; controle, formato (bidi), substituto e uso
        # privado saem (em vez de recusar: títulos legítimos do YouTube não podem dar 400).
        bruto = validar_str(body["now_playing"], "now_playing", 300, strip=False)
        novo["now_playing"] = programacao.limpar_exibicao(bruto)
    if "is_playing" in body:
        if not isinstance(body["is_playing"], bool):
            raise ErroApi("Campo 'is_playing' inválido.")
        novo["is_playing"] = body["is_playing"]
    if "volume" in body:
        novo["volume"] = validar_int(body["volume"], "volume", 0, 100)
    if "video_id" in body:
        novo["video_id"] = _id_opcional(body["video_id"], "video_id", _RE_VIDEO_ID)
    if "list_id" in body:
        novo["list_id"] = _id_opcional(body["list_id"], "list_id", _RE_LIST_ID)
    if "playlist_ids" in body:
        ids = body["playlist_ids"]
        if ids is None:
            ids = []
        if not isinstance(ids, list) or len(ids) > YOUTUBE_PLAYLIST_MAX:
            raise ErroApi("Campo 'playlist_ids' inválido.")
        novo["playlist_ids"] = [
            _id_obrigatorio(i, "playlist_ids", _RE_VIDEO_ID) for i in ids
        ]
    if "playlist_index" in body:
        pi = body["playlist_index"]
        novo["playlist_index"] = (
            None if pi is None
            else validar_int(pi, "playlist_index", 0, YOUTUBE_PLAYLIST_MAX - 1)
        )
    if "source" in body:  # v2.4: o que está tocando ("youtube" ou "arquivo")
        if body["source"] not in ("youtube", "arquivo"):
            raise ErroApi("Campo 'source' inválido.")
        novo["source"] = body["source"]
    if "music_id" in body:
        novo["music_id"] = _id_opcional(body["music_id"], "music_id", musicas.RE_ID)
    if novo.get("source") == "youtube" and "music_id" not in body:
        novo["music_id"] = None  # trocou para o YouTube: não deixa um music_id velho para trás
    if "yt_pronto" in body:  # v2.4: sem ele (aba antiga) vale "pronto", como antes
        if not isinstance(body["yt_pronto"], bool):
            raise ErroApi("Campo 'yt_pronto' inválido.")
        novo["yt_pronto"] = body["yt_pronto"]
    # v2.4.1: tempo da faixa. Tudo é validado ANTES de tocar no estado (400 não altera nada).
    if "posicao_s" in body:
        novo["posicao_s"] = validar_segundos(body["posicao_s"], "posicao_s")
    if "duracao_s" in body:   # null = desconhecida / ao vivo
        novo["duracao_s"] = (
            None if body["duracao_s"] is None
            else validar_segundos(body["duracao_s"], "duracao_s", positivo=True)
        )
    with _yt_estado_lock:
        agora = time.monotonic()
        faixa = ("source", "video_id", "music_id")
        if any(k in novo and novo[k] != _yt_estado[k] for k in faixa):
            # trocou de faixa: o tempo da anterior não vale para esta (se o heartbeat trouxer
            # posicao_s/duracao_s eles são aplicados logo abaixo)
            _yt_estado.update(posicao_s=None, duracao_s=None, posicao_em=None)
        _yt_estado.update(novo)
        if "posicao_s" in novo:
            _yt_estado["posicao_em"] = agora
        _yt_estado["last_heartbeat"] = agora
    return jsonify({"ok": True})


@app.get("/api/youtube/state")
def youtube_state_get() -> Response:
    with _yt_estado_lock:
        agora = time.monotonic()
        beat = _yt_estado["last_heartbeat"]
        vivo = beat is not None and (agora - beat) < YOUTUBE_PLAYER_TIMEOUT
        snap = {k: _yt_estado[k] for k in (
            "now_playing", "is_playing", "volume", "video_id", "list_id",
            "playlist_ids", "playlist_index", "source", "music_id", "yt_pronto",
        )}
        # v2.4.1: tempo da faixa, SÓ se o tocador está vivo e a posição é fresca (mesma régua do
        # "vivo"). A idade usa o mesmo `agora` de cima e o relógio monotônico do servidor.
        posicao_s = duracao_s = idade_ms = None
        em = _yt_estado["posicao_em"]
        if vivo and _yt_estado["posicao_s"] is not None and em is not None \
                and agora - em <= YOUTUBE_PLAYER_TIMEOUT:
            posicao_s, duracao_s = _yt_estado["posicao_s"], _yt_estado["duracao_s"]
            idade_ms = max(0, int((agora - em) * 1000))
    # Fila (com títulos já resolvidos) e versão lidas juntas, de forma consistente.
    with _yt_fila_lock:
        itens = list(_yt_fila)
        versao = _yt_fila_versao
    fila = [_item_publico(i) for i in itens]
    for i in itens:  # itens sem título em cache: resolve em segundo plano
        _pedir_titulo(_item_url(i))

    # Próximas da playlist em reprodução (até 15 a partir do índice atual).
    playlist = None
    if not vivo:  # tocador offline: não mostra (nem busca título de) dados velhos
        snap["video_id"] = None
        snap["list_id"] = None
        snap["playlist_ids"] = []
        snap["source"] = "youtube"
        snap["music_id"] = None
    tocando = ACERVO.obter(snap["music_id"]) if snap["music_id"] else None
    ids, idx = snap["playlist_ids"], snap["playlist_index"]
    if vivo and ids and idx is not None and idx < len(ids):
        proximos = ids[idx + 1: idx + 1 + YOUTUBE_PROXIMAS_MAX]
        playlist = {
            "index": idx,
            "items": [
                {"video_id": v,
                 "title": _titulo_em_cache(_yt_url_canonica(v, None))[1]}
                for v in proximos
            ],
        }
        for v in proximos:
            _pedir_titulo(_yt_url_canonica(v, None))
    return jsonify(
        {
            "now_playing": snap["now_playing"],
            "is_playing": snap["is_playing"],
            "volume": snap["volume"],
            "player_alive": vivo,
            "video_id": snap["video_id"],
            "list_id": snap["list_id"],
            "source": snap["source"],
            "music_id": snap["music_id"],
            "yt_pronto": bool(vivo and snap["yt_pronto"]),
            "musica": tocando.publico() if tocando else None,
            "posicao_s": posicao_s,
            "duracao_s": duracao_s,
            "posicao_idade_ms": idade_ms,
            "queue": fila,
            "queue_version": versao,
            "playlist": playlist,
        }
    )


# ---------------------------------------------------------------------------
# Rotas principais
# ---------------------------------------------------------------------------
@app.get("/")
def home() -> Response:
    return send_from_directory(STATIC_DIR, "index.html", max_age=0)


@app.get("/api/status")
def status() -> Response:
    return jsonify({"internet": tem_internet()})


@app.get("/api/voices")
def voices() -> Response:
    vozes = obter_vozes_offline()  # cacheado após a 1ª chamada
    if vozes is None:
        return jsonify({"ok": False, "error": "Motor de voz offline indisponível.",
                        "voices": [], "default": None})
    return jsonify({"ok": True, "voices": vozes["voices"], "default": vozes["default"]})


_anuncio_lock = threading.Lock()  # um anúncio por vez (não bloqueante -> 409)
ANUNCIO_TEXTO_MAX = 500           # teto do texto digitado (v2.5: as Mensagens prontas usam o MESMO)


@contextmanager
def musica_abaixada(spotify: bool = True, youtube: bool = True) -> Iterator[None]:
    """
    Abaixa Spotify + YouTube durante o bloco e SEMPRE restaura ao sair. `spotify` e
    `youtube` (som.abaixar) desligam cada camada; os padrões mantêm o comportamento da v2.2.1.
    """
    volume_anterior = spotify_duck() if spotify else None
    if youtube:
        youtube_enqueue("duck")
    try:
        yield
    finally:
        spotify_restore(volume_anterior)
        if youtube:
            youtube_enqueue("restore")


def _anunciar(texto: str, modo: str, voz_online: str, velocidade: int,
              voz_offline_id: Any, usar_online: bool, *,
              contexto: Callable[[], Any] | None = None, parar: Any = None) -> tuple[str, str | None]:
    """
    Executa o anúncio e devolve (engine_used, warning).
    Online: gera o mp3 PRIMEIRO (música ainda alta), só então abaixa, toca e restaura.
    Offline: abaixa antes da fala. No modo auto, falha online cai para offline.
    v2.6 (programação): `contexto` troca o "abaixar a música" por outro gerenciador de contexto (o
    anúncio agendado PAUSA e retoma a música) e `parar` (SinalParada) corta a reprodução; a voz do
    Windows falando direto (SAPI) não tem como ser cortada e avisa o sinal disso.
    """
    abaixar = contexto or musica_abaixada
    aviso: str | None = None
    if usar_online:
        caminho: Path | None = None
        try:
            # Fase 1 — geração (edge-tts/rede): SÓ falhas daqui acionam o fallback.
            try:
                caminho = gerar_audio_online(texto, voz_online, velocidade)
            except Exception:  # noqa: BLE001
                log.exception("Falha no motor online de voz.")
                _invalidar_cache_internet()
                if modo != "auto":
                    raise
                aviso = "O motor online falhou; foi usado o motor offline."
            # Fase 2 — reprodução: falha aqui NÃO repete o anúncio em voz offline
            # (o cliente poderia ouvir duas vezes); sobe como erro 500.
            if caminho is not None:
                with abaixar():
                    # apaga o mp3 ao terminar
                    executar_no_audio(_tocar_mp3, caminho, *(() if parar is None else (parar,)))
                return "online", None
        finally:
            apagar_audio(caminho)  # no-op se já foi apagado
    voz_id = resolver_voz_offline(voz_offline_id)
    if parar is not None:
        parar.nao_interrompivel()   # pyttsx3.runAndWait não tem como ser interrompido de fora
    with abaixar():
        executar_no_audio(_falar_offline, texto, VELOCIDADE_OFFLINE_WPM, voz_id)
    return ("offline_fallback" if aviso else "offline"), aviso


ANUNCIO_REMOTO_MAX_POR_MIN = 10   # /api/announce por IP REMOTO (o PC da loja não tem limite)


def _texto_do_anuncio(valor: Any) -> str:
    """
    O texto digitado: a MESMA higienização das Mensagens e da Programação (programacao.validar_texto):
    strip, 1..500 caracteres, sem controle/formato (bidi, invisíveis)/substituto/uso privado. v2.7.1 (P15).
    """
    texto, motivo = programacao.validar_texto(valor, ANUNCIO_TEXTO_MAX, multilinha=True)
    if motivo == "tipo":
        raise ErroApi("Campo 'text' inválido.")
    if motivo == "tamanho":
        raise ErroApi("Campo 'text' com tamanho inválido.")
    if motivo == "caractere":
        raise ErroApi("Campo 'text' tem um caractere que não é permitido.")
    return texto  # type: ignore[return-value]


@app.post("/api/announce")
def announce() -> Response | tuple[Response, int]:
    data = corpo_json()
    texto = _texto_do_anuncio(data.get("text"))
    modo = data.get("engine", "auto")
    if not isinstance(modo, str) or modo not in MOTORES:
        raise ErroApi("Campo 'engine' inválido.")
    chave_voz = data.get("voice", "francisca")
    if not isinstance(chave_voz, str) or chave_voz not in VOZES_ONLINE:
        raise ErroApi("Campo 'voice' inválido.")
    velocidade = validar_int(data.get("speed", 0), "speed", -50, 50, limitar=True)
    # v2.7.1 (P05): um aparelho com PIN não emenda anúncios sem limite (o PC da loja é livre).
    if not _requisicao_local() and excedeu_limite(
            "anuncio:" + (request.remote_addr or "?"), ANUNCIO_REMOTO_MAX_POR_MIN, 60.0):
        auditar("anuncio", caracteres=len(texto), resultado="limite")
        raise ErroApi("Muitos anúncios seguidos. Aguarde um instante.", 429)

    # Não enfileira: se já há um anúncio tocando, responde 409 imediatamente.
    if not _anuncio_lock.acquire(blocking=False):
        auditar("anuncio", caracteres=len(texto), resultado="ocupado")
        return _resp_erro(_msg_ocupado("Já existe um anúncio tocando. Aguarde terminar."), 409)
    resultado_audit = "erro"
    _avisos_musica_iniciar()   # v2.4.2: duck/restore do Spotify anotam aqui o que deu errado
    try:
        # v2.3: com "saida_anuncio" configurada o anúncio vai para a saída escolhida.
        # Sem a chave "som" (ou sem saída) o fluxo abaixo é o da v2.2.1, sem mudança.
        som = SOM.config_som()
        if not SOM.motor_ok:   # contaminado (executor trocado com o pygame preso): vale com ou sem "som"
            raise ErroSom("O áudio do Proclamare travou. Reinicie o Proclamare.", 503, "motor_contaminado")
        internet_ok = tem_internet()
        if modo == "online" and not internet_ok:
            resultado_audit = "sem_internet"
            return _resp_erro("Sem internet — não é possível usar o motor online.", 503)
        usar_online = internet_ok and modo != "offline"
        log.info("Anúncio: %d caracteres, motor=%s, online=%s", len(texto), modo, usar_online)
        extra: dict[str, Any] | None = None
        try:
            if som is not None and som["saida_anuncio"] is not None:
                engine_usado, aviso, extra = _anunciar_na_saida(
                    texto, modo, VOZES_ONLINE[chave_voz], velocidade,
                    data.get("offline_voice_id"), usar_online, som,
                )
            else:
                engine_usado, aviso = _anunciar(
                    texto, modo, VOZES_ONLINE[chave_voz], velocidade,
                    data.get("offline_voice_id"), usar_online,
                )
        except ErroSom:
            raise  # 503 com "codigo" (sem saída, em uso exclusivo, motor contaminado)
        except Exception:  # noqa: BLE001
            log.exception("Falha ao reproduzir o anúncio.")
            return _resp_erro("Não foi possível reproduzir o anúncio.", 500)
        limpar_audios_antigos()  # raramente faz algo (a cada ~1 h)
        resultado_audit = "ok"
        resposta: dict[str, Any] = {"ok": True, "engine_used": engine_usado}
        if aviso:
            resposta["warning"] = aviso
        if extra:
            resposta.update(extra)
        avisos_musica = _avisos_musica_coletar()
        if avisos_musica:   # "musica_nao_restaurada" tem prioridade sobre "musica_nao_abaixada"
            codigo = "musica_nao_restaurada" if "musica_nao_restaurada" in avisos_musica else avisos_musica[0]
            resposta["musica_aviso"] = codigo
            resposta["musica_aviso_texto"] = MUSICA_AVISOS[codigo]
        return jsonify(resposta)
    finally:
        _avisos_musica_coletar()   # descarta o que sobrou se o anúncio falhou
        _anuncio_lock.release()
        auditar("anuncio", caracteres=len(texto), resultado=resultado_audit)   # só o tamanho, nunca o texto


# ---------------------------------------------------------------------------
# Saídas de som (contrato v2.3, F1 — parte que LÊ)
# Todas as rotas /api/som/* exigem PIN para remotos (_exigir_sessao) e NENHUMA
# está em API_PUBLICAS. As respostas vêm de som/modelo.py (visões com lista
# branca): nunca devolvem endpoint_id, MAC, host, UUID nem segredos.
# Os handlers só leem o cache do SomService; a varredura roda em outra thread.
# ---------------------------------------------------------------------------
MSG_SOMENTE_LOCAL = "Disponível só no PC do Proclamare"
DESCOBRIR_MAX_POR_MIN = 6  # por IP


class ErroSom(ErroApi):
    """Erro de /api/som/*: além de "error" leva "erro" (contrato v2.3) e, se útil, "codigo"."""

    def __init__(self, mensagem: str, status: int = 400, codigo: str | None = None) -> None:
        super().__init__(mensagem, status)
        self.codigo = codigo


def _resp_erro_som(mensagem: str, status: int, codigo: str | None = None) -> tuple[Response, int]:
    corpo: dict[str, Any] = {"ok": False, "error": mensagem, "erro": mensagem}
    if codigo:
        corpo["codigo"] = codigo
    return jsonify(corpo), status


@app.errorhandler(ErroSom)
def _tratar_erro_som(e: ErroSom) -> tuple[Response, int]:
    return _resp_erro_som(e.mensagem, e.status, e.codigo)


@app.errorhandler(som_pkg.ErroEsquema)
def _tratar_erro_esquema(e: som_pkg.ErroEsquema) -> tuple[Response, int]:
    # A mensagem é montada por nós (nunca repete texto do cliente).
    return _resp_erro_som(str(e) or "Requisição inválida.", 400)


def somente_local(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorador (ponto único nº 1 da regra "somente local"): a rota inteira só atende o
    PC do Proclamare, MESMO com PIN (403). O critério é _requisicao_local(); com
    PROCLAMARE_CONFIAR_LOOPBACK=0 ninguém é local e a rota fica indisponível para todos.
    """
    @functools.wraps(func)
    def envoltorio(*args: Any, **kwargs: Any) -> Any:
        if not _requisicao_local():
            return _resp_erro_som(MSG_SOMENTE_LOCAL, 403)
        return func(*args, **kwargs)

    return envoltorio


def campos_somente_local(corpo_validado: dict, alvo: Any = None) -> None:
    """
    Ponto único nº 2: chamada DEPOIS de validar o esquema estrito e ANTES de qualquer
    efeito. Se o corpo traz campos que só valem no PC do Proclamare (aprovar/usar BT,
    rede, HDMI ou fone; se_indisponivel=padrao_windows; sessoes_locais; rede; microfone)
    e a requisição não é local, responde 403. `alvo`: Saida da rota /saida (ou lista de
    saídas nas zonas, F3). A regra em si mora em som_modelo.campos_locais_exigidos.
    """
    if som_modelo.campos_locais_exigidos(corpo_validado, alvo) and not _requisicao_local():
        raise ErroSom(MSG_SOMENTE_LOCAL, 403)


def _aparelhos_spotify_para_som() -> list[dict] | None:
    """Fonte da descoberta "spotify": aparelhos Connect da conta (None = sem token/sem rede)."""
    dados, status, _ = spotify_api("GET", "/me/player/devices")
    if status != 200:
        return None
    return [
        {"id": d.get("id"), "name": d.get("name"), "type": d.get("type"),
         "volume_percent": d.get("volume_percent"), "is_restricted": bool(d.get("is_restricted"))}
        for d in (dados.get("devices") or []) if isinstance(d, dict) and d.get("id")
    ]


# O serviço é criado já, mas só liga a varredura em main() (SOM.iniciar()).
# Armazém = o MESMO _config_lock/load_config/_gravar_config_atomico do servidor,
# para o read-modify-write da chave "som" ser atômico com o resto do config.
SOM = som_pkg.SomService(
    armazem=som_pkg.Armazem(lock=_config_lock, carregar=load_config, salvar=save_config),
    fonte_spotify=_aparelhos_spotify_para_som,
    motor=som_pkg.MotorAudio(teto_reproducao_s=TIMEOUT_REPRODUCAO_S),
)


@app.get("/api/som/estado")
def som_estado() -> Response:
    """Estado para o tile (poll de 10 s). Só lê o cache."""
    return jsonify(SOM.estado_publico(anuncio_em_andamento=_anuncio_lock.locked()))


@app.get("/api/som/saidas")
def som_saidas() -> Response:
    """Lista de saídas (poll de 1 s só com o assistente aberto). Só lê o cache + config."""
    return jsonify(SOM.saidas_publicas(remoto=not _requisicao_local()))


@app.post("/api/som/descobrir")
def som_descobrir() -> tuple[Response, int]:
    """Dispara a varredura em segundo plano (202). Ordem: 400, 403, 503, 429, 409."""
    tipos = som_modelo.validar_descobrir(corpo_json())  # 400: tipo fora da lista ou "rede" (F5)
    campos_somente_local({"tipos": tipos})
    if not SOM.disponivel:
        raise ErroSom("Saídas de som indisponíveis neste computador.", 503, "indisponivel")
    ip = request.remote_addr or "?"
    if excedeu_limite(f"descobrir:{ip}", DESCOBRIR_MAX_POR_MIN, 60.0):
        resp, status = _resp_erro_som("Muitas buscas seguidas. Aguarde um minuto.", 429)
        resp.headers["Retry-After"] = "10"
        return resp, status
    try:
        varredura = SOM.iniciar_varredura(tipos)
    except som_pkg.VarreduraEmAndamento:
        raise ErroSom("Já existe uma busca em andamento.", 409, "varredura_em_andamento") from None
    except som_pkg.SomIndisponivel:
        raise ErroSom("Saídas de som indisponíveis neste computador.", 503, "indisponivel") from None
    log.info("Busca de saídas de som iniciada (%s).", ",".join(tipos))
    return jsonify({"ok": True, "varredura": varredura}), 202


# ---------------------------------------------------------------------------
# Saídas de som — reprodução e rotas que ESCREVEM (contrato v2.3, F1 etapa 2)
# O áudio roda SEMPRE na thread de áudio (executar_no_audio). A saída é resolvida e
# ABERTA antes de abaixar a música; o finally de musica_abaixada() restaura sempre.
# ---------------------------------------------------------------------------
TESTAR_MAX_POR_MIN = 3             # por IP
TESTAR_GLOBAL_MAX = 10             # na loja toda...
TESTAR_GLOBAL_JANELA_S = 600.0     # ...a cada 10 minutos
ESCRITA_SOM_MAX_POR_MIN = 30       # POST /saida e /preferencias, por IP
SOM_ABRIR_TIMEOUT_S = 30.0         # teto para resolver + abrir o dispositivo


def _id_sessao() -> str:
    """
    Identificador da sessão do cliente para `testes_recentes` ("a MESMA sessão"). O PC da
    loja é "local"; aparelho remoto = hash do momento do login (`iat`, que as renovações
    do cookie preservam) + IP. Nunca o cookie. Estas rotas já exigem sessão válida.
    """
    if _requisicao_local():
        return "local"
    carga = _carga_token(request.cookies.get(COOKIE_SESSAO), load_config())
    if carga is None:
        return ""
    base = f"{carga[1]!r}|{request.remote_addr}"
    return "r:" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def _resp_429_som(mensagem: str, espera_s: int) -> tuple[Response, int]:
    resp, status = _resp_erro_som(mensagem, 429)
    resp.headers["Retry-After"] = str(espera_s)
    return resp, status


def _rotulo_saida(sid: str | None, som: dict) -> str:
    """Apelido (texto humano) ou rótulo genérico do tipo; nunca o nome do sistema."""
    if sid is None:
        return "Padrão do Windows"
    entrada = som["saidas"].get(sid) or {}
    if entrada.get("apelido"):
        return str(entrada["apelido"])
    saida = SOM.obter_saida(sid)
    return som_modelo.rotulo_do_subtipo(saida.subtipo if saida else entrada.get("subtipo"))


def _musica_abaixada_som(som: dict, ativo: bool = True) -> Any:
    """`musica_abaixada()` conforme som.abaixar (spotify_api/youtube); sem abaixar se `ativo` é falso."""
    if not ativo:
        return contextlib.nullcontext()
    return musica_abaixada(spotify=som["abaixar"]["spotify_api"], youtube=som["abaixar"]["youtube"])


# --- funções que rodam NA THREAD DE ÁUDIO ------------------------------------------
def _som_abrir_saida(candidatas: list[Any]) -> Any:
    """(Thread de áudio) Resolve o devicename na hora e abre a 1ª candidata que funcionar."""
    return SOM.motor.abrir_primeira(candidatas, SOM.resolver_devicename, SOM.classificar_falha)


def _som_sintetizar(texto: str, voz_id: str | None, destino: Path) -> Path:
    """(Thread de áudio) Voz offline para WAV (pyttsx3.save_to_file); quem toca é o motor."""
    return SOM.motor.sintetizar(texto, VELOCIDADE_OFFLINE_WPM, voz_id, destino)


def _som_medir(caminhos: list[Path]) -> list[float | None]:
    """(Thread de áudio) Durações dos arquivos, medidas ANTES de abaixar a música (mp3 é decodificado)."""
    return [SOM.motor.medir(c) for c in caminhos]


def _som_tocar(caminhos: list[Path], duracoes: list[float | None] | None = None,
               parar: Any = None) -> float:
    """(Thread de áudio) Toca os arquivos em sequência com o vigia de progresso; devolve segundos."""
    motor = SOM.motor
    prazo = motor.agora() + TIMEOUT_AUDIO_S - 5.0   # sempre antes do teto do executor
    medidas = duracoes if duracoes and len(duracoes) == len(caminhos) else [None] * len(caminhos)
    extra = {} if parar is None else {"parar": parar}   # v2.6: sem sinal, a chamada é a de sempre
    return sum(motor.tocar(c, prazo=prazo, duracao=d, **extra) for c, d in zip(caminhos, medidas))


def _abrir_saida_som(candidatas: list[Any]) -> Any:
    """Abre a saída ANTES de abaixar a música; falha vira 503 com "codigo" (nada foi tocado)."""
    try:
        return executar_no_audio(_som_abrir_saida, candidatas, timeout=SOM_ABRIR_TIMEOUT_S)
    except som_pkg.SaidaIndisponivel as exc:
        raise ErroSom(exc.mensagem, 503, exc.codigo) from None
    except TimeoutError:
        codigo = "ausente" if SOM.motor_ok else "motor_contaminado"
        raise ErroSom("A saída de som não respondeu.", 503, codigo) from None


def _anunciar_na_saida(texto: str, modo: str, voz_online: str, velocidade: int,
                       voz_offline_id: Any, usar_online: bool, som: dict, *,
                       contexto: Callable[[], Any] | None = None,
                       parar: Any = None) -> tuple[str, str | None, dict]:
    """
    Anúncio na saída configurada (v2.3). Devolve (engine_used, warning, extra da resposta).
    Ordem: gera o áudio (música ainda alta) -> resolve e ABRE a saída -> só então abaixa a
    música -> toca -> restaura (finally). Voz offline: pyttsx3 -> WAV -> o mesmo motor.
    Sem saída utilizável: ErroSom 503 com "codigo" e a música nem chega a ser abaixada.
    v2.6: `contexto` (pausar em vez de abaixar a música) e `parar` (SinalParada), como em _anunciar.
    """
    aviso: str | None = None
    caminho: Path | None = None
    try:
        if usar_online:
            try:
                caminho = gerar_audio_online(texto, voz_online, velocidade)
            except Exception:  # noqa: BLE001
                log.exception("Falha no motor online de voz.")
                _invalidar_cache_internet()
                if modo != "auto":
                    raise
                aviso = "O motor online falhou; foi usado o motor offline."
        engine = "online" if caminho is not None else ("offline_fallback" if aviso else "offline")
        usada = _abrir_saida_som(SOM.candidatas_anuncio(som))   # ANTES de abaixar a música
        if caminho is None:
            caminho = AUDIO_DIR / f"anuncio_{uuid.uuid4().hex}.wav"
            executar_no_audio(_som_sintetizar, texto, resolver_voz_offline(voz_offline_id), caminho)
        sid_medicao = usada.sid if usada.sid is not None else SOM.padrao_sid()
        duracoes = executar_no_audio(_som_medir, [caminho])   # fora da janela com a música baixa
        silenciar = contexto() if contexto is not None else _musica_abaixada_som(som)
        with SOM.medir_sinal(sid_medicao) as medida, silenciar:
            executar_no_audio(_som_tocar, [caminho], duracoes, *(() if parar is None else (parar,)))
        sinal = medida.resultado
    finally:
        apagar_audio(caminho)  # mp3 ou wav; no-op se já foi
    SOM.registrar_sinal(sinal)
    rotulo = _rotulo_saida(usada.sid, som)
    extra: dict[str, Any] = {
        "saidas_usadas": [{"zona_id": "loja", "apelido": rotulo, "resultado": "tocado"}],
        "parcial": False,
        "sinal_detectado": sinal,
    }
    if usada.sid != som["saida_anuncio"]:
        extra["aviso"] = f"A saída dos anúncios não estava disponível; o anúncio saiu em: {rotulo}."
        log.warning("Anúncio desviado para outra saída aprovada (fallback %s).", som["se_indisponivel"])
    return engine, aviso, extra


# --- POST /api/som/testar -------------------------------------------------------------
def _voz_offline_do_teste() -> str | None:
    """Voz offline padrão (pt-BR se houver) para a frase do teste; None = voz do sistema."""
    try:
        vozes = obter_vozes_offline()
    except Exception:  # noqa: BLE001
        return None
    return vozes["default"] if vozes else None


def _executar_teste(sid: str, corpo: dict, ip: str) -> tuple[Response, int] | Response:
    """(Com _anuncio_lock) Abre a saída, toca bipe + frase e mede o sinal da sessão própria."""
    som = SOM.config_som() or som_modelo.som_padrao()
    if not SOM.motor_ok:
        raise ErroSom("O áudio do Proclamare travou. Reinicie o Proclamare.", 503, "motor_contaminado")
    entrada = som["saidas"].get(sid) or {}
    estado = som_modelo.estado_da_saida(SOM.obter_saida(sid))
    if estado == "em_uso_exclusivo":
        raise ErroSom("Outro programa está usando esta saída em modo exclusivo.", 503, "em_uso_exclusivo")
    if estado in ("ausente", "desconectada", "nao_suportada"):
        raise ErroSom("Esta saída não está disponível agora.", 503, "ausente")
    abaixar = corpo.get("abaixar_musica")
    if abaixar is None:
        abaixar = som["teste"]["abaixar_musica_local" if _requisicao_local() else "abaixar_musica_remoto"]
    frase = frase_de_teste(entrada.get("apelido"), SOM.numero_da_saida(sid))
    arquivos: list[Path] = []
    try:
        _abrir_saida_som([som_pkg.Candidata(sid, entrada.get("apelido"))])
        bipe = AUDIO_DIR / f"anuncio_{uuid.uuid4().hex}.wav"
        arquivos.append(bipe)
        gerar_bipe(bipe)
        tocar: list[Path] = [bipe]
        fala = AUDIO_DIR / f"anuncio_{uuid.uuid4().hex}.wav"
        arquivos.append(fala)
        try:
            executar_no_audio(_som_sintetizar, frase, _voz_offline_do_teste(), fala)
            tocar.append(fala)
        except TimeoutError:   # síntese presa: o executor foi trocado; não segue tocando
            raise ErroSom("O áudio do Proclamare não respondeu.", 503,
                          "ausente" if SOM.motor_ok else "motor_contaminado") from None
        except Exception:  # noqa: BLE001 - sem voz offline: o bipe sozinho já testa a saída
            log.warning("Voz offline indisponível no teste; tocando só o bipe.")
        with SOM.medir_sinal(sid) as medida, _musica_abaixada_som(som, bool(abaixar)):
            segundos = executar_no_audio(_som_tocar, tocar)
    except som_pkg.ErroReproducao as exc:
        raise ErroSom(str(exc), 503, "ausente") from None
    except som_pkg.SaidaIndisponivel as exc:   # ex.: motor contaminado no meio do teste
        raise ErroSom(exc.mensagem, 503, exc.codigo) from None
    finally:
        for arquivo in arquivos:
            apagar_audio(arquivo)
        # O teste NUNCA muda por onde os anúncios saem: se a saída testada não é a dos anúncios
        # (ou não há saída de anúncio), solta o dispositivo para o fluxo antigo/outra saída
        # não herdarem o mixer aberto no HDMI/fone/BT testado.
        if sid != som.get("saida_anuncio") and SOM.motor_em_uso and SOM.motor_ok:
            try:
                executar_no_audio(SOM.motor.fechar, timeout=10.0)
            except Exception:  # noqa: BLE001
                log.warning("Não foi possível soltar a saída testada.", exc_info=True)
    SOM.registrar_teste(_id_sessao(), sid)
    auditoria.info("acao=testar ip=%s sid=%s sinal=%s", ip, sid, medida.resultado)
    return jsonify({"ok": True, "duracao_ms": int(round(segundos * 1000)), "sinal_detectado": medida.resultado})


@app.post("/api/som/testar")
def som_testar() -> tuple[Response, int] | Response:
    """
    Bipe + "Teste do Proclamare: <apelido ou 'saída N'>" numa saída. Permitido a remotos
    com PIN, de propósito. Ordem: 400, 404, 503 (indisponível), 429, 409, 503 (saída).
    """
    corpo = som_modelo.validar_testar(corpo_json())               # 400
    sid = corpo["sid"]
    if not SOM.sid_conhecido(sid):
        raise ErroSom("Saída não encontrada.", 404)               # 404: só vale sid que já existe
    saida = SOM.obter_saida(sid)
    if saida is not None and saida.tipo == "spotify_connect":
        raise ErroSom("Aparelhos do Spotify não recebem teste de anúncio.", 400)
    if not SOM.disponivel:
        raise ErroSom("Saídas de som indisponíveis neste computador.", 503, "indisponivel")
    ip = request.remote_addr or "?"
    if (excedeu_limite(f"testar:{ip}", TESTAR_MAX_POR_MIN, 60.0)
            or excedeu_limite("testar:global", TESTAR_GLOBAL_MAX, TESTAR_GLOBAL_JANELA_S)):
        return _resp_429_som("Muitos testes seguidos. Aguarde um pouco.", 20)
    if not _anuncio_lock.acquire(blocking=False):                 # 409: anúncio ou outro teste
        raise ErroSom("Já existe um anúncio ou teste tocando. Aguarde terminar.", 409)
    try:
        return _executar_teste(sid, corpo, ip)
    finally:
        _anuncio_lock.release()


# --- POST /api/som/saida --------------------------------------------------------------
def _fechar_mixer_som() -> None:
    """A saída dos anúncios foi esquecida: solta o dispositivo aberto pelo motor."""
    try:
        executar_no_audio(SOM.motor.fechar, timeout=10.0)
    except Exception:  # noqa: BLE001
        log.warning("Não foi possível fechar o mixer ao esquecer a saída.", exc_info=True)


def _adotar_nova_ref(som: dict, sid_novo: str, nova: Any, origem: str, agora: str) -> None:
    """
    `aceitar_nova_ref`: a saída NOVA (sid_novo, já descoberta) assume o lugar da saída
    aprovada que sumiu (a dos anúncios, ou a única candidata do mesmo tipo). A referência
    antiga some do config; apelido e perfil do rack passam para a nova, que precisa ser
    testada de novo (confirmada_em volta a nulo). Nunca é automático.
    """
    if nova is None or nova.estado_dispositivo != "disponivel":
        raise ErroSom("A saída nova não está disponível agora.", 400, "ausente")
    if sid_novo in som["saidas"]:
        raise ErroSom("Esta saída já está cadastrada.", 400)
    cache = SOM.instantaneo()
    candidatos = [
        s for s, e in som["saidas"].items()
        if e.get("aprovada") and e.get("tipo") == nova.tipo and e.get("subtipo") == nova.subtipo
        and (cache.get(s) is None or cache[s].estado_dispositivo == "ausente")
    ]
    if som["saida_anuncio"] in candidatos:
        velho = som["saida_anuncio"]
    elif len(candidatos) == 1:
        velho = candidatos[0]
    else:
        raise ErroSom("Não há uma saída aprovada ausente para substituir.", 400, "nada_a_adotar")
    antiga = som["saidas"].pop(velho)
    entrada = som_modelo.entrada_de_saida(nova, agora)
    entrada.update(apelido=antiga.get("apelido"), rack=antiga.get("rack") or entrada["rack"],
                   aprovada=True, aprovada_por=origem)
    som["saidas"][sid_novo] = entrada
    if som["saida_anuncio"] == velho:
        som["saida_anuncio"] = sid_novo
        som["padrao_referencia"] = SOM.padrao_sid()


@app.post("/api/som/saida")
def som_saida() -> tuple[Response, int] | Response:
    """
    Nomear, aprovar, confirmar, esquecer ou usar uma saída para os anúncios. Não toca som.
    Ordem: 400, 404, 403 (campos só locais), 429, 412 (teste recente), 409 (anúncio
    tocando), efeito. Troca REMOTA do destino: teste + "ouviu" da mesma sessão, auditoria e alerta.
    """
    corpo = som_modelo.validar_saida(corpo_json())                # 400
    sid = corpo["sid"]
    ip = request.remote_addr or "?"
    saida = SOM.obter_saida(sid)
    som_atual = SOM.config_som()
    entrada_atual = ((som_atual or {}).get("saidas") or {}).get(sid)
    if saida is None and entrada_atual is None:
        raise ErroSom("Saída não encontrada.", 404)               # 404: só vale sid que já existe
    if saida is not None and saida.tipo == "spotify_connect":
        raise ErroSom("Aparelhos do Spotify não são saídas de anúncio.", 400)
    alvo = saida if saida is not None else entrada_atual
    campos_somente_local(corpo, alvo)                             # 403, antes de qualquer efeito
    local = _requisicao_local()
    anuncio_atual = (som_atual or {}).get("saida_anuncio")
    if corpo.get("esquecer") and not local and (som_modelo.alvo_exige_local(alvo) or sid == anuncio_atual):
        raise ErroSom(MSG_SOMENTE_LOCAL, 403)
    if excedeu_limite(f"escrita-som:{ip}", ESCRITA_SOM_MAX_POR_MIN, 60.0):
        return _resp_429_som("Muitas alterações seguidas. Aguarde um minuto.", 10)
    sessao = _id_sessao()
    recente, ouviu = SOM.teste_recente(sessao, sid)
    if corpo.get("confirmada") and not recente:
        raise ErroSom("Teste esta saída e confirme que ouviu antes de usá-la.", 412, "teste_necessario")
    troca = bool(corpo.get("usar_para_anuncios") or corpo.get("aceitar_nova_ref"))
    if not local and (troca or corpo.get("aprovada") is True) and not (
            recente and (ouviu or corpo.get("confirmada"))):
        raise ErroSom("Teste esta saída e confirme que ouviu antes de usá-la.", 412, "teste_necessario")
    muda_destino = troca or (sid == anuncio_atual and (bool(corpo.get("esquecer")) or corpo.get("aprovada") is False))
    if muda_destino and not _anuncio_lock.acquire(blocking=False):
        raise ErroSom("Já existe um anúncio tocando. Aguarde terminar para trocar a saída.", 409)
    origem = "local" if local else "remoto"
    agora = datetime.now().astimezone().isoformat(timespec="seconds")

    def mutador(som: dict) -> None:
        saidas = som["saidas"]
        if corpo.get("esquecer"):
            saidas.pop(sid, None)
            if som["saida_anuncio"] == sid:
                som["saida_anuncio"] = None
            return
        if corpo.get("aceitar_nova_ref"):
            _adotar_nova_ref(som, sid, saida, origem, agora)
        entrada = saidas.get(sid)
        if entrada is None:
            if saida is None:
                raise ErroSom("Saída não encontrada.", 404)
            if len(saidas) >= som_modelo.MAX_SAIDAS:
                raise ErroSom("Limite de saídas guardadas atingido.", 400)
            entrada = saidas[sid] = som_modelo.entrada_de_saida(saida, agora)
        if "apelido" in corpo:
            entrada["apelido"] = corpo["apelido"]
        if "rack" in corpo:
            entrada["rack"].update(corpo["rack"])
        if "aprovada" in corpo:
            if not corpo["aprovada"] and som["saida_anuncio"] == sid:
                raise ErroSom("Esta é a saída dos anúncios; escolha outra antes de desaprovar.", 400, "saida_em_uso")
            entrada["aprovada"] = corpo["aprovada"]
            entrada["aprovada_por"] = origem if corpo["aprovada"] else None
        if corpo.get("confirmada"):
            entrada["confirmada_em"] = agora
        if corpo.get("usar_para_anuncios"):
            if saida is not None and saida.estado_dispositivo == "nao_suportada":
                raise ErroSom("O Proclamare não consegue tocar nesta saída.", 400, "nao_suportada")
            entrada["aprovada"] = True
            entrada["aprovada_por"] = origem
            som["saida_anuncio"] = sid
            som["padrao_referencia"] = SOM.padrao_sid()

    try:
        SOM.atualizar_config(mutador)
    finally:
        if muda_destino:
            _anuncio_lock.release()
    if corpo.get("confirmada"):
        SOM.marcar_ouviu(sessao, sid)
    if muda_destino:
        SOM.dispositivos_mudaram()                                # recria o stream no próximo uso
        if corpo.get("esquecer") and sid == anuncio_atual and SOM.motor_em_uso:
            _fechar_mixer_som()
    if troca:
        if local:
            SOM.limpar_alerta("saida_alterada_remoto")
        else:
            SOM.definir_alerta("saida_alterada_remoto", f"Saída de anúncio alterada por aparelho remoto (IP {ip}).")
    elif not local and ("aprovada" in corpo or (corpo.get("esquecer") and (entrada_atual or {}).get("aprovada"))):
        # aprovar/desaprovar/esquecer uma saída aprovada mexe nas reservas do fallback: avisa no tile
        SOM.definir_alerta("saida_alterada_remoto", f"Saídas aprovadas alteradas por aparelho remoto (IP {ip}).")
    auditoria.info("acao=saida ip=%s sid=%s origem=%s campos=%s", ip, sid, origem,
                   ",".join(sorted(k for k in corpo if k != "sid")))
    visoes = SOM.saidas_publicas(remoto=not local)["saidas"]
    return jsonify({"ok": True, "saida": next((v for v in visoes if v["sid"] == sid), None)})


# --- POST /api/som/preferencias --------------------------------------------------------
@app.post("/api/som/preferencias")
def som_preferencias() -> tuple[Response, int] | Response:
    """Merge parcial das preferências da F1 (esquema estrito). Ordem: 400, 403, 429, efeito."""
    corpo = som_modelo.validar_preferencias(corpo_json())         # 400 (inclui chaves das fases F2+)
    campos_somente_local(corpo)                                   # 403: se_indisponivel=padrao_windows
    ip = request.remote_addr or "?"
    if excedeu_limite(f"escrita-som:{ip}", ESCRITA_SOM_MAX_POR_MIN, 60.0):
        return _resp_429_som("Muitas alterações seguidas. Aguarde um minuto.", 10)
    som = SOM.atualizar_config(lambda s: som_modelo.aplicar_preferencias(s, corpo))
    if not _requisicao_local() and (
            "se_indisponivel" in corpo or "ativo" in (corpo.get("monitor") or {})):
        # fallback e monitor decidem se uma falha aparece: mudança remota fica visível no tile
        SOM.definir_alerta("saida_alterada_remoto", f"Preferências de saída alteradas por aparelho remoto (IP {ip}).")
    if not _requisicao_local():   # P04: fica pendente para o dono rever (config.json["som_alterado_por"])
        with contextlib.suppress(OSError):
            update_config(som_alterado_por={"em": int(time.time())})
    auditoria.info("acao=preferencias ip=%s origem=%s campos=%s", ip,
                   "local" if _requisicao_local() else "remoto", ",".join(sorted(corpo)))
    return jsonify({"ok": True, "preferencias": som_modelo.visao_preferencias(som)})


# ---------------------------------------------------------------------------
# Minhas músicas (contrato v2.4) — arquivos de áudio da pasta "Músicas do Proclamare"
# Toda rota exige PIN de aparelho remoto (nenhuma está em API_PUBLICAS). O cliente só
# conhece ids opacos; a reprodução é do <audio> da aba tocadora (nunca do pygame).
# A lógica (varredura, metadados, envio) mora em musicas.py; aqui ficam validação e HTTP.
# ---------------------------------------------------------------------------
ROTA_ENVIO_MUSICAS = "/api/musicas/enviar"
MUSICAS_ENVIO_HABILITADO = _env("MUSICAS_ENVIO", "1").strip() != "0"
MUSICAS_LISTA_MAX = 100             # itens por página
MUSICAS_ENVIO_MAX_POR_MIN = 12      # requisições de envio por IP
MUSICAS_ATUALIZAR_MAX_POR_MIN = 6   # por IP (atualizar e abrir-pasta)
MUSICAS_BUSCA_MAX = 100
MUSICAS_CAPA_MAX_POR_MIN = 600      # capas por IP (a lista pede uma por linha)
MAX_CORPO_ENVIO = musicas.MAX_BYTES_ENVIO + musicas.FOLGA_MULTIPART
TETO_CORPO_JSON = 64 * 1024         # corpo máximo de QUALQUER rota que não seja o envio de músicas
FOLGA_TEMP_ENVIO = 256 * 1024 * 1024  # espaço livre extra exigido no disco da pasta temporária

ACERVO = musicas.Acervo(musicas.pasta_padrao(_env("MUSICAS_DIR", "")))
_envio_sem = threading.BoundedSemaphore(2)   # no máximo 2 envios simultâneos (cada um grava em disco)


_envios_ativos = 0   # envios de músicas sendo processados agora (o "Desligar" espera terminarem)


def _envio_iniciar() -> None:
    """Conta um envio ativo; recusa (503) se o Proclamare já está desligando (contrato v2.5)."""
    global _envios_ativos
    with _desligar_lock:
        if _desligamento_agendado:
            raise ErroApi("O Proclamare está sendo desligado. Ligue de novo e tente enviar.", 503)
        _envios_ativos += 1


def _envio_terminar() -> None:
    global _envios_ativos
    with _desligar_lock:
        _envios_ativos = max(0, _envios_ativos - 1)


def _corpo_grande_permitido(metodo: str, caminho: str, cabecalhos: Any, origem: str) -> bool:
    """
    Filtro de corpo GRANDE, chamado pelo waitress logo depois de ler os cabeçalhos (ANTES de
    receber o corpo: o waitress guarda o corpo inteiro em disco antes de o Flask rodar, então o
    PIN, o Origin e o limite de taxa só valeriam depois do estrago). Só POST /api/musicas/enviar
    com o envio ligado e de quem é local ou traz um cookie de sessão válido recebe o teto de
    ~100 MB; todas as outras rotas e pessoas ficam em 64 KB.
    `cabecalhos`: mapa do waitress (chaves em maiúsculas, "-" vira "_"); `origem`: IP do socket.
    """
    if metodo != "POST" or caminho != ROTA_ENVIO_MUSICAS or not MUSICAS_ENVIO_HABILITADO:
        return False
    try:
        # chaves do waitress: maiúsculas, "-" vira "_" (CABECALHOS_DE_PROXY no mesmo formato)
        if _local_por(origem, cabecalhos.get("HOST", ""),
                      any(h.upper().replace("-", "_") in cabecalhos for h in CABECALHOS_DE_PROXY)):
            return True
        bruto = cabecalhos.get("COOKIE", "")
        if not bruto or len(bruto) > 4096:
            return False
        token = parse_cookie(bruto).get(COOKIE_SESSAO)
        return _token_valido_sessao(token, load_config())
    except Exception:  # noqa: BLE001 - cabeçalho torto: sem permissão, nunca erro
        return False


def _verificar_envio_multipart() -> tuple[Response, int] | None:
    """
    before_request de POST /api/musicas/enviar (única rota que não é JSON). Mantém as defesas
    do resto da API: Host e Origin já foram conferidos; aqui exigimos que o navegador tenha
    declarado a origem (Origin do mesmo host ou Sec-Fetch-Site same-origin), porque um
    <form> de outro site pode mandar multipart sem pré-voo CORS. Também fixa os limites de
    corpo desta requisição (o padrão global de 16 KB continua valendo para as demais).
    """
    if not MUSICAS_ENVIO_HABILITADO:
        return _resp_erro("O envio de músicas está desativado neste computador.", 403)
    if request.mimetype != "multipart/form-data":
        return _resp_erro("Content-Type deve ser multipart/form-data.", 415)
    if request.headers.get("Origin") is None and request.headers.get("Sec-Fetch-Site") != "same-origin":
        return _resp_erro("Origem não permitida.", 403)
    if request.content_length is not None and request.content_length > MAX_CORPO_ENVIO:
        return _resp_erro("Envio grande demais. Mande menos músicas de uma vez.", 413)
    # O corpo vai ser copiado (waitress -> Werkzeug -> arquivo final): sem espaço na pasta
    # temporária seria um "Erro interno" em vez de uma mensagem que o dono entende.
    try:
        livre_temp = shutil.disk_usage(tempfile.gettempdir()).free
    except OSError:
        livre_temp = None
    if livre_temp is not None and livre_temp < (request.content_length or MAX_CORPO_ENVIO) * 2 + FOLGA_TEMP_ENVIO:
        return _resp_erro("O disco do computador está quase cheio. Libere espaço e tente de novo.", 507)
    request.max_content_length = MAX_CORPO_ENVIO
    request.max_form_parts = musicas.MAX_ARQUIVOS_ENVIO + 10   # campos de texto + arquivos
    # Werkzeug 3.1 também aplica este teto ao buffer do decodificador multipart (pedaço de 64 KB + sobra
    # de fronteira): com exatamente 64 KB, qualquer arquivo de ~300 KB para cima dava 413 falso.
    request.max_form_memory_size = 256 * 1024
    return None


def _musica_da_rota(music_id: str) -> "musicas.Musica | None":
    return ACERVO.obter(music_id) if musicas.RE_ID.fullmatch(music_id) else None


@app.get("/api/musicas")
def musicas_listar() -> Response:
    # Busca por PALAVRAS (todas precisam aparecer, em qualquer ordem, sem acento nem caixa):
    # "garota ipanema" acha "Garota de Ipanema".
    termos = musicas.normalizar_busca(validar_str(request.args.get("q", ""), "q", MUSICAS_BUSCA_MAX)).split()
    pasta = validar_str(request.args.get("pasta", ""), "pasta", 300).replace("\\", "/").strip("/").casefold()
    offset = validar_int(request.args.get("offset", "0"), "offset", 0, 10**7)
    limite = validar_int(request.args.get("limit", "50"), "limit", 1, MUSICAS_LISTA_MAX, limitar=True)
    ordem = request.args.get("ordem", "recentes")
    if ordem not in ("recentes", "titulo"):
        raise ErroApi("Campo 'ordem' inválido.")
    versao, lista, truncado, varrendo = ACERVO.instantaneo()

    contagem: dict[str, int] = {}
    for m in lista:
        if m.pasta:
            contagem[m.pasta] = contagem.get(m.pasta, 0) + 1
    pastas = [{"nome": nome, "total": contagem[nome]} for nome in sorted(contagem, key=str.casefold)]

    achadas = [
        m for m in lista
        if all(t in m.busca for t in termos)
        and (not pasta or m.pasta.casefold() == pasta or m.pasta.casefold().startswith(pasta + "/"))
    ]
    if ordem == "titulo":
        achadas.sort(key=lambda m: (m.titulo.casefold(), m.rel))
    resp: dict[str, Any] = {
        "ok": True, "versao": versao, "total": len(achadas), "total_acervo": len(lista),
        "pastas": pastas, "itens": [m.publico() for m in achadas[offset: offset + limite]],
        "varrendo": varrendo, "truncado": truncado,
        "metadados": ACERVO.metadados_ok,   # False: falta o mutagen (só o nome dos arquivos aparece)
        "envio": {
            "habilitado": MUSICAS_ENVIO_HABILITADO,
            "max_arquivos": musicas.MAX_ARQUIVOS_ENVIO,
            "max_bytes_arquivo": musicas.MAX_BYTES_ARQUIVO,
            "max_bytes_total": musicas.MAX_BYTES_ENVIO,
            "formatos": sorted(musicas.TIPOS_AUDIO),
        },
    }
    if _requisicao_local():  # o caminho só aparece no próprio PC ("Coloque as músicas em ...")
        resp["pasta_local"] = str(ACERVO.raiz)
    return jsonify(resp)


@app.get("/api/musicas/<music_id>/audio")
def musicas_audio(music_id: str) -> Response | tuple[Response, int]:
    """O arquivo, com suporte a Range (o <audio> precisa para buscar/avançar)."""
    m = _musica_da_rota(music_id)
    real = ACERVO.caminho_seguro(m) if m is not None else None
    if m is None or real is None:
        return _resp_erro("Música não encontrada.", 404)
    try:
        # etag em texto: o do Werkzeug (etag=True) inclui um checksum do CAMINHO ABSOLUTO
        resp = send_file(real, mimetype=musicas.TIPOS_AUDIO[m.caminho.suffix.lower()],
                         conditional=True, etag=m.etag, as_attachment=False)
    except OSError:
        return _resp_erro("Música não encontrada.", 404)
    resp.headers.pop("Content-Disposition", None)  # nem o nome do arquivo precisa sair daqui
    return resp


@app.get("/api/musicas/<music_id>/capa")
def musicas_capa(music_id: str) -> Response | tuple[Response, int]:
    """Imagem embutida (jpeg|png, até 2 MB); 404 se não houver."""
    m = _musica_da_rota(music_id)
    if m is None or not m.tem_capa:
        return _resp_erro("Capa não encontrada.", 404)
    if request.if_none_match.contains(m.etag):      # revalidação: 304 sem abrir o arquivo
        resp = Response(status=304)
        resp.set_etag(m.etag)
        return resp
    if excedeu_limite("musicas-capa:" + (request.remote_addr or "?"), MUSICAS_CAPA_MAX_POR_MIN, 60.0):
        return _resp_erro("Muitos pedidos seguidos. Aguarde um pouco.", 429)
    capa = ACERVO.capa(m)
    if capa is None:
        return _resp_erro("Capa não encontrada.", 404)
    dados, mime = capa
    resp = Response(dados, mimetype=mime)
    resp.set_etag(m.etag)
    return resp.make_conditional(request)


def _recusado(nome: Any, motivo: str) -> dict[str, str]:
    return {"nome": musicas.nome_para_exibir(nome), "motivo": motivo}


@app.post("/api/musicas/enviar")
def musicas_enviar() -> Response | tuple[Response, int]:
    """
    Envio de músicas (multipart/form-data) pelo painel, inclusive da galeria do celular.
    Cada arquivo é validado e gravado por musicas.Acervo.receber (assinatura + mutagen +
    nome higienizado + temporário/os.replace + sem sobrescrever + disco e cota).
    """
    ip = request.remote_addr or "?"
    if excedeu_limite("musicas-envio:" + ip, MUSICAS_ENVIO_MAX_POR_MIN, 60.0):
        raise ErroApi("Muitos envios seguidos. Aguarde um minuto.", 429)
    if not _envio_sem.acquire(blocking=False):
        raise ErroApi("Já há um envio em andamento. Espere terminar e tente de novo.", 429)
    try:
        _envio_iniciar()
    except BaseException:
        _envio_sem.release()
        raise
    try:
        try:
            partes = list(request.files.items(multi=True))
        except RequestEntityTooLarge:
            raise ErroApi("Envio grande demais. Mande menos músicas de uma vez.", 413) from None
        if not partes:
            raise ErroApi("Nenhum arquivo foi enviado.")
        if len(partes) > musicas.MAX_ARQUIVOS_ENVIO:
            raise ErroApi(f"Envie no máximo {musicas.MAX_ARQUIVOS_ENVIO} músicas por vez.", 413)
        enviados: list[dict[str, Any]] = []
        recusados: list[dict[str, str]] = []
        parada: musicas.RecusaEnvio | None = None   # disco cheio / cota: o resto também falharia
        for _campo, arquivo in partes:
            if parada is not None:
                recusados.append(_recusado(arquivo.filename, parada.motivo))
                continue
            try:
                enviados.append(ACERVO.receber(arquivo.filename, arquivo.stream).publico())
            except musicas.RecusaEnvio as exc:
                recusados.append(_recusado(arquivo.filename, exc.motivo))
                if exc.para_o_lote:     # disco cheio, cota ou componente ausente: o resto também falharia
                    parada = exc
            except Exception:  # noqa: BLE001 - falha de um arquivo não derruba o lote
                log.exception("Falha ao salvar um arquivo enviado.")
                recusados.append(_recusado(arquivo.filename, "Não foi possível salvar o arquivo."))
        log.info("Envio de músicas: %d aceita(s), %d recusada(s).", len(enviados), len(recusados))
        auditar("musicas_enviar", aceitas=len(enviados), recusadas=len(recusados))
        if not _requisicao_local():   # P04: o dono revê o que aparelhos remotos mandaram para a pasta
            _registrar_musicas_remotas([e["id"] for e in enviados if isinstance(e.get("id"), str)])
        if enviados:
            return jsonify({"ok": True, "enviados": enviados, "recusados": recusados})
        status = parada.status if parada is not None else 422
        mensagem = parada.motivo if parada is not None else "Nenhuma música foi aceita."
        return jsonify({"ok": False, "error": mensagem, "enviados": [], "recusados": recusados}), status
    finally:
        _envio_terminar()
        _envio_sem.release()


@app.post("/api/musicas/remover")
@somente_local
def musicas_remover() -> Response | tuple[Response, int]:
    """SÓ no PC da loja: MOVE a música para "_removidas" (não apaga) e a tira da fila."""
    body = corpo_json()
    if set(body) - {"id"}:
        raise ErroApi("Campo desconhecido.")
    m = _musica_ou_erro(body.get("id"), "id")
    if not ACERVO.remover(m):
        raise ErroApi("Não consegui mover a música agora (ela pode estar em uso). Tente de novo.", 409)
    fila_purgar_musica(m.id)
    return jsonify({"ok": True})


@app.post("/api/musicas/abrir-pasta")
@somente_local
def musicas_abrir_pasta() -> Response | tuple[Response, int]:
    """SÓ no PC da loja: abre a pasta de músicas no Explorer."""
    if set(corpo_json()):
        raise ErroApi("Campo desconhecido.")
    if excedeu_limite("musicas-abrir:" + (request.remote_addr or "?"), MUSICAS_ATUALIZAR_MAX_POR_MIN, 60.0):
        raise ErroApi("Muitos pedidos seguidos. Aguarde um pouco.", 429)
    if not ACERVO.abrir_pasta():
        raise ErroApi("Não foi possível abrir a pasta neste computador.", 503)
    return jsonify({"ok": True})


@app.post("/api/musicas/atualizar")
def musicas_atualizar() -> tuple[Response, int]:
    """
    Pede uma varredura imediata (a lista já se atualiza sozinha a cada ~15 s). Com
    {"aguardar": true} espera até ~8 s a varredura terminar e responde 200 {ok, concluida,
    versao}; sem isso responde 202 na hora.
    """
    corpo = corpo_json()
    if set(corpo) - {"aguardar"}:
        raise ErroApi("Campo desconhecido.")
    aguardar = corpo.get("aguardar", False)
    if not isinstance(aguardar, bool):
        raise ErroApi("Campo 'aguardar' inválido.")
    if excedeu_limite("musicas-atualizar:" + (request.remote_addr or "?"), MUSICAS_ATUALIZAR_MAX_POR_MIN, 60.0):
        raise ErroApi("Muitos pedidos seguidos. Aguarde um pouco.", 429)
    concluida = ACERVO.pedir_varredura(8.0 if aguardar else 0.0)
    if not aguardar:
        return jsonify({"ok": True}), 202
    return jsonify({"ok": True, "concluida": concluida, "versao": ACERVO.instantaneo()[0]}), 200


# ---------------------------------------------------------------------------
# Mensagens prontas (contrato v2.5)
# Os atalhos de anúncio do painel. Ficam no config.json (chave "mensagens" + contador
# "mensagens_versao"); sem a chave vale a lista de fábrica. A lista é trocada INTEIRA, de forma
# atômica sob o _config_lock, e o contador de versão evita que dois aparelhos se atropelem (409).
# Nada daqui vai para o log além da QUANTIDADE de mensagens (o texto é da loja).
# ---------------------------------------------------------------------------
MENSAGENS_MAX = 24                  # itens na lista
MENSAGEM_TITULO_MAX = 40            # caracteres do nome do botão
MENSAGEM_TEXTO_MAX = ANUNCIO_TEXTO_MAX   # o mesmo teto do anúncio digitado
MENSAGENS_ESCRITA_MAX_POR_MIN = 20  # POST /api/mensagens e /restaurar, por IP
MENSAGENS_CORPO_MAX = 60 * 1024     # 24 x (500 + 40) caracteres acentuados passam dos 16 KB globais
RE_MENSAGEM_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")
_MENSAGEM_CHAVES = frozenset({"id", "titulo", "texto"})
MSG_VERSAO_MUDOU = "Outro aparelho mudou as mensagens. Recarregue para ver a versão atual."

# Lista de fábrica (os atalhos que o painel sempre teve). Ids fixos, para o cliente reconhecê-los.
MENSAGENS_DE_FABRICA: tuple[dict[str, str], ...] = (
    {"id": "fab-1", "titulo": "Farol aceso",
     "texto": "Atenção, o proprietário de um veículo deixou os faróis acesos no estacionamento. "
              "Por favor, verifique o seu carro."},
    {"id": "fab-2", "titulo": "Caixa rápido",
     "texto": "Atenção clientes, o caixa rápido está disponível para compras de até dez itens."},
    {"id": "fab-3", "titulo": "Chamado a colaborador",
     "texto": "Solicitamos a presença de um colaborador no setor de frios, por favor."},
    {"id": "fab-4", "titulo": "Loja fecha em 15 min",
     "texto": "Prezados clientes, informamos que a loja encerra suas atividades em quinze minutos."},
    {"id": "fab-5", "titulo": "Criança no balcão",
     "texto": "Atenção, temos uma criança aguardando no balcão de atendimento. "
              "Por favor, dirija-se até lá."},
    {"id": "fab-6", "titulo": "Teste de som",
     "texto": "Teste, um, dois, três. Este é um teste do sistema de som da loja."},
)


class ErroMensagem(ErroApi):
    """Erro de /api/mensagens: além de "error" leva "campo", "indice" (do item) e "codigo", se houver."""

    def __init__(self, mensagem: str, status: int = 400, *, campo: str | None = None,
                 indice: int | None = None, codigo: str | None = None) -> None:
        super().__init__(mensagem, status)
        self.campo = campo
        self.indice = indice
        self.codigo = codigo


@app.errorhandler(ErroMensagem)
def _tratar_erro_mensagem(e: ErroMensagem) -> tuple[Response, int]:
    corpo: dict[str, Any] = {"ok": False, "error": e.mensagem}
    for chave, valor in (("codigo", e.codigo), ("campo", e.campo), ("indice", e.indice)):
        if valor is not None:
            corpo[chave] = valor
    return jsonify(corpo), e.status


def _lista_de_fabrica() -> list[dict[str, str]]:
    """Cópia nova da lista de fábrica (quem recebe pode alterar à vontade)."""
    return [dict(m) for m in MENSAGENS_DE_FABRICA]


def _versao_mensagens(cfg: dict) -> int:
    """Contador do config.json; ausente ou estranho = 0."""
    v = cfg.get("mensagens_versao")
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _mensagens_do_config(cfg: dict) -> tuple[list[dict[str, str]], bool]:
    """
    (lista, de_fabrica). Sem a chave "mensagens" (ou com algo que não é lista) vale a fábrica.
    O que está gravado é revalidado de leve: um config.json editado à mão com um item torto
    perde só esse item, nunca derruba a rota.
    """
    bruto = cfg.get("mensagens")
    if not isinstance(bruto, list):
        return _lista_de_fabrica(), True
    lista: list[dict[str, str]] = []
    vistos: set[str] = set()
    for m in bruto[:MENSAGENS_MAX]:
        if not isinstance(m, dict) or not all(isinstance(m.get(k), str) for k in _MENSAGEM_CHAVES):
            continue
        if (not RE_MENSAGEM_ID.fullmatch(m["id"]) or m["id"] in vistos
                or not m["titulo"].strip() or not m["texto"].strip()):
            continue
        vistos.add(m["id"])
        lista.append({"id": m["id"], "titulo": m["titulo"], "texto": m["texto"]})
    return lista, False


def _mensagens_meta(cfg: dict) -> dict[str, dict[str, Any]]:
    """Carimbo de origem (criado_por/alterado_por/alterado_em) de cada mensagem gravada, por id (P04)."""
    meta: dict[str, dict[str, Any]] = {}
    bruto = cfg.get("mensagens")
    for m in (bruto[:MENSAGENS_MAX] if isinstance(bruto, list) else []):
        if isinstance(m, dict) and isinstance(m.get("id"), str):
            meta[m["id"]] = _meta_de_origem(m)
    return meta


def _resposta_mensagens(cfg: dict) -> Response:
    """Formato único do GET e dos POSTs: {ok, versao, de_fabrica, mensagens}."""
    lista, de_fabrica = _mensagens_do_config(cfg)
    return jsonify({"ok": True, "versao": _versao_mensagens(cfg),
                    "de_fabrica": de_fabrica, "mensagens": lista})


def _texto_da_mensagem(valor: Any, *, campo: str, rotulo: str, maximo: int,
                       multilinha: bool, indice: int) -> str:
    """
    Strip + 1..maximo caracteres + sem caracteres de controle. Quebra de linha (LF) só no texto;
    CRLF vira LF. A regra é UMA só para o Proclamare (programacao.validar_texto): rejeita controle
    (Cc), formato (Cf), substituto (Cs) e uso privado (Co), menos ZWJ/ZWNJ: texto invisível ou que
    inverte a direção não deve ir para a fala nem para a tela. "Cn" (ainda não atribuído) PASSA: o
    Python 3.10-3.13 conhece versões diferentes do Unicode e um emoji novo não pode dar 400.
    """
    quem = f"Mensagem {indice + 1}: "
    texto, motivo = programacao.validar_texto(valor, maximo, multilinha=multilinha)
    if motivo == "tipo":
        raise ErroMensagem(f"{quem}{rotulo} inválido.", campo=campo, indice=indice)
    if motivo == "tamanho":
        raise ErroMensagem(f"{quem}{rotulo} precisa ter de 1 a {maximo} caracteres.",
                           campo=campo, indice=indice)
    if motivo == "caractere":
        raise ErroMensagem(f"{quem}{rotulo} tem um caractere que não é permitido.",
                           campo=campo, indice=indice)
    return texto


def _validar_corpo_mensagens(corpo: dict, *, com_lista: bool) -> tuple[int, list[dict[str, Any]]]:
    """
    Esquema ESTRITO do POST. Devolve (versao, itens) com titulo/texto já limpos e "id" só quando o
    cliente mandou um. O id ainda precisa existir na lista atual (conferido sob o lock).
    """
    if set(corpo) - ({"versao", "mensagens"} if com_lista else {"versao"}):
        raise ErroMensagem("Campo desconhecido.", campo="chave")
    versao = corpo.get("versao")
    if isinstance(versao, bool) or not isinstance(versao, int) or not 0 <= versao <= 2**31:
        raise ErroMensagem("Campo 'versao' inválido.", campo="versao")
    if not com_lista:
        return versao, []
    lista = corpo.get("mensagens")
    if not isinstance(lista, list):
        raise ErroMensagem("Campo 'mensagens' inválido.", campo="mensagens")
    if len(lista) > MENSAGENS_MAX:
        raise ErroMensagem(f"No máximo {MENSAGENS_MAX} mensagens.", campo="mensagens")
    itens: list[dict[str, Any]] = []
    ids_vistos: set[str] = set()
    for i, bruto in enumerate(lista):
        if not isinstance(bruto, dict):
            raise ErroMensagem(f"Mensagem {i + 1} inválida.", campo="mensagens", indice=i)
        if set(bruto) - _MENSAGEM_CHAVES:
            raise ErroMensagem(f"Mensagem {i + 1}: campo desconhecido.", campo="chave", indice=i)
        item: dict[str, Any] = {
            "titulo": _texto_da_mensagem(bruto.get("titulo"), campo="titulo", rotulo="o nome do botão",
                                         maximo=MENSAGEM_TITULO_MAX, multilinha=False, indice=i),
            "texto": _texto_da_mensagem(bruto.get("texto"), campo="texto", rotulo="o texto anunciado",
                                        maximo=MENSAGEM_TEXTO_MAX, multilinha=True, indice=i),
        }
        ident = bruto.get("id")
        if ident is not None:   # null = mensagem nova, igual a não mandar o campo
            if not isinstance(ident, str) or not RE_MENSAGEM_ID.fullmatch(ident) or ident in ids_vistos:
                raise ErroMensagem(f"Mensagem {i + 1}: identificador inválido.", campo="id", indice=i)
            ids_vistos.add(ident)
            item["id"] = ident
        itens.append(item)
    return versao, itens


def _limitar_escrita_mensagens() -> None:
    """429 depois de MENSAGENS_ESCRITA_MAX_POR_MIN gravações por minuto vindas do mesmo IP."""
    if excedeu_limite("mensagens:" + (request.remote_addr or "?"), MENSAGENS_ESCRITA_MAX_POR_MIN, 60.0):
        raise ErroMensagem("Muitas alterações seguidas. Aguarde um minuto.", 429)


def _exigir_versao_mensagens(cfg: dict, enviada: int) -> None:
    """409 se outro aparelho já salvou depois que este carregou a lista."""
    if enviada != _versao_mensagens(cfg):
        raise ErroMensagem(MSG_VERSAO_MUDOU, 409, codigo="versao_mudou")


def _auditar_mensagens(acao: str, total: int) -> None:
    """Quem (local/IP) e QUANTAS mensagens. O conteúdo nunca vai para o log."""
    auditoria.info("acao=%s ip=%s origem=%s total=%d", acao, request.remote_addr or "?",
                   "local" if _requisicao_local() else "remoto", total)


@app.get("/api/mensagens")
def mensagens_listar() -> Response:
    """A lista atual (ou a de fábrica) com a versão para o próximo POST."""
    return _resposta_mensagens(load_config())


@app.post("/api/mensagens")
def mensagens_salvar() -> Response:
    """Substitui a lista INTEIRA. Ordem: 400, 429, 409; depois grava sob o lock do config."""
    request.max_content_length = MENSAGENS_CORPO_MAX   # só nesta rota; antes de ler o corpo
    versao, itens = _validar_corpo_mensagens(corpo_json(), com_lista=True)
    _limitar_escrita_mensagens()
    with _config_lock:
        cfg = load_config()
        _exigir_versao_mensagens(cfg, versao)
        atuais = {m["id"]: m for m in _mensagens_do_config(cfg)[0]}
        meta = _mensagens_meta(cfg)
        existentes = set(atuais)
        usados = {m["id"] for m in itens if "id" in m}
        origem = _origem_da_requisicao()
        agora = int(time.time())
        final: list[dict[str, Any]] = []
        for i, m in enumerate(itens):
            ident = m.get("id")
            if ident is None:   # mensagem nova: o servidor gera o id (estável a partir de agora)
                ident = programacao.novo_id(existentes | usados)
                usados.add(ident)
            elif ident not in existentes:
                raise ErroMensagem(f"Mensagem {i + 1}: esta mensagem não existe mais. Recarregue a lista.",
                                   campo="id", indice=i)
            antiga = atuais.get(ident)
            if antiga is not None and antiga["titulo"] == m["titulo"] and antiga["texto"] == m["texto"]:
                carimbo = meta.get(ident) or {"criado_por": "local", "alterado_por": "local", "alterado_em": 0}
            elif antiga is not None:   # mudou o conteúdo: o criador fica, o último a mexer é esta requisição
                carimbo = {"criado_por": (meta.get(ident) or {}).get("criado_por", "local"),
                           "alterado_por": origem, "alterado_em": agora}
            else:
                carimbo = {"criado_por": origem, "alterado_por": origem, "alterado_em": agora}
            final.append({"id": ident, "titulo": m["titulo"], "texto": m["texto"], **carimbo})
        cfg["mensagens"] = final
        cfg["mensagens_versao"] = _versao_mensagens(cfg) + 1
        _gravar_config_atomico(cfg)
    _contar_removidos_remoto("mensagens", len(existentes - {m["id"] for m in final}))
    _auditar_mensagens("mensagens", len(final))
    return _resposta_mensagens(cfg)


@app.post("/api/mensagens/restaurar")
def mensagens_restaurar() -> Response:
    """Volta à lista de fábrica (remove a chave). Já estando nela, não grava nem muda a versão."""
    versao, _ = _validar_corpo_mensagens(corpo_json(), com_lista=False)
    _limitar_escrita_mensagens()
    with _config_lock:
        cfg = load_config()
        _exigir_versao_mensagens(cfg, versao)
        removidas = len(_mensagens_do_config(cfg)[0]) if "mensagens" in cfg else 0
        if "mensagens" in cfg:
            cfg.pop("mensagens")
            cfg["mensagens_versao"] = _versao_mensagens(cfg) + 1
            _gravar_config_atomico(cfg)
    _contar_removidos_remoto("mensagens", removidas)
    _auditar_mensagens("mensagens_restaurar", len(MENSAGENS_DE_FABRICA))
    return _resposta_mensagens(cfg)


# ---------------------------------------------------------------------------
# Listas de músicas (contrato v2.6)
# Listas criadas NO PAINEL ("Nova lista"): nome + músicas do acervo, em ordem. Ficam em
# listas.json AO LADO do config.json (não dentro dele): até 50 listas x 500 músicas passam de
# 500 KB, e o config.json (segredos do Spotify, PIN) é regravado a cada renovação de token e
# a cada ajuste de volume. Arquivo próprio = o config continua pequeno e sem dados da loja;
# mesma gravação atômica (temporário + os.replace) e mesma DACL restrita. A "versão" não
# fica no config: cada lista tem a sua (409 só quando OUTRO aparelho mexeu NA MESMA lista) e o
# arquivo tem uma versão geral (muda a cada gravação) para o painel saber que algo mudou.
# O cliente só conhece ids opacos (music_id do acervo e lista_id de 8 hex); nunca caminho.
# Música que sumiu da pasta continua na lista como "indisponível" (guardamos um título curto só
# para o dono saber qual foi) e é pulada ao tocar. Nada daqui vai para o log além de contagens.
# ---------------------------------------------------------------------------
LISTAS_ARQUIVO = "listas.json"
LISTAS_MAX = 50                     # listas no total
LISTA_NOME_MAX = 40                 # caracteres do nome
LISTA_MUSICAS_MAX = 500             # músicas por lista
LISTA_TITULO_GUARDADO_MAX = 60      # título guardado de cada música (só para exibir se ela sumir)
LISTAS_ESCRITA_MAX_POR_MIN = 20     # POST de criar/salvar/apagar, por IP
LISTAS_TOCAR_MAX_POR_10S = 6        # POST .../tocar, por IP
# v2.7: a lista aceita links do YouTube (até 300 caracteres cada, como na fila): 500 links passam de 32 KB. O teto
# sobe para 60 KB SÓ nestas rotas. NÃO dá para 96 KB: o waitress corta em 64 KB (waitress_limites.py) toda rota
# que não seja o envio de músicas, então 60 KB é o maior valor que o servidor consegue honrar. A forma "youtube"
# ({"youtube": {...}}, campos nulos podem ser omitidos) cabe em ~55 KB mesmo com 500 itens.
LISTAS_CORPO_MAX = 60 * 1024
LISTA_ITENS_MAX = LISTA_MUSICAS_MAX  # 500 itens por lista (músicas da pasta + YouTube)
ATALHOS_SPOTIFY_MAX = 50            # atalhos do Spotify (v2.7)
RE_LISTA_ID = re.compile(r"[0-9a-f]{8}")
MSG_LISTA_VERSAO_MUDOU = "Outro aparelho mudou esta lista. Recarregue para ver a versão atual."
MSG_LISTA_NAO_EXISTE = "Essa lista não existe mais."
MSG_SEM_TOCADOR = "Nenhum tocador aberto. Abra o modo Tocador da loja primeiro."
MSG_LENDO_PASTA = "O Proclamare está lendo a pasta de músicas. Tente de novo em instantes."
_LISTA_CHAVES_CRIAR = frozenset({"nome", "musicas", "itens"})
_LISTA_CHAVES_SALVAR = frozenset({"versao", "nome", "musicas", "itens"})


class ErroLista(ErroMensagem):
    """Erro de /api/listas: o mesmo formato do /api/mensagens ({ok, error, codigo?, campo?, indice?})."""


def _listas_path() -> Path:
    """listas.json na pasta do config.json (os testes trocam CONFIG_PATH e levam este junto)."""
    return CONFIG_PATH.with_name(LISTAS_ARQUIVO)


def _titulo_guardado(valor: Any) -> str:
    """Título curto guardado junto do id (texto simples, sem controle, até 60 caracteres)."""
    if not isinstance(valor, str):
        return ""
    return programacao.limpar_exibicao(valor)[:LISTA_TITULO_GUARDADO_MAX].strip()


def _inteiro(valor: Any, minimo: int, maximo: int, padrao: int) -> int:
    ok = isinstance(valor, int) and not isinstance(valor, bool) and minimo <= valor <= maximo
    return valor if ok else padrao


# --- item do YouTube dentro de uma lista (v2.7) ---------------------------------------------------
_YT_CAMPOS = ("video_id", "list_id", "index", "start")
MSG_ITEM_YOUTUBE_INVALIDO = "Esse item do YouTube não é válido."


def _validar_youtube(bruto: Any) -> dict[str, Any]:
    """
    Campos de um item do YouTube com as MESMAS regras do parse_youtube_url: video_id (11 caracteres) e/ou
    list_id (2..64, Mix "RD..." NÃO vale), index (0..199, só com list_id) e start (0..86400 s). Chaves
    ausentes = null; zero em index/start vira null (é o mesmo que não ter). Nunca guarda URL crua.
    Levanta ValueError com uma mensagem nossa (pode ir ao cliente).
    """
    if not isinstance(bruto, dict) or set(bruto) - set(_YT_CAMPOS):
        raise ValueError(MSG_ITEM_YOUTUBE_INVALIDO)
    video_id, list_id = bruto.get("video_id"), bruto.get("list_id")
    index, start = bruto.get("index"), bruto.get("start")
    if video_id is not None and not (isinstance(video_id, str) and _RE_VIDEO_ID.fullmatch(video_id)):
        raise ValueError(MSG_ITEM_YOUTUBE_INVALIDO)
    if list_id is not None and not (isinstance(list_id, str) and _RE_LIST_ID.fullmatch(list_id)
                                    and not list_id.upper().startswith("RD")):
        raise ValueError(MSG_ITEM_YOUTUBE_INVALIDO)
    if video_id is None and list_id is None:
        raise ValueError(MSG_ITEM_YOUTUBE_INVALIDO)
    if index is not None:
        if (isinstance(index, bool) or not isinstance(index, int) or list_id is None
                or not 0 <= index < YOUTUBE_PLAYLIST_MAX):
            raise ValueError(MSG_ITEM_YOUTUBE_INVALIDO)
    if start is not None:
        if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= SEGUNDOS_MAX:
            raise ValueError(MSG_ITEM_YOUTUBE_INVALIDO)
    return {"video_id": video_id, "list_id": list_id, "index": index or None, "start": start or None}


def _chave_youtube(alvo: dict[str, Any]) -> tuple:
    """Identidade de um item do YouTube na lista: (video_id, list_id, index, start)."""
    return tuple(alvo.get(c) for c in _YT_CAMPOS)


def _url_youtube_da_lista(alvo: dict[str, Any]) -> str:
    """Link canônico (montado SÓ com ids já validados: nunca a URL que o usuário colou)."""
    video_id, list_id, index, start = (alvo.get(c) for c in _YT_CAMPOS)
    if video_id:
        url = f"https://www.youtube.com/watch?v={video_id}" + (f"&list={list_id}" if list_id else "")
    else:
        url = f"https://www.youtube.com/playlist?list={list_id}"
    if list_id and index is not None:
        url += f"&index={index + 1}"
    if start:
        url += f"&t={start}s"
    return url


def _sanear_item_lista(e: Any) -> dict[str, Any] | None:
    """Um item lido do disco: música da pasta (entrada antiga sem "tipo" também) ou YouTube; torto = None."""
    if not isinstance(e, dict):
        return None
    tipo = e.get("tipo", "musica")
    if tipo == "musica":
        mid = e.get("id")
        if not isinstance(mid, str) or not musicas.RE_ID.fullmatch(mid):
            return None
        return {"tipo": "musica", "id": mid, "titulo": _titulo_guardado(e.get("titulo"))}
    if tipo == "youtube":
        try:
            alvo = _validar_youtube({c: e.get(c) for c in _YT_CAMPOS})
        except ValueError:
            return None
        return {"tipo": "youtube", **alvo, "titulo": _titulo_guardado(e.get("titulo")) or None}
    return None


def _meta_de_origem(bruto: dict) -> dict[str, Any]:
    """
    Carimbo de origem lido do disco (P04): criado_por (imutável), alterado_por (quem mexeu por último no
    CONTEÚDO) e alterado_em (epoch). Registro antigo, sem carimbo, vale "local" (não há como saber).
    """
    criado = bruto.get("criado_por") if bruto.get("criado_por") in programacao.ORIGENS_ITEM else "local"
    alterado = bruto.get("alterado_por") if bruto.get("alterado_por") in programacao.ORIGENS_ITEM else criado
    return {"criado_por": criado, "alterado_por": alterado,
            "alterado_em": _inteiro(bruto.get("alterado_em"), 0, 2**40, 0)}


def _carimbar(registro: dict, *, novo: bool) -> None:
    """Grava no registro quem (local|remoto) o criou/alterou agora. É o SERVIDOR que decide; o cliente não manda."""
    origem = _origem_da_requisicao()
    if novo:
        registro["criado_por"] = origem
    registro["alterado_por"] = origem
    registro["alterado_em"] = int(time.time())


def _sanear_atalho_spotify(a: Any) -> dict[str, Any] | None:
    if not isinstance(a, dict):
        return None
    ident, uri = a.get("id"), a.get("uri")
    nome = programacao.texto_limpo(a.get("nome"), LISTA_NOME_MAX, multilinha=False)
    m = RE_URI_SPOTIFY_ATALHO.fullmatch(uri) if isinstance(uri, str) else None
    if not isinstance(ident, str) or not RE_LISTA_ID.fullmatch(ident) or nome is None or m is None:
        return None
    return {"id": ident, "nome": nome, "uri": _uri_spotify(m.group(1), m.group(2)), "versao": _inteiro(a.get("versao"), 1, 2**31, 1),
            "atualizada_em": _inteiro(a.get("atualizada_em"), 0, 2**40, 0), **_meta_de_origem(a)}


def _sanear_listas(bruto: Any) -> dict:
    """
    Revalida o que foi lido do disco. Um listas.json editado à mão perde só o item torto
    (lista sem id/nome, id ou nome repetido, item inválido ou repetido, atalho do Spotify torto);
    nunca derruba a rota.
    """
    doc: dict = {"versao": 0, "listas": [], "spotify": [], "spotify_versao": 0}
    if not isinstance(bruto, dict):
        return doc
    doc["versao"] = _inteiro(bruto.get("versao"), 0, 2**31, 0)
    doc["spotify_versao"] = _inteiro(bruto.get("spotify_versao"), 0, 2**31, 0)
    brutas = bruto.get("listas")
    ids_vistos: set[str] = set()
    nomes_vistos: set[str] = set()
    for lista in (brutas[:LISTAS_MAX] if isinstance(brutas, list) else []):
        if not isinstance(lista, dict):
            continue
        ident, nome = lista.get("id"), lista.get("nome")
        if (not isinstance(ident, str) or not RE_LISTA_ID.fullmatch(ident) or ident in ids_vistos):
            continue
        nome = programacao.texto_limpo(nome, LISTA_NOME_MAX, multilinha=False)   # a mesma regra do POST
        if nome is None or nome.casefold() in nomes_vistos:
            continue
        faixas: list[dict[str, Any]] = []
        vistos: set[Any] = set()
        itens = lista.get("musicas")   # a chave continua "musicas" (a lista mistura músicas e YouTube)
        for bruto_item in (itens[:LISTA_ITENS_MAX] if isinstance(itens, list) else []):
            item = _sanear_item_lista(bruto_item)
            if item is None:
                continue
            chave = item["id"] if item["tipo"] == "musica" else _chave_youtube(item)
            if chave in vistos:
                continue
            vistos.add(chave)
            faixas.append(item)
        ids_vistos.add(ident)
        nomes_vistos.add(nome.casefold())
        doc["listas"].append({
            "id": ident, "nome": nome, "versao": _inteiro(lista.get("versao"), 1, 2**31, 1),
            "atualizada_em": _inteiro(lista.get("atualizada_em"), 0, 2**40, 0), "musicas": faixas,
            **_meta_de_origem(lista),
        })
    atalhos = bruto.get("spotify")
    ids_vistos, nomes_vistos = set(), set()
    for a in (atalhos[:ATALHOS_SPOTIFY_MAX] if isinstance(atalhos, list) else []):
        atalho = _sanear_atalho_spotify(a)
        if atalho is None or atalho["id"] in ids_vistos or atalho["nome"].casefold() in nomes_vistos:
            continue
        ids_vistos.add(atalho["id"])
        nomes_vistos.add(atalho["nome"].casefold())
        doc["spotify"].append(atalho)
    return doc


# Armazém: o MESMO do programacao.json (programacao.ArquivoJson): cache validado por os.stat, gravação
# atômica com DACL antes dos dados e cópia "<nome>.corrompido-*" se o arquivo ficar ilegível. Regra:
# quem lê NÃO muda o que recebe; quem grava monta uma cópia (copy.deepcopy), grava e troca o cache.
# (v2.7: o mesmo arquivo guarda também os atalhos do Spotify, na chave "spotify".)
_listas_arquivo = programacao.ArquivoJson(
    _listas_path, lambda: {"versao": 0, "listas": [], "spotify": [], "spotify_versao": 0},
    lambda bruto: _sanear_listas(bruto),
    lambda caminho: _restringir_acl_windows(caminho), rotulo=LISTAS_ARQUIVO, prefixo=".listas.")


def listas_ler() -> dict:
    """
    Documento atual {"versao", "listas": [{id, nome, versao, atualizada_em, musicas: [item]}], "spotify":
    [atalho], "spotify_versao"}; item = {"tipo":"musica","id","titulo"} | {"tipo":"youtube",video_id,list_id,
    index,start,"titulo"|None}. SOMENTE LEITURA (é o cache): quem quiser alterar usa copy.deepcopy. Relê o
    disco só quando o arquivo mudou (um os.stat por chamada). Arquivo ilegível: mantém o último documento válido.
    """
    return _listas_arquivo.ler()


def lista_obter(lista_id: Any) -> dict | None:
    """A lista (registro do cache, SOMENTE LEITURA) ou None. Para a programação (etapa 2) também."""
    if not isinstance(lista_id, str) or not RE_LISTA_ID.fullmatch(lista_id):
        return None
    return next((lista for lista in listas_ler()["listas"] if lista["id"] == lista_id), None)


def _tocador_vivo() -> bool:
    """True se a aba "Tocador da loja" mandou sinal nos últimos YOUTUBE_PLAYER_TIMEOUT segundos."""
    with _yt_estado_lock:
        beat = _yt_estado["last_heartbeat"]
        return beat is not None and (time.monotonic() - beat) < YOUTUBE_PLAYER_TIMEOUT


def _titulo_youtube(alvo: dict[str, Any], guardado: str | None, pedir: bool = True) -> str | None:
    """
    Título de um item do YouTube: o guardado, senão o do cache da fila (oEmbed, o mesmo host de sempre).
    Sem nenhum dos dois pede a busca em segundo plano (None até resolver; o painel mostra "Vídeo do
    YouTube"/"Playlist do YouTube").
    """
    if guardado:
        return guardado
    url = _yt_url_canonica(alvo.get("video_id"), alvo.get("list_id"))
    achou, titulo = _titulo_em_cache(url)
    if achou:
        return _titulo_guardado(titulo) or None
    if pedir:
        _pedir_titulo(url)
    return None


def _resumo_lista(lista: dict) -> dict[str, Any]:
    """
    Visão de lista para o GET geral. `total` conta tudo; `youtube` = itens do YouTube; `disponiveis` = músicas
    da pasta que existem + itens do YouTube (sempre disponíveis), ou None enquanto a pasta ainda não foi lida
    (lista só com YouTube não depende da pasta).
    """
    itens = lista["musicas"]
    yt = sum(1 for e in itens if e["tipo"] == "youtube")
    if ACERVO.pronto:
        disponiveis: int | None = yt + sum(
            1 for e in itens if e["tipo"] == "musica" and ACERVO.obter(e["id"]) is not None)
    else:
        disponiveis = yt if itens and yt == len(itens) else None
    return {
        "id": lista["id"], "nome": lista["nome"], "versao": lista["versao"], "atualizada_em": lista["atualizada_em"],
        "total": len(itens), "youtube": yt, "disponiveis": disponiveis,
    }


def _detalhe_lista(lista: dict, buscar_titulos: bool = True) -> dict[str, Any]:
    """
    Resumo + os itens na ordem da lista. Música indisponível = só o id e o título que foi guardado.
    `buscar_titulos=False`: título que falta só vem do cache, sem pedir a busca ao YouTube.
    """
    pronto = ACERVO.pronto
    itens: list[dict[str, Any]] = []
    for e in lista["musicas"]:
        if e["tipo"] == "youtube":
            itens.append({
                "tipo": "youtube", "kind": "playlist" if e["list_id"] else "video",
                "video_id": e["video_id"], "list_id": e["list_id"], "index": e["index"], "start": e["start"],
                "titulo": _titulo_youtube(e, e["titulo"], buscar_titulos), "disponivel": True,
                "url": _url_youtube_da_lista(e),
            })
            continue
        m = ACERVO.obter(e["id"])
        if m is not None:
            itens.append({**m.publico(), "tipo": "musica", "disponivel": True})
        else:
            itens.append({"tipo": "musica", "id": e["id"], "titulo": e["titulo"] or None,
                          "disponivel": False if pronto else None})
    resumo = _resumo_lista(lista)
    resumo["musicas"] = itens
    return resumo


TITULOS_GRAVAR_INTERVALO_S = 30.0    # no máximo UMA gravação de títulos a cada ~30 s (todas as listas juntas)
LISTA_DETALHE_BUSCA_MAX_POR_MIN = 60  # GET /api/listas/<id> que PEDE títulos ao YouTube, por IP
_titulos_gravar_lock = threading.Lock()
_titulos_gravar_timer: threading.Timer | None = None
_titulos_gravar_ultimo = -1e9         # time.monotonic() da última gravação de títulos


def _gravar_titulos_youtube() -> bool:
    """
    Grava no listas.json, de UMA vez, os títulos do YouTube que o cache já resolveu para itens de QUALQUER lista (para
    aparecerem sem internet e depois de reiniciar). Não é uma alteração do dono: NÃO mexe em versao nem em
    atualizada_em. Só grava se algo mudou. Falha de disco é ignorada. True = gravou.
    """
    global _titulos_gravar_ultimo
    try:
        with _listas_arquivo.lock:
            doc = copy.deepcopy(listas_ler())
            mudou = False
            for lista in doc["listas"]:
                for e in lista["musicas"]:
                    if e["tipo"] != "youtube" or e["titulo"]:
                        continue
                    achou, titulo = _titulo_em_cache(_yt_url_canonica(e["video_id"], e["list_id"]))
                    limpo = _titulo_guardado(titulo) if achou else ""
                    if limpo:
                        e["titulo"] = limpo
                        mudou = True
            if mudou:
                _listas_arquivo.gravar(doc)
                _titulos_gravar_ultimo = time.monotonic()
            return mudou
    except OSError:
        log.warning("Não foi possível guardar os títulos do YouTube das listas.")
        return False


def _gravar_titulos_agora() -> None:
    """O que o temporizador chama: libera o agendamento e grava."""
    global _titulos_gravar_timer
    with _titulos_gravar_lock:
        _titulos_gravar_timer = None
    _gravar_titulos_youtube()


def _agendar_gravacao_de_titulos() -> None:
    """
    Pede a gravação dos títulos resolvidos (debounce): um único temporizador por vez; a gravação acontece quando
    passaram TITULOS_GRAVAR_INTERVALO_S desde a anterior (ou já, se faz tempo). Vários GETs e vários títulos
    resolvidos viram UMA escrita do arquivo.
    """
    global _titulos_gravar_timer
    with _titulos_gravar_lock:
        if _titulos_gravar_timer is not None:
            return
        espera = max(0.0, _titulos_gravar_ultimo + TITULOS_GRAVAR_INTERVALO_S - time.monotonic())
        _titulos_gravar_timer = threading.Timer(espera, _gravar_titulos_agora)
        _titulos_gravar_timer.daemon = True
        _titulos_gravar_timer.start()


def _cancelar_gravacao_de_titulos() -> None:
    """Para o temporizador pendente (os testes e o desligamento)."""
    global _titulos_gravar_timer
    with _titulos_gravar_lock:
        if _titulos_gravar_timer is not None:
            _titulos_gravar_timer.cancel()
            _titulos_gravar_timer = None


def _titulo_resolvido_para_listas(url: str | None) -> None:
    """Chamado quando o oEmbed resolve um título: se alguma lista tem esse item sem título, agenda a gravação."""
    if url is None:
        return
    for lista in listas_ler()["listas"]:
        for e in lista["musicas"]:
            if e["tipo"] == "youtube" and not e["titulo"] and _yt_url_canonica(e["video_id"], e["list_id"]) == url:
                _agendar_gravacao_de_titulos()
                return


def _limites_listas() -> dict[str, int]:
    return {"listas": LISTAS_MAX, "nome": LISTA_NOME_MAX, "musicas": LISTA_MUSICAS_MAX, "itens": LISTA_ITENS_MAX}


def _nome_de(valor: Any, rotulo: str) -> str:
    """Nome: texto, strip, 1..40 caracteres, UMA linha, sem controle/formato/substituto/uso privado."""
    nome, motivo = programacao.validar_texto(valor, LISTA_NOME_MAX, multilinha=False)
    if motivo == "tipo":
        raise ErroLista(f"O nome {rotulo} é inválido.", campo="nome")
    if motivo == "tamanho":
        raise ErroLista(f"O nome {rotulo} precisa ter de 1 a {LISTA_NOME_MAX} caracteres.", campo="nome")
    if motivo == "caractere":
        raise ErroLista(f"O nome {rotulo} tem um caractere que não é permitido.", campo="nome")
    return nome


def _nome_de_lista(valor: Any) -> str:
    return _nome_de(valor, "da lista")


def _ids_de_musicas(valor: Any) -> list[str]:
    """Lista de music_id: 0..500, formato do acervo, sem repetição (400 com o "indice" do problema)."""
    if not isinstance(valor, list):
        raise ErroLista("Campo 'musicas' inválido.", campo="musicas")
    if len(valor) > LISTA_MUSICAS_MAX:
        raise ErroLista(f"Cada lista pode ter no máximo {LISTA_MUSICAS_MAX} músicas.", campo="musicas")
    vistos: set[str] = set()
    for i, mid in enumerate(valor):
        if not isinstance(mid, str) or not musicas.RE_ID.fullmatch(mid):
            raise ErroLista(f"Música {i + 1} inválida.", campo="musicas", indice=i)
        if mid in vistos:
            raise ErroLista(f"A música {i + 1} já está na lista.", campo="musicas", indice=i)
        vistos.add(mid)
    return list(valor)


def _entradas_de_ids(ids: list[str]) -> list[dict[str, Any]]:
    return [{"tipo": "musica", "id": mid} for mid in ids]


def _entradas_de_itens(valor: Any) -> list[dict[str, Any]]:
    """
    Campo "itens" (v2.7): [{"music_id"} | {"url"} | {"youtube": {video_id,list_id,index,start}}], 0..500, na
    ordem. O link passa pelo parse_youtube_url (mesmas regras da fila); a forma "youtube" é validada igual.
    Repetido (mesma música, ou mesmo video_id+list_id+index+start) = 400 com o "indice" do segundo.
    """
    if not isinstance(valor, list):
        raise ErroLista("Campo 'itens' inválido.", campo="itens")
    if len(valor) > LISTA_ITENS_MAX:
        raise ErroLista(f"Cada lista pode ter no máximo {LISTA_ITENS_MAX} itens.", campo="itens")
    entradas: list[dict[str, Any]] = []
    vistos: set[Any] = set()
    for i, bruto in enumerate(valor):
        n = i + 1
        if not isinstance(bruto, dict) or len(bruto) != 1 or next(iter(bruto)) not in ("music_id", "url", "youtube"):
            raise ErroLista(f"Item {n} inválido.", campo="itens", indice=i)
        (forma, conteudo), = bruto.items()
        if forma == "music_id":
            if not isinstance(conteudo, str) or not musicas.RE_ID.fullmatch(conteudo):
                raise ErroLista(f"Item {n}: música inválida.", campo="itens", indice=i)
            entrada: dict[str, Any] = {"tipo": "musica", "id": conteudo}
            chave: Any = conteudo
        else:
            try:
                if forma == "url":
                    try:
                        conteudo = parse_youtube_url(conteudo)
                    except ErroApi as e:
                        texto = e.mensagem
                        if texto.startswith("Campo 'url'"):
                            texto = "isso não parece um link do YouTube."
                        raise ValueError(texto) from None
                alvo = _validar_youtube(conteudo)
            except ValueError as e:
                raise ErroLista(f"Item {n}: {e}", campo="itens", indice=i) from None
            entrada = {"tipo": "youtube", **alvo}
            chave = _chave_youtube(alvo)
        if chave in vistos:
            raise ErroLista(f"O item {n} já está na lista.", campo="itens", indice=i)
        vistos.add(chave)
        entradas.append(entrada)
    return entradas


def _entradas_do_corpo(corpo: dict) -> list[dict[str, Any]] | None:
    """Os itens do POST: "itens" (v2.7) OU "musicas" (lista de ids, como na v2.6); os dois juntos = 400."""
    if "itens" in corpo and "musicas" in corpo:
        raise ErroLista("Use 'itens' ou 'musicas', não os dois.", campo="itens")
    if "itens" in corpo:
        return _entradas_de_itens(corpo["itens"])
    if "musicas" in corpo:
        return _entradas_de_ids(_ids_de_musicas(corpo["musicas"]))
    return None


def _faixas_da_lista(entradas: list[dict[str, Any]], anteriores: dict[Any, str | None],
                     campo: str = "musicas") -> list[dict[str, Any]]:
    """
    Monta os itens guardados. Música presente no acervo: guarda o título atual. Fora do acervo: só passa se
    JÁ estava na lista (re-salvar uma lista com indisponíveis) e conserva o título antigo; senão 400 (id
    inventado ou de música removida). Pasta ainda não lida = 409 (não dá para saber se a música existe; o
    painel tenta de novo). YouTube: título que já estava na lista, senão o do cache (ou None até resolver).
    `anteriores` mapeia id da música -> título e (video_id,list_id,index,start) -> título.
    """
    faixas: list[dict[str, Any]] = []
    for i, e in enumerate(entradas):
        if e["tipo"] == "youtube":
            alvo = {c: e[c] for c in _YT_CAMPOS}
            faixas.append({"tipo": "youtube", **alvo,
                           "titulo": _titulo_youtube(alvo, anteriores.get(_chave_youtube(alvo)))})
            continue
        mid = e["id"]
        m = ACERVO.obter(mid)
        if m is not None:
            faixas.append({"tipo": "musica", "id": mid, "titulo": _titulo_guardado(m.titulo)})
        elif mid in anteriores:
            faixas.append({"tipo": "musica", "id": mid, "titulo": anteriores[mid] or ""})
        elif not ACERVO.pronto:
            raise ErroLista(MSG_LENDO_PASTA, 409, codigo="lendo_pasta")
        else:
            raise ErroLista(f"A música {i + 1} não está mais na pasta.", campo=campo, indice=i)
    return faixas


def _titulos_anteriores(lista: dict) -> dict[Any, str | None]:
    """Títulos já guardados na lista (música por id, YouTube por video_id+list_id+index+start)."""
    return {(e["id"] if e["tipo"] == "musica" else _chave_youtube(e)): e["titulo"] for e in lista["musicas"]}


def _limitar_escrita_listas() -> None:
    if excedeu_limite("listas:" + (request.remote_addr or "?"), LISTAS_ESCRITA_MAX_POR_MIN, 60.0):
        raise ErroLista("Muitas alterações seguidas. Aguarde um minuto.", 429)


def _auditar_listas(acao: str, total: int, resultado: str | None = None) -> None:
    """
    Quem (local/IP) e QUANTAS músicas. Nome da lista e ids nunca vão para o log. `resultado` (opcional) é o
    "codigo" estável de uma falha (ex.: premium), nunca texto livre, nome, id ou uri.
    """
    extra = f" resultado={resultado}" if resultado else ""
    auditoria.info("acao=%s ip=%s origem=%s total=%d%s", acao, request.remote_addr or "?",
                   "local" if _requisicao_local() else "remoto", total, extra)


def _versao_da_lista(corpo: dict) -> int:
    v = corpo.get("versao")
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 2**31:
        raise ErroLista("Campo 'versao' inválido.", campo="versao")
    return v


def _lista_da_rota(lista_id: str) -> str:
    """Formato do id na URL: id mal formado nem chega a procurar (404, como uma lista que não existe)."""
    if not RE_LISTA_ID.fullmatch(lista_id):
        raise ErroLista(MSG_LISTA_NAO_EXISTE, 404)
    return lista_id


def _nome_em_uso(doc: dict, nome: str, exceto: str | None = None) -> bool:
    chave = nome.casefold()
    return any(lista["nome"].casefold() == chave and lista["id"] != exceto for lista in doc["listas"])


@app.get("/api/listas")
def listas_listar() -> Response:
    """Resumo de todas as listas (sem os itens; para eles há GET /api/listas/<id>)."""
    doc = listas_ler()
    return jsonify({"ok": True, "versao": doc["versao"], "limites": _limites_listas(),
                    "acervo_pronto": ACERVO.pronto,
                    "listas": [_resumo_lista(lista) for lista in doc["listas"]]})


@app.get("/api/listas/<lista_id>")
def listas_detalhe(lista_id: str) -> Response:
    """Uma lista com os itens, na ordem. Músicas que sumiram: disponivel false. YouTube: sempre true."""
    lista = lista_obter(_lista_da_rota(lista_id))
    if lista is None:
        raise ErroLista(MSG_LISTA_NAO_EXISTE, 404)
    # Itens do YouTube sem título: este GET pede a busca em 2º plano e agenda a gravação (com debounce). O limite
    # leve (60/min por IP) vale SÓ para esse trabalho extra: passou dele, a lista continua saindo (200), mas sem
    # pedir títulos nem gravar nada (nunca um 429 numa leitura que o painel faz ao abrir uma lista).
    sem_titulo = any(e["tipo"] == "youtube" and not e["titulo"] for e in lista["musicas"])
    buscar = sem_titulo and not excedeu_limite(
        "listas-titulos:" + (request.remote_addr or "?"), LISTA_DETALHE_BUSCA_MAX_POR_MIN, 60.0)
    detalhe = _detalhe_lista(lista, buscar_titulos=buscar)
    if buscar:
        _agendar_gravacao_de_titulos()
    return jsonify({"ok": True, "limites": _limites_listas(), "lista": detalhe})


def _sem_titulos(faixas: list[dict[str, Any]]) -> list[tuple]:
    """Os itens da lista sem o título guardado (o título é enfeite: reenviar a mesma lista não é mudar o conteúdo)."""
    return [(f["id"],) if f["tipo"] == "musica" else (_chave_youtube(f),) for f in faixas]


def _criar_lista(doc: dict, nome: str, faixas: list[dict[str, Any]]) -> dict:
    """Acrescenta a lista nova ao `doc` (cópia sob o lock do armazém) e devolve o registro. Sem gravar."""
    ident = programacao.novo_id({lista["id"] for lista in doc["listas"]})
    nova = {"id": ident, "nome": nome, "versao": 1, "atualizada_em": int(time.time()), "musicas": faixas}
    _carimbar(nova, novo=True)
    doc["listas"].append(nova)
    doc["versao"] += 1
    return nova


def _exigir_espaco_para_lista(doc: dict, nome: str) -> None:
    if len(doc["listas"]) >= LISTAS_MAX:
        raise ErroLista(f"Você já tem {LISTAS_MAX} listas. Apague alguma para criar outra.", 409,
                        codigo="limite_listas")
    if _nome_em_uso(doc, nome):
        raise ErroLista("Já existe uma lista com esse nome.", 409, codigo="nome_repetido", campo="nome")


@app.post("/api/listas")
def listas_criar() -> tuple[Response, int]:
    """
    Cria uma lista {nome, itens? | musicas?}. Ordem: 400, 429, 409 (limite / nome repetido). "itens" (v2.7)
    mistura músicas da pasta e links do YouTube; "musicas" (lista de ids) continua valendo; os dois = 400.
    """
    request.max_content_length = LISTAS_CORPO_MAX
    corpo = corpo_json()
    if set(corpo) - _LISTA_CHAVES_CRIAR:
        raise ErroLista("Campo desconhecido.", campo="chave")
    nome = _nome_de_lista(corpo.get("nome"))
    entradas = _entradas_do_corpo(corpo) or []
    _limitar_escrita_listas()
    with _listas_arquivo.lock:
        doc = copy.deepcopy(listas_ler())
        _exigir_espaco_para_lista(doc, nome)
        faixas = _faixas_da_lista(entradas, {}, "itens" if "itens" in corpo else "musicas")
        nova = _criar_lista(doc, nome, faixas)
        _listas_arquivo.gravar(doc)
    _auditar_listas("lista_criar", len(faixas))
    return jsonify({"ok": True, "versao": doc["versao"], "lista": _detalhe_lista(nova)}), 201


@app.post("/api/listas/da-fila")
def listas_da_fila() -> tuple[Response, int]:
    """
    "Salvar fila como lista" (v2.7) {nome, incluir_atual?: bool = true}. O SERVIDOR monta os itens: o que está
    tocando agora na aba Tocador (se incluir_atual e o tocador estiver vivo) + a fila atual, na ordem; repetidos
    são ignorados e passou de 500 corta (resposta com "cortados"). O cliente nunca manda os itens. Ordem: 400,
    429, 409 ("fila_vazia", "limite_listas", "nome_repetido", "lendo_pasta").
    """
    corpo = corpo_json()
    if set(corpo) - {"nome", "incluir_atual"}:
        raise ErroLista("Campo desconhecido.", campo="chave")
    nome = _nome_de_lista(corpo.get("nome"))
    incluir_atual = corpo.get("incluir_atual", True)
    if not isinstance(incluir_atual, bool):
        raise ErroLista("Campo 'incluir_atual' inválido.", campo="incluir_atual")
    _limitar_escrita_listas()
    entradas, ignorados = _entradas_da_fila(incluir_atual)
    cortados = max(0, len(entradas) - LISTA_ITENS_MAX)
    entradas = entradas[:LISTA_ITENS_MAX]
    if not entradas:
        raise ErroLista("Não há nada na fila para salvar.", 409, codigo="fila_vazia")
    with _listas_arquivo.lock:
        doc = copy.deepcopy(listas_ler())
        _exigir_espaco_para_lista(doc, nome)
        faixas = _faixas_da_lista(entradas, {}, "itens")
        nova = _criar_lista(doc, nome, faixas)
        _listas_arquivo.gravar(doc)
    _auditar_listas("lista_da_fila", len(faixas))
    return jsonify({"ok": True, "versao": doc["versao"], "lista": _detalhe_lista(nova),
                    "cortados": cortados, "ignorados": ignorados}), 201


def _entradas_da_fila(incluir_atual: bool) -> tuple[list[dict[str, Any]], int]:
    """
    Itens (músicas e YouTube) do que toca agora + a fila do Proclamare, sem repetidos, na ordem. Devolve
    (entradas, ignorados): ignorados = repetidos, músicas que já saíram da pasta e itens tortos. Tudo é
    lido do ESTADO DO SERVIDOR sob os locks (estado -> fila, a ordem de sempre) e revalidado.
    Regra do "tocando agora": entra se a aba Tocador está viva E há um item atual de verdade: arquivo com
    music_id, ou vídeo/playlist do YouTube com o player carregado; parado conta (pausado), EXCETO se a faixa já
    terminou (parada com a posição recente no fim da duração) ou se o tocador está ocioso (nada em cena).
    """
    with _yt_estado_lock:
        agora = time.monotonic()
        beat = _yt_estado["last_heartbeat"]
        vivo = beat is not None and (agora - beat) < YOUTUBE_PLAYER_TIMEOUT
        atual = {k: _yt_estado[k] for k in ("source", "video_id", "list_id", "music_id")} if vivo else None
        if atual is not None and not _yt_estado["is_playing"]:
            # Parado pode ser PAUSADO (entra) ou TERMINADO (o video_id fica para trás; não entra): terminado =
            # posição recente a menos de 2 s do fim da duração conhecida.
            em, pos, dur = _yt_estado["posicao_em"], _yt_estado["posicao_s"], _yt_estado["duracao_s"]
            if (em is not None and agora - em <= YOUTUBE_PLAYER_TIMEOUT and pos is not None and dur
                    and pos >= dur - 2.0):
                atual = None
        if atual is not None and atual["source"] == "youtube" and not _yt_estado["yt_pronto"]:
            atual = None   # ocioso: sem o player do YouTube não há vídeo de verdade em cena
        with _yt_fila_lock:
            fila = [dict(i) for i in _yt_fila]
    brutos: list[dict[str, Any]] = []
    if incluir_atual and atual is not None:
        if atual["source"] == "arquivo" and atual["music_id"]:
            brutos.append({"kind": "arquivo", "music_id": atual["music_id"]})
        elif atual["list_id"] or atual["video_id"]:
            # Playlist em reprodução: a playlist inteira (do começo); vídeo avulso: o vídeo. O ponto em que
            # ela está e o "t=" do link original não são conhecidos aqui (e nem faria sentido guardar).
            brutos.append({"kind": "playlist" if atual["list_id"] else "video",
                           "video_id": None if atual["list_id"] else atual["video_id"],
                           "list_id": atual["list_id"]})
    brutos.extend(fila)
    entradas: list[dict[str, Any]] = []
    vistos: set[Any] = set()
    ignorados = 0
    for it in brutos:
        if it.get("kind") == "arquivo":
            mid = it.get("music_id")
            if not isinstance(mid, str) or not musicas.RE_ID.fullmatch(mid):
                ignorados += 1
                continue
            if ACERVO.obter(mid) is None:
                if not ACERVO.pronto:
                    raise ErroLista(MSG_LENDO_PASTA, 409, codigo="lendo_pasta")
                ignorados += 1   # saiu da pasta depois de entrar na fila
                continue
            entrada: dict[str, Any] = {"tipo": "musica", "id": mid}
            chave: Any = mid
        else:
            try:
                alvo = _validar_youtube({c: it.get(c) for c in _YT_CAMPOS})
            except ValueError:
                ignorados += 1
                continue
            entrada = {"tipo": "youtube", **alvo}
            chave = _chave_youtube(alvo)
        if chave in vistos:
            ignorados += 1
            continue
        vistos.add(chave)
        entradas.append(entrada)
    return entradas, ignorados


@app.post("/api/listas/<lista_id>")
def listas_salvar(lista_id: str) -> Response:
    """
    Renomeia e/ou define os itens (lista INTEIRA, na ordem). {versao, nome?, itens? | musicas?} com pelo
    menos um dos dois. Ordem: 404 (id mal formado), 400, 429, 404 (lista), 409 (versão / nome).
    """
    _lista_da_rota(lista_id)
    request.max_content_length = LISTAS_CORPO_MAX
    corpo = corpo_json()
    if set(corpo) - _LISTA_CHAVES_SALVAR:
        raise ErroLista("Campo desconhecido.", campo="chave")
    versao = _versao_da_lista(corpo)
    if "nome" not in corpo and "musicas" not in corpo and "itens" not in corpo:
        raise ErroLista("Informe o nome e/ou os itens.", campo="chave")
    nome = _nome_de_lista(corpo["nome"]) if "nome" in corpo else None
    entradas = _entradas_do_corpo(corpo)
    _limitar_escrita_listas()
    with _listas_arquivo.lock:
        doc = copy.deepcopy(listas_ler())
        lista = next((x for x in doc["listas"] if x["id"] == lista_id), None)
        if lista is None:
            raise ErroLista(MSG_LISTA_NAO_EXISTE, 404)
        if versao != lista["versao"]:
            raise ErroLista(MSG_LISTA_VERSAO_MUDOU, 409, codigo="versao_mudou")
        if nome is not None and _nome_em_uso(doc, nome, exceto=lista_id):
            raise ErroLista("Já existe uma lista com esse nome.", 409, codigo="nome_repetido", campo="nome")
        antes = (lista["nome"], copy.deepcopy(lista["musicas"]))
        novas_faixas = None
        if entradas is not None:
            novas_faixas = _faixas_da_lista(entradas, _titulos_anteriores(lista),
                                            "itens" if "itens" in corpo else "musicas")
        mudou_conteudo = (nome is not None and nome != antes[0]) or (
            novas_faixas is not None and _sem_titulos(novas_faixas) != _sem_titulos(antes[1]))
        if mudou_conteudo:
            _prog_checar_alvo_remoto("lista", lista_id)   # remoto mexendo no que um horário toca conta no teto diário
        if novas_faixas is not None:
            lista["musicas"] = novas_faixas
        if nome is not None:
            lista["nome"] = nome
        lista["versao"] += 1
        lista["atualizada_em"] = int(time.time())
        if mudou_conteudo:   # salvar igual NÃO marca: quem só reenviou a mesma lista não "assume" nem "suja" o item
            _carimbar(lista, novo=False)
        doc["versao"] += 1
        _listas_arquivo.gravar(doc)
    _auditar_listas("lista_salvar", len(lista["musicas"]))
    return jsonify({"ok": True, "versao": doc["versao"], "lista": _detalhe_lista(lista)})


@app.post("/api/listas/<lista_id>/apagar")
def listas_apagar(lista_id: str) -> Response:
    """Apaga a lista {versao}. As músicas continuam na pasta; só a lista some."""
    _lista_da_rota(lista_id)
    corpo = corpo_json()
    if set(corpo) - {"versao"}:
        raise ErroLista("Campo desconhecido.", campo="chave")
    versao = _versao_da_lista(corpo)
    _limitar_escrita_listas()
    with _listas_arquivo.lock:
        doc = copy.deepcopy(listas_ler())
        lista = next((x for x in doc["listas"] if x["id"] == lista_id), None)
        if lista is None:
            raise ErroLista(MSG_LISTA_NAO_EXISTE, 404)
        if versao != lista["versao"]:
            raise ErroLista(MSG_LISTA_VERSAO_MUDOU, 409, codigo="versao_mudou")
        doc["listas"].remove(lista)
        doc["versao"] += 1
        _listas_arquivo.gravar(doc)
    _contar_removidos_remoto("listas", 1)
    _auditar_listas("lista_apagar", len(lista["musicas"]))
    return jsonify({"ok": True, "versao": doc["versao"]})


def tocar_lista(lista_id: str, embaralhar: bool = False) -> dict[str, Any]:
    """
    Toca uma lista no Tocador da loja: o MESMO mecanismo do "Tocar pasta" (fila esvaziada, itens
    entram, o primeiro sai da fila e vai num comando "load" para a aba). Sem contexto de
    requisição: a rota e a programação (etapa 2) chamam esta função.
    v2.7: a lista mistura músicas da pasta e itens do YouTube (vídeo ou playlist, como os itens da fila).
    Lista só com YouTube não depende da pasta de músicas; música sumida é pulada. Sem o player do YouTube
    carregado na aba (yt_pronto false) os itens do YouTube são pulados (como o play_now recusa).
    Levanta ErroLista: 404 (lista não existe / nada dela pode tocar), 409 com "codigo" "sem_tocador"
    (nenhuma aba Tocador viva; NADA é mexido na fila), "lendo_pasta" ou "youtube_indisponivel" (só tem
    YouTube e o player da aba não carregou).
    Devolve {total, adicionados, indisponiveis, cortados, queue_version, primeiro, id, youtube,
    pulados_youtube}.
    """
    lista = lista_obter(lista_id)
    if lista is None:
        raise ErroLista(MSG_LISTA_NAO_EXISTE, 404)
    itens = lista["musicas"]
    if any(e["tipo"] == "musica" for e in itens) and not ACERVO.pronto:
        raise ErroLista(MSG_LENDO_PASTA, 409, codigo="lendo_pasta")
    if not _tocador_vivo():   # aviso cedo e claro; é conferido de novo, sob o lock, antes de mexer na fila
        raise ErroLista(MSG_SEM_TOCADOR, 409, codigo="sem_tocador")
    achadas: list[Any] = []   # Musica | item do YouTube (dict), na ordem da lista
    indisponiveis = 0
    for e in itens:
        if e["tipo"] == "youtube":
            achadas.append(e)
            continue
        m = ACERVO.obter(e["id"])
        if m is None:
            indisponiveis += 1
        else:
            achadas.append(m)
    if not achadas:
        texto = ("Nenhuma música dessa lista está na pasta." if not any(e["tipo"] == "youtube" for e in itens)
                 else "Nenhum item dessa lista pode tocar agora.")
        raise ErroLista(texto, 404, codigo="sem_musicas")
    if embaralhar:
        random.shuffle(achadas)
    # Ordem de locks igual à do play_now: estado -> fila. O tocador precisa estar vivo ANTES de a
    # fila antiga ser trocada (senão a lista ficaria na fila sem ninguém para tocá-la).
    pulados = 0
    with _yt_estado_lock:
        beat = _yt_estado["last_heartbeat"]
        if beat is None or (time.monotonic() - beat) >= YOUTUBE_PLAYER_TIMEOUT:
            raise ErroLista(MSG_SEM_TOCADOR, 409, codigo="sem_tocador")
        if not _yt_estado["yt_pronto"]:
            # tocador "vivo" só para arquivos (sem internet/API do YouTube): um "load" do YouTube ficaria
            # parado para sempre (a mesma regra do play_now)
            antes = len(achadas)
            achadas = [a for a in achadas if not isinstance(a, dict)]
            pulados = antes - len(achadas)
            if not achadas:
                raise ErroLista("O player do YouTube desta aba ainda não carregou. Confira a internet ou "
                                "toque uma música do computador.", 409, codigo="youtube_indisponivel")
        for a in achadas[:YOUTUBE_FILA_MAX]:   # o título guardado aparece na fila sem esperar o oEmbed
            if isinstance(a, dict) and a.get("titulo"):
                _semear_titulo(_yt_url_canonica(a["video_id"], a["list_id"]), a["titulo"])
        novos, cortados, versao_fila, primeiro = _adicionar_arquivos(
            achadas, posicao="fim", substituir=True, tirar_primeiro=True)
    comando = youtube_enqueue("load", _params_load(primeiro)) if primeiro is not None else None
    _marcar_musica_substituida()   # a lista tomou o lugar da música: quem pausou a anterior não a devolve
    return {
        "total": len(itens), "adicionados": len(novos),
        "indisponiveis": indisponiveis, "cortados": cortados,
        "queue_version": versao_fila, "primeiro": _item_publico(primeiro) if primeiro else None,
        "id": comando, "youtube": sum(1 for n in novos if n["kind"] != "arquivo"), "pulados_youtube": pulados,
    }


@app.post("/api/listas/<lista_id>/tocar")
def listas_tocar(lista_id: str) -> Response:
    """Toca a lista {embaralhar?}. 404 (lista / nada para tocar), 409 (sem Tocador vivo), 429."""
    _lista_da_rota(lista_id)
    corpo = corpo_json()
    if set(corpo) - {"embaralhar"}:
        raise ErroLista("Campo desconhecido.", campo="chave")
    embaralhar = corpo.get("embaralhar", False)
    if not isinstance(embaralhar, bool):
        raise ErroLista("Campo 'embaralhar' inválido.", campo="embaralhar")
    if excedeu_limite("listas-tocar:" + (request.remote_addr or "?"), LISTAS_TOCAR_MAX_POR_10S, 10.0):
        raise ErroLista("Muitos pedidos seguidos. Aguarde um instante.", 429)
    resultado = tocar_lista(lista_id, embaralhar)
    _auditar_listas("lista_tocar", resultado["adicionados"])
    return jsonify({"ok": True, **resultado})


# ---------------------------------------------------------------------------
# Atalhos do Spotify (contrato v2.7)
# "Salvar a playlist do Spotify": nome + uma playlist OU um álbum, que toca com um toque (e entra na
# Programação como o tipo "spotify"). Ficam no MESMO listas.json das listas (chave "spotify", com versão
# própria "spotify_versao" e a versão de CADA atalho para o 409 "versao_mudou"). Guardamos só a URI
# ("spotify:playlist:<22 base62>"): o link que o dono cola nunca é guardado nem buscado (nenhuma requisição
# a partir dele). Tocar = o mesmo PUT /me/player/play com context_uri do /api/spotify/play. Nada daqui vai
# para o log além de contagens.
# ---------------------------------------------------------------------------
MSG_LINK_SPOTIFY = "Cole o link de uma playlist ou álbum do Spotify."
MSG_ATALHO_NAO_EXISTE = "Esse atalho do Spotify não existe mais."
MSG_ATALHO_VERSAO_MUDOU = "Outro aparelho mudou este atalho. Recarregue para ver a versão atual."
ATALHO_TOCAR_MAX_POR_10S = 6        # POST .../tocar, por IP (além do limite geral de comandos do Spotify)
CONTEXTO_SPOTIFY_MAX_POR_10S = 20   # GET /api/spotify/atual-contexto, por IP
CONTEXTO_NOME_TTL_S = 600.0         # o nome de uma playlist/álbum muda raramente
CONTEXTO_NOME_FALHA_TTL_S = 60.0
CONTEXTO_NOME_CACHE_MAX = 32
_RE_LINK_SPOTIFY_CAMINHO = re.compile(
    rf"/(?:(?i:intl-[a-z]{{2,3}}(?:-[a-z]{{2,4}})?)/)?({_SPOTIFY_TIPO})/({_SPOTIFY_ID22})/?")
_ATALHO_CHAVES_CRIAR = frozenset({"nome", "link"})
_ATALHO_CHAVES_SALVAR = frozenset({"versao", "nome", "link"})


class ErroTocarSpotify(Exception):
    """Falha ao tocar um atalho do Spotify: o formato do Spotify v2.4.2 ("error" + "codigo"[, "espera_s"])."""

    def __init__(self, mensagem: str, codigo: str, http: int = 200, espera_s: int | None = None) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.codigo = codigo
        self.http = http
        self.espera_s = espera_s


def _uri_spotify_do_link(valor: Any) -> str:
    """
    "https://open.spotify.com/(intl-xx/)?(playlist|album)/<22 base62>[?si=...]" ou
    "spotify:(playlist|album):<22 base62>" -> a URI ("spotify:playlist:<id>"). Qualquer outra coisa = 400
    (campo "link"). Só lê o texto: não abre o link, não segue redirecionamento, não busca nada.
    """
    erro = ErroLista(MSG_LINK_SPOTIFY, campo="link")
    if not isinstance(valor, str):
        raise erro
    texto = valor.strip()
    if not 1 <= len(texto) <= 300 or any(ord(c) < 33 or ord(c) == 127 for c in texto):
        raise erro
    m = RE_URI_SPOTIFY_ATALHO.fullmatch(texto)
    if m is None:
        try:
            partes = urlsplit(texto)
            porta = partes.port
        except ValueError:
            raise erro from None
        if (partes.scheme != "https" or partes.hostname != "open.spotify.com" or partes.username is not None
                or partes.password is not None or porta not in (None, 443)):
            raise erro
        m = _RE_LINK_SPOTIFY_CAMINHO.fullmatch(partes.path)
        if m is None:
            raise erro
    return _uri_spotify(m.group(1), m.group(2))


def _atalho_publico(atalho: dict) -> dict[str, Any]:
    _, tipo, ident = atalho["uri"].split(":")
    return {"id": atalho["id"], "nome": atalho["nome"], "tipo": tipo, "versao": atalho["versao"],
            "atualizada_em": atalho["atualizada_em"], "url": f"https://open.spotify.com/{tipo}/{ident}"}


def _limites_atalhos() -> dict[str, int]:
    return {"atalhos": ATALHOS_SPOTIFY_MAX, "nome": LISTA_NOME_MAX}


def atalho_spotify_obter(atalho_id: Any) -> dict | None:
    """O atalho (registro do cache, SOMENTE LEITURA) ou None. Para a programação também."""
    if not isinstance(atalho_id, str) or not RE_LISTA_ID.fullmatch(atalho_id):
        return None
    return next((a for a in listas_ler()["spotify"] if a["id"] == atalho_id), None)


def _atalho_da_rota(atalho_id: str) -> str:
    if not RE_LISTA_ID.fullmatch(atalho_id):
        raise ErroLista(MSG_ATALHO_NAO_EXISTE, 404, codigo="atalho_inexistente")
    return atalho_id


def _nome_de_atalho_em_uso(doc: dict, nome: str, exceto: str | None = None) -> bool:
    chave = nome.casefold()
    return any(a["nome"].casefold() == chave and a["id"] != exceto for a in doc["spotify"])


@app.get("/api/spotify/atalhos")
def spotify_atalhos_listar() -> Response:
    doc = listas_ler()
    return jsonify({"ok": True, "versao": doc["spotify_versao"], "limites": _limites_atalhos(),
                    "atalhos": [_atalho_publico(a) for a in doc["spotify"]]})


@app.post("/api/spotify/atalhos")
def spotify_atalhos_criar() -> tuple[Response, int]:
    """Cria {nome, link}. Ordem: 400, 429, 409 ("limite_atalhos" | "nome_repetido")."""
    corpo = corpo_json()
    if set(corpo) - _ATALHO_CHAVES_CRIAR:
        raise ErroLista("Campo desconhecido.", campo="chave")
    nome = _nome_de(corpo.get("nome"), "do atalho")
    uri = _uri_spotify_do_link(corpo.get("link"))
    _limitar_escrita_listas()
    with _listas_arquivo.lock:
        doc = copy.deepcopy(listas_ler())
        if len(doc["spotify"]) >= ATALHOS_SPOTIFY_MAX:
            raise ErroLista(f"Você já tem {ATALHOS_SPOTIFY_MAX} atalhos do Spotify. Apague algum para criar outro.",
                            409, codigo="limite_atalhos")
        if _nome_de_atalho_em_uso(doc, nome):
            raise ErroLista("Já existe um atalho com esse nome.", 409, codigo="nome_repetido", campo="nome")
        ident = programacao.novo_id({a["id"] for a in doc["spotify"]})
        novo = {"id": ident, "nome": nome, "uri": uri, "versao": 1, "atualizada_em": int(time.time())}
        _carimbar(novo, novo=True)
        doc["spotify"].append(novo)
        doc["spotify_versao"] += 1
        _listas_arquivo.gravar(doc)
    _auditar_listas("spotify_atalho_criar", 1)
    return jsonify({"ok": True, "versao": doc["spotify_versao"], "atalho": _atalho_publico(novo)}), 201


@app.post("/api/spotify/atalhos/<atalho_id>")
def spotify_atalhos_salvar(atalho_id: str) -> Response:
    """Muda o nome e/ou o link {versao, nome?, link?}. Ordem: 404 (id mal formado), 400, 429, 404, 409."""
    _atalho_da_rota(atalho_id)
    corpo = corpo_json()
    if set(corpo) - _ATALHO_CHAVES_SALVAR:
        raise ErroLista("Campo desconhecido.", campo="chave")
    versao = _versao_da_lista(corpo)
    if "nome" not in corpo and "link" not in corpo:
        raise ErroLista("Informe o nome e/ou o link.", campo="chave")
    nome = _nome_de(corpo["nome"], "do atalho") if "nome" in corpo else None
    uri = _uri_spotify_do_link(corpo["link"]) if "link" in corpo else None
    _limitar_escrita_listas()
    with _listas_arquivo.lock:
        doc = copy.deepcopy(listas_ler())
        atalho = next((a for a in doc["spotify"] if a["id"] == atalho_id), None)
        if atalho is None:
            raise ErroLista(MSG_ATALHO_NAO_EXISTE, 404, codigo="atalho_inexistente")
        if versao != atalho["versao"]:
            raise ErroLista(MSG_ATALHO_VERSAO_MUDOU, 409, codigo="versao_mudou")
        if nome is not None and _nome_de_atalho_em_uso(doc, nome, exceto=atalho_id):
            raise ErroLista("Já existe um atalho com esse nome.", 409, codigo="nome_repetido", campo="nome")
        mudou_conteudo = (nome is not None and nome != atalho["nome"]) or (uri is not None and uri != atalho["uri"])
        if mudou_conteudo:
            _prog_checar_alvo_remoto("spotify", atalho_id)
        if nome is not None:
            atalho["nome"] = nome
        if uri is not None:
            atalho["uri"] = uri
        atalho["versao"] += 1
        atalho["atualizada_em"] = int(time.time())
        if mudou_conteudo:
            _carimbar(atalho, novo=False)
        doc["spotify_versao"] += 1
        _listas_arquivo.gravar(doc)
    _auditar_listas("spotify_atalho_salvar", 1)
    return jsonify({"ok": True, "versao": doc["spotify_versao"], "atalho": _atalho_publico(atalho)})


@app.post("/api/spotify/atalhos/<atalho_id>/apagar")
def spotify_atalhos_apagar(atalho_id: str) -> Response:
    """Apaga o atalho {versao}. Um horário da Programação que o usava fica com alvo_ok false."""
    _atalho_da_rota(atalho_id)
    corpo = corpo_json()
    if set(corpo) - {"versao"}:
        raise ErroLista("Campo desconhecido.", campo="chave")
    versao = _versao_da_lista(corpo)
    _limitar_escrita_listas()
    with _listas_arquivo.lock:
        doc = copy.deepcopy(listas_ler())
        atalho = next((a for a in doc["spotify"] if a["id"] == atalho_id), None)
        if atalho is None:
            raise ErroLista(MSG_ATALHO_NAO_EXISTE, 404, codigo="atalho_inexistente")
        if versao != atalho["versao"]:
            raise ErroLista(MSG_ATALHO_VERSAO_MUDOU, 409, codigo="versao_mudou")
        doc["spotify"].remove(atalho)
        doc["spotify_versao"] += 1
        _listas_arquivo.gravar(doc)
    _contar_removidos_remoto("atalhos", 1)
    _auditar_listas("spotify_atalho_apagar", 1)
    return jsonify({"ok": True, "versao": doc["spotify_versao"]})


def tocar_atalho_spotify(atalho_id: str) -> dict[str, Any]:
    """
    Toca o atalho no Spotify do PC da loja (o PUT /me/player/play com context_uri do /api/spotify/play).
    Sem contexto de requisição: a rota e a programação chamam esta função. A música do Spotify SUBSTITUI a da
    aba Tocador: se ela estiver tocando, recebe "pause" (só depois de o Spotify aceitar o comando, para um
    erro nunca deixar a loja em silêncio). Devolve {"tocador_pausado": bool}. Levanta ErroTocarSpotify com
    "codigo": atalho_inexistente (404) | sem_dispositivo (400, nenhum aparelho escolhido) | reconectar |
    premium | limite | indisponivel (os cinco últimos como na v2.4.2, HTTP 200 + ok:false na rota).
    """
    atalho = atalho_spotify_obter(atalho_id)
    if atalho is None:
        raise ErroTocarSpotify(MSG_ATALHO_NAO_EXISTE, "atalho_inexistente", 404)
    device_id = spotify_active_device_id()
    if not device_id:
        # sem aparelho escolhido: se nem está conectado, a causa é a conexão (o que o dono precisa fazer)
        if spotify_get_token() is None and _motivo_sem_token(load_config()) != "rede":
            raise ErroTocarSpotify(MSG_SPOTIFY["reconectar"], "reconectar")
        raise ErroTocarSpotify("Nenhum dispositivo Spotify selecionado.", "sem_dispositivo", 400)
    status, reason = spotify_comando(
        "PUT", "/me/player/play", params={"device_id": device_id}, json_body={"context_uri": atalho["uri"]})
    if not _sucesso(status):
        erro = _corpo_erro_spotify(status, reason)
        raise ErroTocarSpotify(erro["error"], erro["codigo"], 200, erro.get("espera_s"))
    pausou = False
    _marcar_musica_substituida()   # antes de checar a aba: se um bloco pausado está terminando, ele não retoma
    vivo, tocando, _ = _estado_do_tocador()
    if vivo and tocando:
        youtube_enqueue("pause")
        with _yt_estado_lock:   # a aba só avisa no próximo sinal dela; o painel não pode ver "tocando" até lá
            _yt_estado["is_playing"] = False
        pausou = True
    return {"tocador_pausado": pausou}


@app.post("/api/spotify/atalhos/<atalho_id>/tocar")
def spotify_atalhos_tocar(atalho_id: str) -> tuple[Response, int] | Response:
    """
    Toca o atalho {}. Falha do Spotify: o formato v2.4.2 ({ok:false, error, codigo[, espera_s]}, HTTP 200; 400 sem
    aparelho escolhido). 404 "atalho_inexistente". 429 (limite de comandos do Spotify / 6 em 10 s).
    """
    _atalho_da_rota(atalho_id)
    if corpo_json():
        raise ErroLista("Campo desconhecido.", campo="chave")
    _limitar_comandos_spotify()
    if excedeu_limite("spotify-atalho-tocar:" + (request.remote_addr or "?"), ATALHO_TOCAR_MAX_POR_10S, 10.0):
        raise ErroApi("Muitos pedidos seguidos. Aguarde um instante.", 429)
    try:
        resultado = tocar_atalho_spotify(atalho_id)
    except ErroTocarSpotify as e:
        _auditar_listas("spotify_atalho_tocar", 0, e.codigo)
        corpo: dict[str, Any] = {"ok": False, "error": e.mensagem, "codigo": e.codigo}
        if e.espera_s is not None:
            corpo["espera_s"] = e.espera_s
        return jsonify(corpo), e.http
    _auditar_listas("spotify_atalho_tocar", 1, "ok")
    return jsonify({"ok": True, **resultado})


# --- "Salvar a playlist que está tocando" ---------------------------------------------------------
_contexto_nomes_lock = threading.Lock()
_contexto_nomes: OrderedDict[str, tuple[str | None, float]] = OrderedDict()   # uri -> (nome|None, expira_em)


def _nome_do_contexto_spotify(tipo: str, ident: str) -> str | None:
    """
    Nome da playlist/álbum que toca, para sugerir ao dono. SÓ api.spotify.com (SPOTIFY_API fixo) com o id já
    validado (22 base62). Qualquer falha = None (o nome é um enfeite; a falha não vira "api_ok" do painel).
    Cache curto por URI.
    """
    chave = f"{tipo}:{ident}"
    agora = time.monotonic()
    with _contexto_nomes_lock:
        achado = _contexto_nomes.get(chave)
        if achado is not None and achado[1] > agora:
            return achado[0]
    caminho = f"/{'playlists' if tipo == 'playlist' else 'albums'}/{ident}"
    params = {"fields": "name"} if tipo == "playlist" else None
    nome: str | None = None
    try:
        dados, status, _ = spotify_api("GET", caminho, params=params, registrar_resultado=False,
                                       rotulo_log="nome do contexto")
        bruto = dados.get("name") if status == 200 else None
        if isinstance(bruto, str):
            limpo = programacao.limpar_exibicao(bruto).strip()[:LISTA_NOME_MAX].strip()
            nome = programacao.texto_limpo(limpo, LISTA_NOME_MAX, multilinha=False)
    except Exception:  # noqa: BLE001 - o nome é opcional
        log.debug("Nome da playlist do Spotify indisponível.")
    with _contexto_nomes_lock:
        _contexto_nomes[chave] = (nome, agora + (CONTEXTO_NOME_TTL_S if nome else CONTEXTO_NOME_FALHA_TTL_S))
        _contexto_nomes.move_to_end(chave)
        while len(_contexto_nomes) > CONTEXTO_NOME_CACHE_MAX:
            _contexto_nomes.popitem(last=False)
    return nome


@app.get("/api/spotify/atual-contexto")
def spotify_atual_contexto() -> Response:
    """
    Se o Spotify está com uma playlist ou um álbum: {ok, uri_tipo: "playlist"|"album", url, nome|null,
    tocando}. Outra coisa (música avulsa, artista, podcast, nada): uri_tipo/url/nome null. Falha do Spotify:
    {ok:false, error, codigo}. Usa o cache do player (~2 s) compartilhado com o now-playing.
    """
    if excedeu_limite("spctx:" + (request.remote_addr or "?"), CONTEXTO_SPOTIFY_MAX_POR_10S, 10.0):
        raise ErroApi("Muitos pedidos seguidos. Aguarde um instante.", 429)
    vazio = {"uri_tipo": None, "url": None, "nome": None, "tocando": False}
    dados = spotify_player_state()
    if dados is None:
        status, reason = _player_estado.get("erro") or (0, None)
        if _em_backoff():
            status = 429
        return jsonify({"ok": False, **vazio, **_corpo_erro_spotify(status, reason)})
    contexto = dados.get("context") if dados else None
    uri = contexto.get("uri") if isinstance(contexto, dict) else None
    achado = RE_URI_SPOTIFY_ATALHO.fullmatch(uri) if isinstance(uri, str) else None
    if achado is None:
        return jsonify({"ok": True, **vazio})
    tipo, ident = achado.group(1).lower(), achado.group(2)
    return jsonify({"ok": True, "uri_tipo": tipo, "url": f"https://open.spotify.com/{tipo}/{ident}",
                    "nome": _nome_do_contexto_spotify(tipo, ident), "tocando": bool(dados.get("is_playing"))})


# ---------------------------------------------------------------------------
# Programação de horários (contrato v2.6, etapa 2)
# Toca no horário marcado: um áudio da pasta de músicas, uma lista ou um anúncio de texto.
# O relógio, a validação, o dedupe e o histórico moram em programacao.py (sem áudio nem Flask); aqui
# ficam o armazenamento, as rotas e a EXECUÇÃO, que reaproveita o caminho dos anúncios:
#   * áudio e anúncio usam a trava `_anuncio_lock` (um som por vez; um anúncio manual recebe 409
#     "Programação tocando: <nome>") e a thread de áudio única (executar_no_audio), pela saída fixada
#     em "Som da loja" quando houver; funcionam com o navegador fechado;
#   * no horário a música que estiver tocando (aba Tocador e Spotify) é PAUSADA e, no `finally`,
#     retomada de onde parou, SÓ o que estava tocando e só se continua a mesma faixa;
#   * lista = tocar_lista() (troca a música; precisa da aba Tocador aberta).
# Dados em programacao.json ao lado do config.json (mesmo motivo das listas: o config guarda segredos
# e é regravado o tempo todo). Nada de nome, texto ou id vai para o log ou para a auditoria.
# ---------------------------------------------------------------------------
PROGRAMACAO_ARQUIVO = "programacao.json"
PROGRAMACAO_VISTO_ARQUIVO = "programacao.visto.json"
PROG_CORPO_MAX = 60 * 1024               # 100 itens x ate 500 caracteres acentuados passam dos 16 KB globais
PROG_ESCRITA_MAX_POR_MIN = 20            # POST /api/programacao, por IP
PROG_TESTAR_MAX_POR_MIN = 6              # POST .../testar, por IP
PROG_PARAR_MAX_POR_MIN = 20              # POST .../parar, por IP
PROG_AUDIO_TETO_S = 600.0                # teto do ÁUDIO agendado (10 min); os anúncios continuam em 80 s
PROG_AUDIO_MARGEM_S = 20.0               # folga do executor de áudio sobre o teto do laço de reprodução
PROG_AUDIO_FOLGA_PROP = 1.15             # teto do laço = duração dos metadados x 1,15 + 10 s (mp3 VBR sem Xing)
PROG_AUDIO_FOLGA_S = 10.0
PROG_AUDIO_DIA_MAX_S = 3600.0            # soma dos áudios agendados ATIVOS por dia da semana (60 min)
PROG_FORMATOS_AUDIO = frozenset({"mp3", "ogg", "wav", "flac"})   # os que o pygame toca; m4a/aac/opus não
PROG_PAUSA_FOLGA_S = 0.4                 # dá tempo de a aba Tocador pausar antes de o som começar
PROG_RETOMAR_TENTATIVAS = 3              # play do Spotify no fim (a API às vezes falha uma vez)
PROG_RETOMAR_ESPERA_S = 1.5
PROG_ESPERA_TOCADOR_S = 15.0             # lista agendada: quanto esperar a aba Tocador reconectar (religou há pouco)
PROG_ESPERA_ACERVO_S = 10.0              # logo depois de ligar: quanto esperar a 1ª leitura da pasta de músicas
MSG_PROG_NAO_EXISTE = "Esse horário não existe mais. Recarregue a programação."
MSG_PROG_VERSAO_MUDOU = "Outro aparelho mudou a programação. Recarregue para ver a versão atual."
MSG_SPOTIFY_NAO_PAUSOU = "A música do Spotify não pôde ser pausada durante a programação."
MSG_SPOTIFY_NAO_VOLTOU = "O Spotify não voltou a tocar sozinho; toque em Play no painel."
MSG_TOCADOR_SUMIU = "O Tocador da loja fechou durante a programação; a música não foi retomada."
_prog_dormir = time.sleep                # os testes trocam por um relógio de mentira


class ErroProg(ErroMensagem):
    """Erro de /api/programacao: o mesmo formato do /api/mensagens ({ok, error, codigo?, campo?, indice?})."""


@app.errorhandler(programacao.ErroValidacao)
def _tratar_erro_validacao_prog(e: programacao.ErroValidacao) -> tuple[Response, int]:
    corpo: dict[str, Any] = {"ok": False, "error": e.mensagem}
    for chave, valor in (("codigo", e.codigo), ("campo", e.campo), ("indice", e.indice)):
        if valor is not None:
            corpo[chave] = valor
    return jsonify(corpo), e.status


def _prog_path() -> Path:
    """programacao.json na pasta do config.json (os testes trocam CONFIG_PATH e levam este junto)."""
    return CONFIG_PATH.with_name(PROGRAMACAO_ARQUIVO)


def _prog_visto_path() -> Path:
    return CONFIG_PATH.with_name(PROGRAMACAO_VISTO_ARQUIVO)


_prog_arquivo = programacao.ArquivoJson(
    _prog_path, lambda: {"versao": 0, "itens": []}, programacao.sanear_documento,
    lambda caminho: _restringir_acl_windows(caminho))


def _prog_nome(item: dict) -> str:
    """Texto que identifica o item para o dono: o nome dele, senão o título/nome do alvo, senão a hora."""
    if item.get("nome"):
        return str(item["nome"])
    if item.get("tipo") == "audio":
        m = ACERVO.obter(item.get("alvo"))
        return m.titulo if m is not None else f"Áudio das {item.get('hora')}"
    if item.get("tipo") == "lista":
        lista = lista_obter(item.get("alvo"))
        return lista["nome"] if lista is not None else f"Lista das {item.get('hora')}"
    if item.get("tipo") == "spotify":
        atalho = atalho_spotify_obter(item.get("alvo"))
        return atalho["nome"] if atalho is not None else f"Spotify das {item.get('hora')}"
    return f"Anúncio das {item.get('hora')}"


def _prog_auditar_execucao(registro: dict) -> None:
    """Auditoria de cada execução: só tipo, origem e resultado (nunca nome, texto ou ids)."""
    auditoria.info("acao=programacao_executar origem=%s tipo=%s status=%s motivo=%s",
                   registro["origem"], registro["tipo"], registro["status"], registro["motivo"] or "-")


def _msg_ocupado(padrao: str, sufixo: str = "") -> str:
    """Mensagem do 409 de quem não conseguiu a trava de áudio: diz qual programação está tocando."""
    t = PROGRAMACAO.tocando
    return f"Programação tocando: {t['nome']}{sufixo}" if t else padrao


# --- pausar e retomar a música --------------------------------------------------------------------
def _estado_do_tocador() -> tuple[bool, bool, tuple]:
    """(aba Tocador viva, está tocando, identidade da faixa = (source, video_id, music_id))."""
    with _yt_estado_lock:
        beat = _yt_estado["last_heartbeat"]
        vivo = beat is not None and (time.monotonic() - beat) < YOUTUBE_PLAYER_TIMEOUT
        return vivo, bool(_yt_estado["is_playing"]), (
            _yt_estado["source"], _yt_estado["video_id"], _yt_estado["music_id"])


# Geração das "substituições de música": tocar_lista() e tocar_atalho_spotify() TROCAM o que toca. Um bloco
# _musica_pausada (áudio/anúncio agendado) que pausou a música antes disso não pode devolvê-la no `finally`
# (a aba Tocador voltaria a tocar junto com o Spotify; o Spotify antigo sobreporia a lista nova).
_substituicao_lock = threading.Lock()
_substituicao_gen = 0


def _marcar_musica_substituida() -> None:
    global _substituicao_gen
    with _substituicao_lock:
        _substituicao_gen += 1


def _geracao_da_musica() -> int:
    with _substituicao_lock:
        return _substituicao_gen


def _retomar_musica(faixa_tocador: tuple | None, device_spotify: str | None, avisos: list[str]) -> None:
    """Devolve a música que NÓS pausamos. Nunca levanta: o `finally` do áudio ainda precisa soltar a trava."""
    if faixa_tocador is not None:
        try:
            vivo, _, faixa = _estado_do_tocador()
            if not vivo:
                avisos.append(MSG_TOCADOR_SUMIU)
            elif faixa == faixa_tocador:   # outra faixa carregada nesse meio-tempo: não mexe
                youtube_enqueue("play")
                # A aba só avisa "estou tocando" no próximo sinal dela. Sem isto, o PRÓXIMO horário da fila
                # (ex.: dois itens no mesmo minuto) ainda veria "pausado" e não pausaria a música.
                with _yt_estado_lock:
                    _yt_estado["is_playing"] = True
        except Exception:  # noqa: BLE001
            log.warning("Não foi possível retomar o Tocador da loja.", exc_info=True)
    if device_spotify is not None:
        voltou = False
        for tentativa in range(PROG_RETOMAR_TENTATIVAS):
            try:
                status, _ = spotify_comando("PUT", "/me/player/play", params={"device_id": device_spotify},
                                            json_body={}, critico=True)
                if _sucesso(status):
                    voltou = True
                else:   # "restriction violated" quando já está tocando: confere no player
                    dados = spotify_player_state(force=True)
                    voltou = bool(dados and dados.get("is_playing"))
            except Exception:  # noqa: BLE001
                log.warning("Não foi possível retomar o Spotify.", exc_info=True)
            if voltou:
                break
            if tentativa < PROG_RETOMAR_TENTATIVAS - 1:
                _prog_dormir(PROG_RETOMAR_ESPERA_S)
        if not voltou:
            avisos.append(MSG_SPOTIFY_NAO_VOLTOU)


def _pausar_spotify_se_tocando(avisos: list[str]) -> str | None:
    """
    Pausa o Spotify SÓ se estiver tocando. Devolve o device pausado (para quem for retomar) ou None
    (não tocava, sem Spotify ou a pausa falhou: `avisos` recebe o motivo). Nunca levanta.
    """
    try:
        device = spotify_active_device_id()
        if not device:
            return None
        dados = spotify_player_state(force=True)
        if dados and dados.get("is_playing"):
            status, _ = spotify_comando("PUT", "/me/player/pause", params={"device_id": device},
                                        critico=True)
            if _sucesso(status):
                return device
            avisos.append(MSG_SPOTIFY_NAO_PAUSOU)
    except Exception:  # noqa: BLE001
        log.warning("Não foi possível pausar o Spotify.", exc_info=True)
        avisos.append(MSG_SPOTIFY_NAO_PAUSOU)
    return None


@contextmanager
def _musica_pausada(avisos: list[str] | None = None) -> Iterator[None]:
    """
    Pausa a música que estiver TOCANDO (aba Tocador e/ou Spotify) durante o bloco e, no `finally`,
    retoma SÓ o que este bloco pausou. Quem não estava tocando não é tocado. `avisos` recebe os
    textos do que deu errado (o histórico mostra).
    """
    avisos = avisos if avisos is not None else []
    faixa_tocador: tuple | None = None
    device_spotify: str | None = None
    geracao = _geracao_da_musica()
    try:
        vivo, tocando, faixa = _estado_do_tocador()
        if vivo and tocando:
            youtube_enqueue("pause")
            faixa_tocador = faixa
        device_spotify = _pausar_spotify_se_tocando(avisos)
        if faixa_tocador is not None or device_spotify is not None:
            _prog_dormir(PROG_PAUSA_FOLGA_S)
        yield
    finally:
        if _geracao_da_musica() == geracao:
            _retomar_musica(faixa_tocador, device_spotify, avisos)
        # senão: uma lista ou uma playlist do Spotify tomou o lugar da música durante o bloco; não há o que devolver


# --- execução ---------------------------------------------------------------------------------------
def _prog_aguardar_trava(ctx: programacao.Execucao) -> None:
    """
    Pega a trava de áudio. Com um anúncio manual tocando, tenta de novo a cada ~1 s até o prazo do
    pedido (60 s depois do horário); passou disso, "não tocou". O desligamento corta a espera.
    """
    while True:
        if _desligamento_agendado or _desligando.is_set():
            raise programacao.NaoTocou("desligando", "O Proclamare estava sendo desligado.")
        if _anuncio_lock.acquire(blocking=False):
            return
        restante = ctx.prazo_mono - ctx.monotonic()
        if restante <= 0:
            raise programacao.NaoTocou("ocupado", "Havia um anúncio tocando e não deu para esperar mais.")
        if ctx.esperar(min(1.0, restante)):
            raise programacao.NaoTocou("desligando", "O Proclamare estava sendo desligado.")


def _prog_aguardar_tocador(ctx: programacao.Execucao) -> bool:
    """
    Logo depois de ligar o Proclamare, a aba Tocador (que já estava aberta) leva alguns segundos para
    reconectar. Espera até PROG_ESPERA_TOCADOR_S (e nunca além do prazo do pedido) por um sinal dela.
    """
    fim = min(ctx.monotonic() + PROG_ESPERA_TOCADOR_S, ctx.prazo_mono)
    while not _tocador_vivo():
        if ctx.monotonic() >= fim or ctx.esperar(1.0):
            return False
    return True


def _prog_executar_lista(item: dict, ctx: programacao.Execucao) -> dict:
    """
    Lista agendada = o "Tocar lista" (TROCA a música). Espera a 1ª leitura da pasta (recém-ligado) SÓ se a lista
    tem música da pasta (lista só com YouTube, ou mista, v2.7, não espera por ela quando não precisa) e a
    aba Tocador reconectar; sem Tocador vivo: "não tocou". Se o Spotify estiver tocando ele é PAUSADO
    e NÃO volta (a lista substitui a música); se a lista não chegar a tocar, o Spotify é devolvido.
    """
    lista = lista_obter(item["alvo"])
    if lista is None or any(e["tipo"] == "musica" for e in lista["musicas"]):
        _prog_aguardar_acervo(ctx)   # lista só com YouTube (v2.7) não depende da pasta de músicas
    if not _tocador_vivo() and not _prog_aguardar_tocador(ctx):
        raise programacao.NaoTocou("tocador_fechado", "Tocador da loja fechado")
    avisos: list[str] = []
    device_spotify = _pausar_spotify_se_tocando(avisos)
    tocou = False
    try:
        resultado = tocar_lista(item["alvo"], bool(item.get("embaralhar")))
        tocou = True
    except ErroLista as e:
        if e.codigo == "sem_tocador":
            raise programacao.NaoTocou("tocador_fechado", "Tocador da loja fechado") from None
        if e.codigo == "lendo_pasta":
            raise programacao.NaoTocou("lendo_pasta", "O Proclamare ainda estava lendo a pasta de músicas.") from None
        if e.codigo == "sem_musicas":
            raise programacao.NaoTocou("lista_sem_musicas", "Nada da lista pode tocar agora.") from None
        if e.codigo == "youtube_indisponivel":
            raise programacao.NaoTocou("youtube_indisponivel",
                                       "O player do YouTube do Tocador da loja não carregou.") from None
        if e.status == 404:
            raise programacao.NaoTocou("lista_inexistente", "A lista não existe mais.") from None
        raise
    finally:
        if not tocou and device_spotify is not None:   # a lista não tocou: a música que parou volta
            _retomar_musica(None, device_spotify, [])
    if resultado["cortados"]:
        avisos.append(f"A fila do Tocador comporta {YOUTUBE_FILA_MAX} músicas; as {resultado['cortados']} "
                      "últimas da lista não entraram.")
    if resultado.get("pulados_youtube"):
        avisos.append(f"O player do YouTube do Tocador não estava pronto: {resultado['pulados_youtube']} item(ns) "
                      "do YouTube da lista ficaram de fora.")
    return {"aviso": " ".join(avisos) or None}


# Motivos estáveis (histórico) das falhas do Spotify; o painel mapeia por eles.
_MOTIVO_SPOTIFY = {"reconectar": "spotify_desconectado", "sem_dispositivo": "spotify_sem_aparelho",
                   "premium": "spotify_premium", "limite": "spotify_limite",
                   "indisponivel": "spotify_indisponivel", "atalho_inexistente": "atalho_inexistente"}


def _prog_executar_spotify(item: dict, ctx: programacao.Execucao) -> dict:
    """
    Playlist/álbum do Spotify agendado = o "Tocar" do atalho (tocar_atalho_spotify): TROCA a música. Se a aba
    Tocador estava tocando, ela é pausada (a música do Spotify a substitui; nada é retomado depois). Falhou =
    "não tocou" com o motivo: spotify_desconectado | spotify_sem_aparelho | spotify_premium | spotify_limite |
    spotify_indisponivel | atalho_inexistente. Não usa a trava de áudio (o som sai pelo Spotify do PC, não
    pelo caminho dos anúncios) e não conta no teto diário de áudio.
    """
    try:
        tocar_atalho_spotify(item["alvo"])   # {"tocador_pausado": bool}: nada a avisar (o Spotify substitui a aba)
    except ErroTocarSpotify as e:
        raise programacao.NaoTocou(_MOTIVO_SPOTIFY.get(e.codigo, "spotify_indisponivel"), e.mensagem) from None
    return {"aviso": None}


def _prog_aguardar_acervo(ctx: programacao.Execucao) -> None:
    """Logo depois de ligar, a 1ª leitura da pasta de músicas pode demorar: espera até 10 s por ela."""
    fim = ctx.monotonic() + PROG_ESPERA_ACERVO_S
    while not ACERVO.pronto:
        if ctx.monotonic() >= fim or ctx.esperar(0.5):
            raise programacao.NaoTocou("lendo_pasta", MSG_LENDO_PASTA)


def _prog_musica_do_item(item: dict) -> "tuple[musicas.Musica, Path]":
    """
    O arquivo do item de áudio, conferido AGORA: existe, formato que o pygame toca, até 10 min e
    (como o streaming) ainda dentro da pasta, arquivo comum, sem link/reparse. Devolve (música, caminho
    real): quem toca usa o caminho real, nunca o registrado na última varredura.
    """
    m = ACERVO.obter(item["alvo"])
    if m is None:
        if not ACERVO.pronto:   # recém-ligado: "não achei" ainda não quer dizer "sumiu"
            raise programacao.NaoTocou("lendo_pasta", MSG_LENDO_PASTA)
        raise programacao.NaoTocou("sem_musica", "A música não está mais na pasta.")
    if m.formato not in PROG_FORMATOS_AUDIO:
        raise programacao.NaoTocou("formato", f"O formato .{m.formato} não toca em horário marcado.")
    if m.duracao_s is not None and m.duracao_s > PROG_AUDIO_TETO_S:
        raise programacao.NaoTocou("audio_longo", "O áudio passa de 10 minutos.")
    real = ACERVO.caminho_seguro(m)
    if real is None:   # trocado por link/arquivo de fora da pasta depois da varredura (TOCTOU)
        raise programacao.NaoTocou("sem_musica", "A música não está mais na pasta.")
    return m, real


def _prog_teto_audio(m: "musicas.Musica") -> float:
    """
    Teto do laço de reprodução do áudio agendado. A duração dos metadados pode ficar abaixo da real (mp3
    VBR sem cabeçalho Xing): folga de 15% + 10 s, sempre até 10 min. Sem duração: 10 min (o laço sai
    quando a música acaba).
    """
    if m.duracao_s is None:
        return PROG_AUDIO_TETO_S
    return min(PROG_AUDIO_TETO_S, float(m.duracao_s) * PROG_AUDIO_FOLGA_PROP + PROG_AUDIO_FOLGA_S)


def _prog_tocar_no_motor(caminho: Path, duracao: float | None, sinal: Any) -> bool:
    """(Thread de áudio) Toca o arquivo da biblioteca pelo motor da saída fixada; teto próprio de 10 min."""
    motor = SOM.motor
    prazo = motor.agora() + PROG_AUDIO_TETO_S + PROG_AUDIO_MARGEM_S - 5.0   # antes do teto do executor
    motor.tocar(caminho, prazo=prazo, duracao=duracao, parar=sinal, teto_s=PROG_AUDIO_TETO_S,
                folga_proporcional=PROG_AUDIO_FOLGA_PROP - 1.0, margem_s=PROG_AUDIO_FOLGA_S)
    return sinal.is_set()


def _prog_tocar_arquivo(m: "musicas.Musica", real: Path, ctx: programacao.Execucao,
                        som: dict | None) -> dict:
    """
    Áudio agendado: abre a saída ANTES de pausar a música, pausa, toca `real` (o caminho conferido; pelo
    motor da saída fixada em "Som da loja" ou pelo mixer padrão), retoma. O arquivo da pasta NUNCA é apagado.
    """
    avisos: list[str] = []
    teto = _prog_teto_audio(m)
    espera_max = teto + PROG_AUDIO_MARGEM_S      # o laço de reprodução sempre sai antes do executor
    try:
        if som is not None:
            _abrir_saida_som(SOM.candidatas_anuncio(som))
        with _musica_pausada(avisos):
            if som is not None:
                cortado = executar_no_audio(_prog_tocar_no_motor, real, m.duracao_s, ctx.sinal,
                                            timeout=espera_max)
            else:
                cortado = executar_no_audio(_reproduzir_legado, real, teto, ctx.sinal, timeout=espera_max)
    except ErroSom as e:
        raise programacao.NaoTocou(e.codigo or "sem_saida", e.mensagem, "erro") from None
    except som_pkg.SaidaIndisponivel as e:
        raise programacao.NaoTocou(e.codigo, e.mensagem, "erro") from None
    except (som_pkg.ErroReproducao, TimeoutError) as e:
        raise programacao.NaoTocou("falha_audio", str(e) or "A reprodução travou.", "erro") from None
    except Exception as e:  # noqa: BLE001 - ex.: pygame.error "No file '<caminho>'": a mensagem traz o CAMINHO
        log.error("Programação: falha ao tocar o áudio (%s).", type(e).__name__)
        raise programacao.NaoTocou("falha_audio", "Não foi possível tocar o áudio.", "erro") from None
    return {"interrompido": bool(cortado), "aviso": " ".join(avisos) or None}


def _prog_falar(item: dict, ctx: programacao.Execucao, som: dict | None) -> dict:
    """Anúncio de texto agendado: o fluxo de anúncio de sempre, PAUSANDO (não abaixando) a música."""
    modo = item["motor"]
    internet_ok = tem_internet()
    if modo == "online" and not internet_ok:
        raise programacao.NaoTocou("sem_internet", "Sem internet para o motor online.")
    usar_online = internet_ok and modo != "offline"
    try:
        vozes = obter_vozes_offline()
    except Exception:  # noqa: BLE001
        vozes = None
    voz_offline = vozes["default"] if vozes else None   # pt-BR quando houver
    avisos: list[str] = []
    contexto = functools.partial(_musica_pausada, avisos)
    try:
        if som is not None:
            _, aviso, extra = _anunciar_na_saida(
                item["alvo"], modo, VOZES_ONLINE[item["voz"]], item["velocidade"], voz_offline,
                usar_online, som, contexto=contexto, parar=ctx.sinal)
            if extra.get("aviso"):
                avisos.append(extra["aviso"])
        else:
            _, aviso = _anunciar(item["alvo"], modo, VOZES_ONLINE[item["voz"]], item["velocidade"],
                                 voz_offline, usar_online, contexto=contexto, parar=ctx.sinal)
    except ErroSom as e:
        raise programacao.NaoTocou(e.codigo or "sem_saida", e.mensagem, "erro") from None
    except (som_pkg.ErroReproducao, TimeoutError) as e:
        raise programacao.NaoTocou("falha_audio", str(e) or "A reprodução travou.", "erro") from None
    if aviso:
        avisos.append(aviso)
    return {"interrompido": ctx.sinal.is_set(), "aviso": " ".join(avisos) or None}


def _prog_executar_som(item: dict, ctx: programacao.Execucao) -> dict:
    """Áudio ou anúncio: com a trava de áudio (a da requisição, no "Tocar agora", ou esperando por ela)."""
    tem_trava = ctx.lock_tomado
    try:
        if not tem_trava:
            _prog_aguardar_trava(ctx)
            tem_trava = True
        if _desligamento_agendado or _desligando.is_set():
            raise programacao.NaoTocou("desligando", "O Proclamare estava sendo desligado.")
        if item["tipo"] == "audio":
            _prog_aguardar_acervo(ctx)
        musica = _prog_musica_do_item(item) if item["tipo"] == "audio" else None   # (música, caminho real)
        if not SOM.motor_ok:   # contaminado: o pygame ficou preso numa thread órfã
            raise programacao.NaoTocou("motor_travado", "O áudio do Proclamare travou. Reinicie o Proclamare.",
                                       "erro")
        som = SOM.config_som()
        if som is not None and som["saida_anuncio"] is None:
            som = None   # sem saída fixada: o fluxo é o de sempre (mixer padrão do Windows)
        ctx.comecou()
        log.info("Programação: tocando um item (tipo=%s, origem=%s).", item["tipo"], ctx.origem)
        if musica is not None:
            return _prog_tocar_arquivo(musica[0], musica[1], ctx, som)
        return _prog_falar(item, ctx, som)
    finally:
        if tem_trava:
            _anuncio_lock.release()


def _prog_executar(item: dict, ctx: programacao.Execucao) -> dict | None:
    """O que o agendador chama (thread própria): despacha por tipo. Levanta NaoTocou ou Exception."""
    if item["tipo"] in ("lista", "spotify"):
        if _desligamento_agendado or _desligando.is_set():
            raise programacao.NaoTocou("desligando", "O Proclamare estava sendo desligado.")
        return _prog_executar_lista(item, ctx) if item["tipo"] == "lista" else _prog_executar_spotify(item, ctx)
    return _prog_executar_som(item, ctx)


def _prog_criar_servico(**ajustes: Any) -> programacao.Programacao:
    """O agendador ligado ao armazenamento e à execução deste servidor (os testes passam relógios falsos)."""
    padrao: dict[str, Any] = dict(
        itens=lambda: _prog_arquivo.ler()["itens"],
        executar=_prog_executar,
        nome_do_item=_prog_nome,
        ler_visto=lambda: programacao.ler_visto_arquivo(_prog_visto_path()),
        gravar_visto=lambda quando: programacao.gravar_visto_arquivo(_prog_visto_path(), quando),
        auditar=_prog_auditar_execucao,
    )
    padrao.update(ajustes)
    return programacao.Programacao(**padrao)


PROGRAMACAO = _prog_criar_servico()   # as threads só ligam no main() (PROGRAMACAO.iniciar())


# --- rotas --------------------------------------------------------------------------------------------
def _prog_limites() -> dict[str, Any]:
    return {"itens": programacao.ITENS_MAX, "nome": programacao.NOME_MAX, "texto": programacao.TEXTO_MAX,
            "texto_total": programacao.TEXTO_TOTAL_MAX, "historico": programacao.HISTORICO_MAX,
            "audio_max_s": int(PROG_AUDIO_TETO_S), "audio_dia_max_s": int(PROG_AUDIO_DIA_MAX_S),
            "formatos_audio": sorted(PROG_FORMATOS_AUDIO)}


def _prog_alvo_info(item: dict) -> tuple[str | None, bool | None, str | None]:
    """
    (nome do alvo, alvo_ok, alvo_motivo). ok=None enquanto a pasta de músicas não foi lida. `motivo`
    (só quando ok é False): "sem_musica" | "formato" | "audio_longo" | "lista_inexistente" | "atalho_inexistente".
    """
    if item["tipo"] == "audio":
        m = ACERVO.obter(item["alvo"])
        if m is None:
            return None, (False if ACERVO.pronto else None), ("sem_musica" if ACERVO.pronto else None)
        if m.formato not in PROG_FORMATOS_AUDIO:
            return m.titulo, False, "formato"
        if m.duracao_s is not None and m.duracao_s > PROG_AUDIO_TETO_S:
            return m.titulo, False, "audio_longo"   # o arquivo cresceu depois de agendado
        return m.titulo, True, None
    if item["tipo"] == "lista":
        lista = lista_obter(item["alvo"])
        return (lista["nome"], True, None) if lista is not None else (None, False, "lista_inexistente")
    if item["tipo"] == "spotify":
        atalho = atalho_spotify_obter(item["alvo"])
        return (atalho["nome"], True, None) if atalho is not None else (None, False, "atalho_inexistente")
    return None, True, None


def _prog_item_publico(item: dict, agora: datetime) -> dict[str, Any]:
    alvo_nome, alvo_ok, alvo_motivo = _prog_alvo_info(item)
    proxima = programacao.proxima_ocorrencia(item, agora) if item["ativo"] else None
    return {**item, "rotulo": _prog_nome(item), "alvo_nome": alvo_nome, "alvo_ok": alvo_ok,
            "alvo_motivo": alvo_motivo,
            "proxima": proxima.isoformat(timespec="seconds") if proxima else None}


def _prog_agora_json(agora: datetime) -> dict[str, Any]:
    try:
        deslocamento = agora.astimezone().strftime("%z")
        fuso = f"{deslocamento[:3]}:{deslocamento[3:]}" if len(deslocamento) == 5 else None
    except (ValueError, OSError, OverflowError):
        fuso = None
    return {"iso": agora.isoformat(timespec="seconds"), "data": agora.date().isoformat(),
            "hora": agora.strftime("%H:%M"), "dia_semana": programacao.dia_da_semana(agora.date()),
            "fuso": fuso}


def _prog_resposta() -> Response:
    """Formato único do GET e do POST: {ok, versao, agora, limites, itens, proximas, historico, tocando}."""
    doc = _prog_arquivo.ler()
    agora = PROGRAMACAO.agora()
    itens = [_prog_item_publico(i, agora) for i in doc["itens"]]
    por_id = {i["id"]: i for i in itens}
    proximas = [{**p, "tipo": por_id[p["id"]]["tipo"], "rotulo": por_id[p["id"]]["rotulo"],
                 "hora": por_id[p["id"]]["hora"]}
                for p in PROGRAMACAO.proximas(agora) if p["id"] in por_id]
    return jsonify({"ok": True, "versao": doc["versao"], "agora": _prog_agora_json(agora),
                    "limites": _prog_limites(), "acervo_pronto": ACERVO.pronto,
                    "tocador_vivo": _tocador_vivo(), "itens": itens, "proximas": proximas,
                    "historico": PROGRAMACAO.historico(), "tocando": PROGRAMACAO.tocando})


def _prog_checar_alvo(tipo: str, alvo: str, ja_existia: bool, indice: int) -> None:
    """Parte da validação que só o servidor sabe: a música/lista existem; o áudio é de um formato e tamanho que tocam."""
    n = indice + 1
    if tipo == "audio":
        if ja_existia:
            # Re-salvar a lista inteira não pode falhar por um item que NÃO mudou: música que sumiu,
            # trocada por um formato que não toca ou que ficou longa demais. O aviso fica na etiqueta
            # do item (alvo_ok) e no histórico ("não tocou"), que é onde o dono olha.
            return
        m = ACERVO.obter(alvo)
        if m is None:
            if not ACERVO.pronto:
                raise programacao.ErroValidacao(MSG_LENDO_PASTA, 409, campo="alvo", indice=indice,
                                                codigo="lendo_pasta")
            raise programacao.ErroValidacao(f"Horário {n}: a música não está mais na pasta.",
                                            campo="alvo", indice=indice, codigo="musica_inexistente")
        if m.formato not in PROG_FORMATOS_AUDIO:
            raise programacao.ErroValidacao(
                f"Horário {n}: arquivos .{m.formato} não tocam em horário marcado. Use mp3, ogg, wav ou "
                "flac (converta o arquivo).", campo="alvo", indice=indice, codigo="formato_nao_suportado")
        if m.duracao_s is not None and m.duracao_s > PROG_AUDIO_TETO_S:
            raise programacao.ErroValidacao(
                f"Horário {n}: o áudio passa de {int(PROG_AUDIO_TETO_S // 60)} minutos.",
                campo="alvo", indice=indice, codigo="audio_longo")
    elif tipo == "lista" and not ja_existia and lista_obter(alvo) is None:
        raise programacao.ErroValidacao(f"Horário {n}: essa lista não existe.", campo="alvo", indice=indice,
                                        codigo="lista_inexistente")
    elif tipo == "spotify" and not ja_existia and atalho_spotify_obter(alvo) is None:
        raise programacao.ErroValidacao(f"Horário {n}: essa playlist do Spotify não existe.", campo="alvo",
                                        indice=indice, codigo="atalho_inexistente")


_NOMES_DIAS = ("domingo", "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado")


PROG_FALA_MAX_S = 80.0                   # estimativa de fala por anúncio: len/7 s, no máximo isto (= teto do laço de 80 s)
PROG_FALA_CARACTERES_POR_S = 7.0
PROG_EVENTOS_DIA_MAX = 72                # horários ATIVOS no mesmo dia da semana (interrupções da música): 1 a cada 10 min por 12 h
PROG_REMOTO_LISTAS_DIA_MAX = 10          # listas/playlists do Spotify ATIVAS criadas/alteradas por aparelhos remotos por dia


def _prog_dias_do_item(item: dict) -> list[int]:
    return list(item.get("dias") or [programacao.dia_da_semana(date.fromisoformat(item["data"]))])


def _prog_por_dia(itens: list[dict], valor: Callable[[dict], float]) -> list[float]:
    """Soma `valor(item)` dos itens ATIVOS por dia da semana (0 = domingo). Data única conta no dia da semana dela."""
    por_dia = [0.0] * 7
    for item in itens:
        if not item.get("ativo"):
            continue
        v = valor(item)
        if v <= 0:
            continue
        for dia in _prog_dias_do_item(item):
            por_dia[dia] += v
    return por_dia


def _prog_segundos_audio(item: dict) -> float:
    """Áudio agendado: a duração (sem duração conhecida conta o teto de 10 min; música que sumiu não toca = 0)."""
    if item["tipo"] != "audio":
        return 0.0
    m = ACERVO.obter(item["alvo"])
    if m is None:
        return 0.0
    return float(m.duracao_s) if m.duracao_s is not None else PROG_AUDIO_TETO_S


def _prog_segundos_fala(item: dict) -> float:
    """Anúncio agendado: estimativa conservadora de fala (len/7 s, no máximo 80 s)."""
    if item["tipo"] != "anuncio":
        return 0.0
    return min(PROG_FALA_MAX_S, len(item["alvo"]) / PROG_FALA_CARACTERES_POR_S)


def _prog_audio_por_dia(itens: list[dict]) -> list[float]:
    """
    Segundos de ÁUDIO agendado ATIVO por dia da semana (0 = domingo). Item de data única conta no dia da
    semana dessa data; áudio sem duração conhecida conta o teto de 10 min; música que sumiu não toca (0).
    """
    return _prog_por_dia(itens, _prog_segundos_audio)


def _prog_som_por_dia(itens: list[dict]) -> list[float]:
    """v2.7.1 (P14): áudio + estimativa de fala dos anúncios, por dia da semana. É o que o teto de 60 min limita."""
    return _prog_por_dia(itens, lambda i: _prog_segundos_audio(i) + _prog_segundos_fala(i))


def _prog_conteudo(item: dict, com_ativo: bool) -> dict:
    """O que o item É (sem os carimbos do servidor e, se `com_ativo` for falso, sem ligado/desligado)."""
    return {k: v for k, v in item.items() if k not in programacao.CHAVES_SO_SERVIDOR and (com_ativo or k != "ativo")}


def _prog_mexeu(item: dict, antigo: dict | None, origem: str) -> bool:
    """
    A requisição `origem` mexeu neste item? Item novo ou de conteúdo diferente: sim. Ligar/desligar: para um
    aparelho REMOTO conta (reativar um horário que o dono desligou é mexer); para o PC da loja não (o dono
    desligar um horário não apaga a marca de que ele veio de um celular).
    """
    if antigo is None:
        return True
    return _prog_conteudo(antigo, origem == "remoto") != _prog_conteudo(item, origem == "remoto")


def _prog_origem_prevista(item: dict, antigo: dict | None, origem: str) -> str:
    if _prog_mexeu(item, antigo, origem):
        return origem
    return antigo.get("origem") if antigo and antigo.get("origem") in programacao.ORIGENS_ITEM else "local"


def _prog_checar_limite_diario(novos: list[dict], atuais: list[dict]) -> None:
    """
    Tetos por dia da semana (um aparelho com o PIN não pode deixar o PC da loja tocando horas por dia, todo
    dia, nem interrompendo a música o dia inteiro). Só recusa o que PIORA um dia que passou do limite (uma
    programação já grande não impede de desativar ou apagar horários):
      * som (áudio + estimativa de fala dos anúncios) <= PROG_AUDIO_DIA_MAX_S (60 min);
      * horários ativos <= PROG_EVENTOS_DIA_MAX (72) - cada um interrompe a música uma vez;
      * listas/playlists do Spotify ativas criadas/alteradas por aparelho REMOTO <= PROG_REMOTO_LISTAS_DIA_MAX (10).
    """
    por_id = {i["id"]: i for i in atuais}
    origem = _origem_da_requisicao()
    previstos = [{**i, "origem": _prog_origem_prevista(i, por_id.get(i.get("id") or ""), origem)} for i in novos]

    depois, antes = _prog_som_por_dia(novos), _prog_som_por_dia(atuais)
    for dia in range(7):
        if depois[dia] > PROG_AUDIO_DIA_MAX_S and depois[dia] > antes[dia]:
            raise ErroProg(
                f"Os áudios e anúncios agendados somam mais de {int(PROG_AUDIO_DIA_MAX_S // 60)} minutos por dia "
                f"({_NOMES_DIAS[dia]}). Desative ou remova algum horário.",
                codigo="limite_diario", campo="alvo")
    depois, antes = _prog_por_dia(novos, lambda i: 1.0), _prog_por_dia(atuais, lambda i: 1.0)
    for dia in range(7):
        if depois[dia] > PROG_EVENTOS_DIA_MAX and depois[dia] > antes[dia]:
            raise ErroProg(
                f"Há mais de {PROG_EVENTOS_DIA_MAX} horários no mesmo dia ({_NOMES_DIAS[dia]}). "
                "Desative ou remova algum.", codigo="limite_diario", campo="itens")

    alvos_remotos = _alvos_remotos()

    def remoto_lista(i: dict) -> float:
        if i["tipo"] not in ("lista", "spotify"):
            return 0.0
        return 1.0 if i.get("origem") == "remoto" or (i["tipo"], i["alvo"]) in alvos_remotos else 0.0

    depois, antes = _prog_por_dia(previstos, remoto_lista), _prog_por_dia(atuais, remoto_lista)
    for dia in range(7):
        if depois[dia] > PROG_REMOTO_LISTAS_DIA_MAX and depois[dia] > antes[dia]:
            raise ErroProg(
                f"Por aparelhos de fora do PC da loja só cabem {PROG_REMOTO_LISTAS_DIA_MAX} listas ou "
                f"playlists agendadas por dia ({_NOMES_DIAS[dia]}). Peça ao dono para agendar no PC.",
                codigo="limite_diario", campo="alvo")


def _alvos_remotos() -> set[tuple[str, str]]:
    """(tipo, id) das listas/playlists do Spotify cujo último autor foi um aparelho remoto."""
    doc = listas_ler()
    return ({("lista", x["id"]) for x in doc["listas"] if x.get("alterado_por") == "remoto"}
            | {("spotify", x["id"]) for x in doc["spotify"] if x.get("alterado_por") == "remoto"})


def _prog_checar_alvo_remoto(tipo: str, alvo_id: str) -> None:
    """
    Um aparelho REMOTO está mudando o conteúdo de uma lista/playlist: se horários ativos a tocam, ela passa a
    contar no teto de PROG_REMOTO_LISTAS_DIA_MAX por dia (senão bastaria editar o que o dono agendou). Só
    recusa o que PIORA um dia que passou do limite. O PC da loja não tem esse teto.
    """
    if _origem_da_requisicao() != "remoto":
        return
    itens = _prog_arquivo.ler()["itens"]
    ja = _alvos_remotos()

    def conta(extra: bool) -> list[float]:
        def valor(i: dict) -> float:
            if i["tipo"] not in ("lista", "spotify"):
                return 0.0
            chave = (i["tipo"], i["alvo"])
            return 1.0 if i.get("origem") == "remoto" or chave in ja or (extra and chave == (tipo, alvo_id)) else 0.0
        return _prog_por_dia(itens, valor)

    depois, antes = conta(True), conta(False)
    for dia in range(7):
        if depois[dia] > PROG_REMOTO_LISTAS_DIA_MAX and depois[dia] > antes[dia]:
            raise ErroLista(
                f"Esta lista/playlist está agendada e, por aparelhos de fora do PC da loja, só cabem "
                f"{PROG_REMOTO_LISTAS_DIA_MAX} listas ou playlists agendadas por dia ({_NOMES_DIAS[dia]}). "
                "Peça ao dono para alterar no PC.", 400, codigo="limite_diario")


def _prog_marcar_origem(final: list[dict], atuais: list[dict]) -> None:
    """
    Carimbos do SERVIDOR em cada item (o cliente não pode mandar nenhum): "origem" ("local" | "remoto") =
    quem alterou o CONTEÚDO por último (ligar/desligar pelo PC não conta; veja _prog_mexeu); "criado_por" =
    quem criou (imutável); "alterado_em" = epoch da última alteração de conteúdo.
    """
    origem = _origem_da_requisicao()
    agora = int(time.time())
    antigos = {i["id"]: i for i in atuais}
    for item in final:
        antigo = antigos.get(item["id"])
        mexeu = _prog_mexeu(item, antigo, origem)
        item["origem"] = _prog_origem_prevista(item, antigo, origem)
        item["criado_por"] = (antigo.get("criado_por") or antigo.get("origem") or "local") if antigo else origem
        item["alterado_em"] = agora if mexeu else (antigo.get("alterado_em") or 0)


def _prog_pendentes() -> list[tuple[dict, str]]:
    """
    Horários que um aparelho remoto deixou (ou que tocam algo que ele deixou), para o dono revisar: (item,
    motivo) com motivo "item" (o próprio horário foi criado/alterado por remoto) ou "alvo" (a lista ou a
    playlist do Spotify que ele toca foi alterada por remoto). Inclui os desativados.
    """
    doc = listas_ler()
    listas = {x["id"]: x for x in doc["listas"]}
    atalhos = {x["id"]: x for x in doc["spotify"]}
    achados: list[tuple[dict, str]] = []
    for item in _prog_arquivo.ler()["itens"]:
        if item.get("origem") == "remoto":
            achados.append((item, "item"))
        elif item["tipo"] == "lista" and listas.get(item["alvo"], {}).get("alterado_por") == "remoto":
            achados.append((item, "alvo"))
        elif item["tipo"] == "spotify" and atalhos.get(item["alvo"], {}).get("alterado_por") == "remoto":
            achados.append((item, "alvo"))
    return achados


def _prog_contar_remotos() -> int:
    """Quantos horários vieram de aparelhos remotos (ou tocam algo alterado por eles): o aviso ao trocar o PIN."""
    return len(_prog_pendentes())


# --- Revisão do que aparelhos remotos deixaram (v2.7.1, P04) -------------------------------------
# Pendente = o ÚLTIMO autor do conteúdo foi um aparelho remoto. Duas classes, que o painel mostra separadas:
#   criados_pela_rede  - o item NASCEU num aparelho remoto (criado_por == "remoto");
#   mudados_pela_rede  - o item é do dono (criado_por == "local"), mas um remoto mexeu no conteúdo depois
#                        (ou o horário do dono toca uma lista/playlist que um remoto alterou).
# `remover_remotos` apaga SÓ os criados pela rede: o que é do dono nunca é apagado por esta rotina (um remoto
# pode ter editado a lista do dono; apagá-la perderia trabalho legítimo). Esses ficam pendentes para o dono
# decidir (e, se forem horários, desativados por `desativar_remotos`).
# Corrida conhecida: uma requisição REMOTA que já estava em andamento quando o PIN foi trocado ou as sessões
# foram revogadas pode terminar de gravar DEPOIS da revisão (janela de milissegundos entre validar a sessão e
# gravar). O que ela deixar aparece como pendente na próxima consulta de /api/seguranca/remotos; por isso o
# painel local avisa o dono de pendências também depois de revogar, e a revisão pode ser repetida.
MUSICAS_ORIGEM_MAX = 2000


REMOVIDOS_TIPOS = ("horarios", "listas", "atalhos", "mensagens")


def _contar_removidos_remoto(tipo: str, n: int) -> None:
    """
    Um aparelho REMOTO apagou `n` itens de `tipo`: o dono não consegue "ver" o que sumiu, então só o NÚMERO fica
    anotado (config.json "removidos_remotos", por tipo) para o painel avisar. Nunca nome, texto nem id. O PC da
    loja apagando não conta. Zera quando o dono aprova ("removidos" ou "todos").
    """
    if n <= 0 or tipo not in REMOVIDOS_TIPOS or not has_request_context() or _origem_da_requisicao() != "remoto":
        return
    try:
        with _config_lock:
            cfg = load_config()
            cont = cfg.get("removidos_remotos") if isinstance(cfg.get("removidos_remotos"), dict) else {}
            cont[tipo] = min(10**6, int(cont.get(tipo, 0) if isinstance(cont.get(tipo, 0), int) else 0) + n)
            cfg["removidos_remotos"] = cont
            _gravar_config_atomico(cfg)
    except OSError as exc:
        log.warning("Não foi possível anotar o que um aparelho remoto apagou (%s).", type(exc).__name__)


def _removidos_remotos(cfg: dict) -> dict[str, int]:
    bruto = cfg.get("removidos_remotos") if isinstance(cfg.get("removidos_remotos"), dict) else {}
    return {t: v for t in REMOVIDOS_TIPOS if isinstance(v := bruto.get(t), int) and not isinstance(v, bool) and v > 0}


def _som_saidas_remotas(cfg: dict) -> list[str]:
    """sids das saídas de som APROVADAS por um aparelho remoto (som.saidas[sid].aprovada_por == "remoto")."""
    som = cfg.get("som") if isinstance(cfg.get("som"), dict) else {}
    saidas = som.get("saidas") if isinstance(som.get("saidas"), dict) else {}
    return [sid for sid, e in saidas.items() if isinstance(e, dict) and e.get("aprovada") is True
            and e.get("aprovada_por") == "remoto"]


def _apagavel(registro: dict) -> bool:
    """
    `remover_remotos` só apaga o que NASCEU num aparelho remoto E continua com o último autor remoto: se o dono
    editou depois (alterado_por "local"), ele assumiu o item - a edição do dono aprova implicitamente - e nada o apaga.
    """
    return registro.get("criado_por") == "remoto" and registro.get("alterado_por") == "remoto"


def _remotas(registros: list[dict]) -> list[dict]:
    return [r for r in registros if r.get("alterado_por") == "remoto"]


def _mensagens_remotas(cfg: dict) -> list[dict]:
    """Mensagens prontas (gravadas) cujo último autor foi um aparelho remoto, com o carimbo."""
    meta = _mensagens_meta(cfg)
    return [{**m, **meta.get(m["id"], {})} for m in _mensagens_do_config(cfg)[0]
            if meta.get(m["id"], {}).get("alterado_por") == "remoto"]


def _musicas_remotas(cfg: dict) -> dict[str, int]:
    """Músicas ENVIADAS por aparelho remoto que ainda estão na pasta: {music_id: epoch do envio}."""
    bruto = cfg.get("musicas_origem")
    achadas: dict[str, int] = {}
    for mid, em in (bruto.items() if isinstance(bruto, dict) else []):
        if isinstance(mid, str) and musicas.RE_ID.fullmatch(mid) and _numero_epoch(em) is not None:
            if not ACERVO.pronto or ACERVO.obter(mid) is not None:
                achadas[mid] = int(em)
    return achadas


def _som_pendente(cfg: dict) -> int:
    """Epoch da última mudança de preferências de som feita por aparelho remoto (0 = nenhuma pendente)."""
    em = _numero_epoch((cfg.get("som_alterado_por") or {}).get("em")) if isinstance(cfg.get("som_alterado_por"), dict) else None
    return int(em or 0)


def _horario_criado_pela_rede(item: dict, motivo: str) -> bool:
    return motivo == "item" and (item.get("criado_por") or item.get("origem")) == "remoto"


def _revisao_contar() -> dict[str, int]:
    """Contagens do que espera revisão, com a divisão criados_pela_rede x mudados_pela_rede."""
    doc = listas_ler()
    cfg = load_config()
    criados = mudados = 0
    horarios = _prog_pendentes()
    for item, motivo in horarios:
        if _horario_criado_pela_rede(item, motivo):
            criados += 1
        else:
            mudados += 1
    listas, atalhos, mensagens = _remotas(doc["listas"]), _remotas(doc["spotify"]), _mensagens_remotas(cfg)
    for reg in listas + atalhos + mensagens:
        if reg.get("criado_por") == "remoto":
            criados += 1
        else:
            mudados += 1
    apagaveis = sum(1 for r in listas + atalhos + mensagens if _apagavel(r))
    musicas_n = len(_musicas_remotas(cfg))
    criados += musicas_n
    som_n = (1 if _som_pendente(cfg) else 0) + len(_som_saidas_remotas(cfg))   # preferências + saídas aprovadas por remoto
    mudados += som_n
    total = len(horarios) + len(listas) + len(atalhos) + len(mensagens) + musicas_n + som_n
    return {"horarios": len(horarios), "listas": len(listas), "atalhos": len(atalhos),
            "mensagens": len(mensagens), "musicas": musicas_n, "som": som_n,
            "criados_pela_rede": criados, "mudados_pela_rede": mudados, "total": total,
            # quantos itens o remover_remotos realmente APAGA (listas + atalhos + mensagens criados pela rede);
            # os horários criados pela rede e as músicas NÃO entram: horário só é desativado, música nunca é apagada
            "apagaveis_pela_rede": apagaveis,
            # só números (por tipo no inventário): o que um remoto APAGOU. Informativo, fora do "total".
            "removidos_pela_rede": sum(_removidos_remotos(cfg).values())}


def _revisao_inventario() -> dict[str, Any]:
    """O que os aparelhos remotos criaram/alteraram: ids, nomes/títulos curtos e carimbos (nunca o texto das mensagens)."""
    doc = listas_ler()
    cfg = load_config()
    agora = PROGRAMACAO.agora()
    horarios = []
    for item, motivo in _prog_pendentes():
        publico = _prog_item_publico(item, agora)
        horarios.append({
            "id": item["id"], "rotulo": publico["rotulo"], "tipo": item["tipo"], "hora": item["hora"],
            "dias": item.get("dias"), "data": item.get("data"), "ativo": item["ativo"], "motivo": motivo,
            # nome da lista/playlist que o horário toca: o MESMO campo (e higienização) do GET /api/programacao
            "alvo_nome": publico["alvo_nome"] if item["tipo"] in ("lista", "spotify") else None,
            "criado_por": item.get("criado_por") or item.get("origem") or "local",
            "alterado_por": item.get("origem") or "local", "alterado_em": item.get("alterado_em") or 0})

    def carimbos(r: dict) -> dict[str, Any]:
        return {"criado_por": r["criado_por"], "alterado_por": r["alterado_por"], "alterado_em": r["alterado_em"]}

    musicas_lista = []
    for mid, em in sorted(_musicas_remotas(cfg).items(), key=lambda kv: -kv[1]):
        m = ACERVO.obter(mid)
        musicas_lista.append({"id": mid, "titulo": m.titulo if m is not None else None, "enviada_em": em,
                              "criado_por": "remoto"})
    return {
        "pendentes": _revisao_contar(),
        "horarios": horarios,
        "listas": [{"id": x["id"], "nome": x["nome"], "total": len(x["musicas"]), **carimbos(x)}
                   for x in _remotas(doc["listas"])],
        "atalhos": [{"id": x["id"], "nome": x["nome"], **carimbos(x)} for x in _remotas(doc["spotify"])],
        "mensagens": [{"id": m["id"], "titulo": m["titulo"], **carimbos(m)} for m in _mensagens_remotas(cfg)],
        "musicas": musicas_lista,
        "som": {"pendente": bool(_som_pendente(cfg) or _som_saidas_remotas(cfg)),
                "alterado_em": _som_pendente(cfg), "saidas": len(_som_saidas_remotas(cfg))},
        "removidos": _removidos_remotos(cfg),
    }


def _aplicar_revisao_remotos(opcoes: dict[str, bool]) -> dict[str, Any]:
    """
    Desfaz (conforme as opções) o que aparelhos remotos deixaram. Chamado logo DEPOIS de trocar o PIN ou
    revogar as sessões (veja a "corrida conhecida" acima).
      desativar_remotos   horários pendentes (criados OU mudados pela rede; veja _prog_pendentes) ficam inativos;
                          os que já estavam na fila de execução saem dela; se um deles está tocando agora, é
                          interrompido;
      remover_remotos     apaga mensagens, listas e atalhos do Spotify CRIADOS por aparelho remoto
                          (criado_por == "remoto"); o que é do dono e foi só alterado por remoto NÃO é apagado.
                          Mensagens: se não sobrar nenhuma, volta à lista de fábrica. Músicas enviadas por
                          remoto nunca são apagadas aqui (só listadas);
      restaurar_mensagens volta TODAS as mensagens à lista de fábrica;
      limpar_fila         esvazia a fila do Tocador (YouTube e arquivos).
    Devolve as contagens do que foi feito (nunca nomes nem textos).
    """
    global _yt_fila_versao
    res: dict[str, Any] = {
        "horarios_desativados": 0, "horarios_cancelados": 0, "parou_item": False, "fila_limpa": False,
        "mensagens_removidas": 0, "listas_removidas": 0, "atalhos_removidos": 0, "mensagens_restauradas": False,
        "itens_removidos": 0,
    }
    if opcoes.get("desativar_remotos"):
        ids = {item["id"] for item, _ in _prog_pendentes()}
        if ids:
            with _prog_arquivo.lock:
                doc = copy.deepcopy(_prog_arquivo.ler())
                for item in doc["itens"]:
                    if item["id"] in ids and item.get("ativo"):
                        item["ativo"] = False
                        res["horarios_desativados"] += 1
                if res["horarios_desativados"]:
                    doc["versao"] += 1
                    _prog_arquivo.gravar(doc)
            res["horarios_cancelados"] = PROGRAMACAO.cancelar_fila(lambda it: it.get("id") in ids)
            tocando = PROGRAMACAO.tocando
            if tocando is not None and tocando.get("id") in ids:
                res["parou_item"] = PROGRAMACAO.parar_execucao() != "nada"
    if opcoes.get("limpar_fila"):
        with _yt_fila_lock:
            _yt_fila.clear()
            _yt_fila_versao += 1
        res["fila_limpa"] = True
    if opcoes.get("remover_remotos"):
        with _listas_arquivo.lock:
            doc = copy.deepcopy(listas_ler())
            antes_l, antes_a = len(doc["listas"]), len(doc["spotify"])
            doc["listas"] = [x for x in doc["listas"] if not _apagavel(x)]
            doc["spotify"] = [x for x in doc["spotify"] if not _apagavel(x)]
            res["listas_removidas"] = antes_l - len(doc["listas"])
            res["atalhos_removidos"] = antes_a - len(doc["spotify"])
            if res["listas_removidas"]:
                doc["versao"] += 1
            if res["atalhos_removidos"]:
                doc["spotify_versao"] += 1
                doc["versao"] += 1
            if res["listas_removidas"] or res["atalhos_removidos"]:
                _listas_arquivo.gravar(doc)
        if not opcoes.get("restaurar_mensagens"):
            with _config_lock:
                cfg = load_config()
                meta = _mensagens_meta(cfg)
                criadas = {mid for mid, m in meta.items() if _apagavel(m)}
                if criadas and isinstance(cfg.get("mensagens"), list):
                    restantes = [m for m in cfg["mensagens"] if isinstance(m, dict) and m.get("id") not in criadas]
                    res["mensagens_removidas"] = len(cfg["mensagens"]) - len(restantes)
                    if restantes:
                        cfg["mensagens"] = restantes
                    else:
                        cfg.pop("mensagens", None)
                    cfg["mensagens_versao"] = _versao_mensagens(cfg) + 1
                    _gravar_config_atomico(cfg)
    if opcoes.get("restaurar_mensagens"):
        with _config_lock:
            cfg = load_config()
            if "mensagens" in cfg:
                cfg.pop("mensagens")
                cfg["mensagens_versao"] = _versao_mensagens(cfg) + 1
                _gravar_config_atomico(cfg)
        res["mensagens_restauradas"] = True
    res["itens_removidos"] = res["mensagens_removidas"] + res["listas_removidas"] + res["atalhos_removidos"]
    return res


def _aprovar_registros(chave: str, ident: str | None) -> int:
    """Aprova listas (chave "listas") ou atalhos ("spotify") com último autor remoto. Devolve quantos."""
    with _listas_arquivo.lock:
        doc = copy.deepcopy(listas_ler())
        mudou = 0
        for x in doc[chave]:
            if x.get("alterado_por") == "remoto" and (ident is None or x["id"] == ident):
                x["alterado_por"] = "local"
                mudou += 1
        if mudou:
            doc["versao"] += 1
            _listas_arquivo.gravar(doc)
    return mudou


def _revisao_aprovar(tipo: str | None, ident: str | None) -> int:
    """
    O dono assume o item (ou tudo, com tipo None): o último autor passa a ser "local". Devolve quantos.
    Aprovar um HORÁRIO também aprova a lista/playlist que ele toca, se um remoto a alterou (é isso que o
    painel mostra como motivo "alvo"): sem isso o horário voltaria a aparecer como pendente.
    """
    n = 0
    if tipo in (None, "horario"):
        alvos: list[tuple[str, str]] = []
        with _prog_arquivo.lock:
            doc = copy.deepcopy(_prog_arquivo.ler())
            mudou = 0
            for item in doc["itens"]:
                if ident is not None and item["id"] != ident:
                    continue
                if item.get("origem") == "remoto":
                    item["origem"] = "local"
                    mudou += 1
                if tipo == "horario" and item["tipo"] in ("lista", "spotify"):
                    alvos.append((item["tipo"], item["alvo"]))
            if mudou:
                doc["versao"] += 1
                _prog_arquivo.gravar(doc)
            n += mudou
        for tipo_alvo, alvo_id in alvos:
            n += _aprovar_registros("listas" if tipo_alvo == "lista" else "spotify", alvo_id)
    if tipo in (None, "lista"):
        n += _aprovar_registros("listas", ident)
    if tipo in (None, "atalho"):
        n += _aprovar_registros("spotify", ident)
    if tipo in (None, "mensagem"):
        with _config_lock:
            cfg = load_config()
            mudou = 0
            for m in (cfg.get("mensagens") if isinstance(cfg.get("mensagens"), list) else []):
                if (isinstance(m, dict) and m.get("alterado_por") == "remoto"
                        and (ident is None or m.get("id") == ident)):
                    m["alterado_por"] = "local"
                    mudou += 1
            if mudou:
                _gravar_config_atomico(cfg)
            n += mudou
    if tipo in (None, "musica"):
        with _config_lock:
            cfg = load_config()
            origem = cfg.get("musicas_origem") if isinstance(cfg.get("musicas_origem"), dict) else {}
            sair = [m for m in origem if ident is None or m == ident]
            if sair:
                for m in sair:
                    origem.pop(m, None)
                if origem:
                    cfg["musicas_origem"] = origem
                else:
                    cfg.pop("musicas_origem", None)
                _gravar_config_atomico(cfg)
            n += len([m for m in sair if musicas.RE_ID.fullmatch(m)])
    if tipo in (None, "som") and (ident in (None, "preferencias")):
        with _config_lock:
            cfg = load_config()
            if "som_alterado_por" in cfg:
                cfg.pop("som_alterado_por")
                _gravar_config_atomico(cfg)
                n += 1
        sids = _som_saidas_remotas(load_config())
        if sids:   # saídas aprovadas por remoto: o dono assume (aprovada_por passa a "local")
            def _assumir(som: dict) -> None:
                for sid in sids:
                    if sid in som.get("saidas", {}):
                        som["saidas"][sid]["aprovada_por"] = "local"

            with contextlib.suppress(Exception):
                SOM.atualizar_config(_assumir)
                n += len(sids)
    if tipo in (None, "removidos"):
        with _config_lock:
            cfg = load_config()
            if "removidos_remotos" in cfg:
                n += sum(_removidos_remotos(cfg).values()) or 1
                cfg.pop("removidos_remotos")
                _gravar_config_atomico(cfg)
    return n


def _registrar_musicas_remotas(ids: list[str]) -> None:
    """Anota (no config.json) que estas músicas foram ENVIADAS por aparelho remoto. Fica só o id e o epoch."""
    if not ids:
        return
    agora = int(time.time())
    try:
        with _config_lock:
            cfg = load_config()
            origem = cfg.get("musicas_origem") if isinstance(cfg.get("musicas_origem"), dict) else {}
            for mid in ids:
                origem[mid] = agora
            if len(origem) > MUSICAS_ORIGEM_MAX:
                for velho in sorted(origem, key=lambda k: origem[k])[:len(origem) - MUSICAS_ORIGEM_MAX]:
                    del origem[velho]
            cfg["musicas_origem"] = origem
            _gravar_config_atomico(cfg)
    except OSError as exc:
        log.warning("Não foi possível anotar a origem das músicas enviadas (%s).", type(exc).__name__)


@app.get("/api/seguranca/remotos")
def seguranca_remotos() -> Response:
    """Só local. O que aparelhos remotos criaram/alteraram e o dono ainda não assumiu (horários, listas, atalhos, mensagens, músicas, som)."""
    if not _requisicao_local():
        raise ErroApi(MSG_SOMENTE_LOCAL, 403)
    return jsonify({"ok": True, **_revisao_inventario()})


@app.post("/api/seguranca/remotos/aprovar")
def seguranca_remotos_aprovar() -> Response:
    """
    Só local. {"tipo": "horario"|"lista"|"atalho"|"mensagem"|"musica"|"som", "id": "..."} assume UM item (som:
    id "preferencias"); {"todos": true} assume tudo. O item continua existindo; só deixa de contar como "deixado
    por aparelho remoto". Aprovar um horário aprova também a lista/playlist que ele toca.
    """
    if not _requisicao_local():
        raise ErroApi(MSG_SOMENTE_LOCAL, 403)
    corpo = corpo_json()
    if set(corpo) - {"tipo", "id", "todos"}:
        raise ErroApi("Campo desconhecido.")
    if corpo.get("todos") is True and set(corpo) == {"todos"}:
        n = _revisao_aprovar(None, None)
    else:
        tipo, ident = corpo.get("tipo"), corpo.get("id")
        if tipo not in ("horario", "lista", "atalho", "mensagem", "musica", "som", "removidos") or "todos" in corpo:
            raise ErroApi("Campo 'tipo' inválido.")
        regex = {"mensagem": RE_MENSAGEM_ID, "musica": musicas.RE_ID, "som": re.compile("preferencias"),
                 "removidos": re.compile("todos")}.get(tipo, RE_LISTA_ID)
        if not isinstance(ident, str) or not regex.fullmatch(ident):
            raise ErroApi("Campo 'id' inválido.")
        n = _revisao_aprovar(tipo, ident)
        if n == 0:
            raise ErroApi("Esse item não está na lista de itens a revisar.", 404)
    auditar("remotos_aprovar", total=n)
    return jsonify({"ok": True, "aprovados": n, "pendentes": _revisao_contar()})


def _auditar_programacao(acao: str, total: int) -> None:
    """Quem (local/IP) e QUANTOS horários. Nome, texto e ids nunca vão para o log."""
    auditoria.info("acao=%s ip=%s origem=%s total=%d", acao, request.remote_addr or "?",
                   "local" if _requisicao_local() else "remoto", total)


@app.get("/api/programacao")
def programacao_listar() -> Response:
    return _prog_resposta()


@app.post("/api/programacao")
def programacao_salvar() -> Response:
    """
    Substitui a programação INTEIRA {versao, itens}. Ordem: 400, 429, 409 (versão / pasta sendo lida).
    O corpo é validado antes de gastar a cota de gravações (um 400 não conta).
    """
    request.max_content_length = PROG_CORPO_MAX   # só nesta rota; antes de ler o corpo
    corpo = corpo_json()
    atuais = {i["id"]: i for i in _prog_arquivo.ler()["itens"]}
    versao, novos = programacao.validar_corpo(
        corpo, atuais=atuais, hoje=PROGRAMACAO.agora().date(), checar_alvo=_prog_checar_alvo)
    _prog_checar_limite_diario(novos, list(atuais.values()))
    if excedeu_limite("programacao:" + (request.remote_addr or "?"), PROG_ESCRITA_MAX_POR_MIN, 60.0):
        raise ErroProg("Muitas alterações seguidas. Aguarde um minuto.", 429)
    with _prog_arquivo.lock:
        doc = copy.deepcopy(_prog_arquivo.ler())
        if versao != doc["versao"]:
            raise ErroProg(MSG_PROG_VERSAO_MUDOU, 409, codigo="versao_mudou")
        existentes = {i["id"] for i in doc["itens"]}
        usados = existentes | {i["id"] for i in novos if "id" in i}
        final: list[dict] = []
        for indice, item in enumerate(novos):
            if "id" not in item:
                item = {"id": programacao.novo_id(usados), **item}
                usados.add(item["id"])
            elif item["id"] not in existentes:   # só acontece se o arquivo foi editado à mão entre a leitura e o lock
                raise ErroProg(MSG_PROG_NAO_EXISTE, campo="id", indice=indice)
            final.append({"id": item["id"], **{k: v for k, v in item.items() if k != "id"}})
        _prog_marcar_origem(final, doc["itens"])
        doc["itens"] = final
        doc["versao"] += 1
        _prog_arquivo.gravar(doc)
    _contar_removidos_remoto("horarios", len(existentes - {i["id"] for i in final}))
    # v2.7.1 (P13): o que já venceu e espera na fila mas foi apagado/desativado agora não toca.
    ativos = {i["id"] for i in final if i.get("ativo")}
    PROGRAMACAO.cancelar_fila(lambda it: it.get("id") not in ativos)
    _auditar_programacao("programacao_salvar", len(final))
    return _prog_resposta()


@app.post("/api/programacao/<item_id>/testar")
def programacao_testar(item_id: str) -> tuple[Response, int]:
    """
    "Tocar agora": executa o item já (mesmo pausar/retomar), mesmo inativo. Responde 202 e toca em segundo
    plano. Ordem: 404 (id/horário), 400, 429, 409 (anúncio ou programação tocando / sem Tocador).
    """
    if not programacao.RE_ITEM_ID.fullmatch(item_id):
        raise ErroProg(MSG_PROG_NAO_EXISTE, 404)
    if corpo_json():
        raise ErroProg("Campo desconhecido.", campo="chave")
    item = next((i for i in _prog_arquivo.ler()["itens"] if i["id"] == item_id), None)
    if item is None:
        raise ErroProg(MSG_PROG_NAO_EXISTE, 404)
    if excedeu_limite("prog-testar:" + (request.remote_addr or "?"), PROG_TESTAR_MAX_POR_MIN, 60.0):
        raise ErroProg("Muitos pedidos seguidos. Aguarde um instante.", 429)
    if _desligamento_agendado:
        raise ErroProg("O Proclamare está sendo desligado.", 409, codigo="desligando")
    if item["tipo"] in ("lista", "spotify"):
        if PROGRAMACAO.tocando is not None:
            raise ErroProg(_msg_ocupado(""), 409, codigo="ocupado")
        if item["tipo"] == "spotify":   # sem a trava de áudio: o som sai pelo Spotify do PC
            if atalho_spotify_obter(item["alvo"]) is None:
                raise ErroProg(MSG_ATALHO_NAO_EXISTE, 404, codigo="atalho_inexistente")
        elif not _tocador_vivo():
            raise ErroProg(MSG_SEM_TOCADOR, 409, codigo="sem_tocador")
        PROGRAMACAO.solicitar(item, lock_tomado=False)
    else:
        if item["tipo"] == "audio":   # erros que não dependem da trava: respondidos antes de tomá-la
            try:
                _prog_musica_do_item(item)
            except programacao.NaoTocou as e:
                raise ErroProg(e.texto, 404 if e.motivo == "sem_musica" else 409, codigo=e.motivo) from None
        if not _anuncio_lock.acquire(blocking=False):
            raise ErroProg(_msg_ocupado("Já existe um anúncio tocando. Aguarde terminar."), 409, codigo="ocupado")
        try:
            PROGRAMACAO.solicitar(item, lock_tomado=True)   # o executar libera a trava ao terminar
        except BaseException:
            _anuncio_lock.release()
            raise
    _auditar_programacao("programacao_testar", 1)
    return jsonify({"ok": True, "execucao": {"id": item["id"], "tipo": item["tipo"],
                                               "nome": _prog_nome(item)}}), 202


@app.post("/api/programacao/parar")
def programacao_parar() -> Response:
    """Interrompe o ÁUDIO ou ANÚNCIO agendado que está tocando. 409 se nada da programação toca."""
    if corpo_json():
        raise ErroProg("Campo desconhecido.", campo="chave")
    if excedeu_limite("prog-parar:" + (request.remote_addr or "?"), PROG_PARAR_MAX_POR_MIN, 60.0):
        raise ErroProg("Muitos pedidos seguidos. Aguarde um instante.", 429)
    resultado = PROGRAMACAO.parar_execucao()
    if resultado == "nada":
        raise ErroProg("Não há nada tocando pela programação.", 409, codigo="nada_tocando")
    _auditar_programacao("programacao_parar", 1)
    return jsonify({"ok": True, "parando": resultado == "parando",
                    "aviso": None if resultado == "parando" else
                    "Esta voz não pode ser interrompida; ela termina em instantes."})


# ---------------------------------------------------------------------------
# Desligar pelo painel (contrato v2.5) — SÓ no PC da loja
# A rota responde 202 e, numa thread, ~0,5 s depois, pede ao laço do servidor (thread principal)
# que saia pelo MESMO caminho do Ctrl+C/SIGTERM (main() fecha a porta, para os serviços e
# remove o arquivo de PID). Um vigia força a saída se algo prender o encerramento.
# ---------------------------------------------------------------------------
DESLIGAR_ATRASO_S = 0.5        # deixa a resposta 202 chegar ao navegador antes de fechar a porta
DESLIGAR_PRAZO_MAX_S = 10.0    # depois disto o vigia encerra o processo à força
_desligar_lock = threading.Lock()
_desligamento_agendado = False   # True = já aceito; o _anuncio_lock fica preso até o processo acabar
_servidor_ativo: Any = None      # o servidor do main(); None = módulo importado (testes), sem o que desligar


def _vigia_de_saida() -> None:
    """Último recurso: se o processo ainda existir depois do prazo, remove o PID e sai."""
    time.sleep(DESLIGAR_PRAZO_MAX_S)
    log.error("O Proclamare não encerrou a tempo; forçando a saída.")
    _remover_pid_file()
    logging.shutdown()
    os._exit(0)


def _desligar_apos_resposta() -> None:
    """Corpo da thread "desligar-painel": acorda quem espera e interrompe o laço do servidor."""
    global _desligamento_agendado
    time.sleep(DESLIGAR_ATRASO_S)
    servidor = _servidor_ativo
    if servidor is None:   # não está rodando pelo main(): não há o que desligar (e nada é morto)
        log.warning("Pedido de desligar sem servidor em execução; ignorado.")
        with _desligar_lock:
            _desligamento_agendado = False
            _anuncio_lock.release()
        return
    _desligando.set()                      # long-polls e a reaplicação do Spotify saem logo
    with _yt_cond:
        _yt_cond.notify_all()
    _reaplic_acordar.set()
    threading.Thread(target=_vigia_de_saida, name="desligar-vigia", daemon=True).start()
    _thread.interrupt_main()               # KeyboardInterrupt na thread principal (via _ao_sinal)
    with contextlib.suppress(Exception):   # acorda o select() do waitress: sai já, não em até 1 s
        servidor.pull_trigger()


def _agendar_desligamento() -> None:
    """Dispara a thread do desligamento (os testes trocam esta função por um espião)."""
    threading.Thread(target=_desligar_apos_resposta, name="desligar-painel", daemon=True).start()


@app.post("/api/sistema/desligar")
@somente_local
def sistema_desligar() -> tuple[Response, int]:
    """
    Desliga o Proclamare. Ordem: 403 (remoto, mesmo com PIN), 400 (corpo diferente de {}),
    409 (envio de músicas em andamento; anúncio ou teste de saída tocando), 202. Um 2º pedido
    durante o desligamento repete o 202.
    """
    global _desligamento_agendado
    if corpo_json():
        raise ErroApi("Campo desconhecido.")
    with _desligar_lock:
        if _desligamento_agendado:
            return jsonify({"ok": True}), 202
        # Só conta o envio já em processamento (o waitress recebe o corpo antes de a rota rodar).
        if _envios_ativos > 0:
            raise ErroApi("Há músicas sendo enviadas. Espere terminar.", 409)
        # Segura o lock dos anúncios: nenhum anúncio novo começa e nenhum tocando é cortado.
        if not _anuncio_lock.acquire(blocking=False):
            # v2.6: com um áudio/anúncio da programação tocando, a mensagem diz qual (e que dá para parar)
            raise ErroApi(_msg_ocupado("Espere o anúncio terminar.", ". Toque em Parar ou espere terminar."), 409)
        _desligamento_agendado = True
    log.info("Desligado pelo painel (PC local).")
    auditar("desligar", resultado="aceito")
    # A aba "Tocador da loja" pode estar em OUTRA aba ou aparelho: manda uma pausa agora, para ela
    # chegar no long-poll antes de a porta fechar (a thread do desligamento ainda espera 0,5 s).
    with contextlib.suppress(Exception):
        youtube_enqueue("pause")
    try:
        _agendar_desligamento()
    except Exception:   # noqa: BLE001  (não conseguiu criar a thread: volta ao normal)
        with _desligar_lock:
            _desligamento_agendado = False
            _anuncio_lock.release()
        raise
    return jsonify({"ok": True}), 202


# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------
LOG_FORMATO = "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"
AUDITORIA_FORMATO = "%(asctime)s %(message)s"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3
AUDITORIA_RETENCAO_DIAS = 90   # um arquivo por dia, 90 dias (os IPs da equipe são dado pessoal: prazo definido)


class _FormatadorIso(logging.Formatter):
    """Data/hora ISO 8601 com o fuso (2026-10-20T14:03:09-03:00): a hora sem fuso confunde a análise."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="seconds")


def _caminho_auditoria(caminho_log: str) -> Path | None:
    """PROCLAMARE_AUDIT_FILE, senão `auditoria.log` ao lado do log comum; sem log em arquivo, None."""
    bruto = _env("AUDIT_FILE", "").strip()
    if bruto:
        return Path(bruto)
    return Path(caminho_log).with_name("auditoria.log") if caminho_log else None


class _AuditoriaRotativa(logging.handlers.TimedRotatingFileHandler):
    """
    Rotação diária que REAPLICA a ACL restrita: cada arquivo novo (a cada meia-noite, ou o primeiro de um dia
    sem log) nasce herdando a ACL da pasta; aqui ele passa a ser só do usuário, SYSTEM e Administradores.
    """

    def _open(self) -> Any:
        fluxo = super()._open()
        _restringir_acl_windows(Path(self.baseFilename))
        return fluxo


def _configurar_auditoria(caminho: Path) -> str | None:
    """
    Liga o arquivo PRÓPRIO da trilha de auditoria (P22): rotação por dia (90 dias), data com fuso, nenhuma
    linha do waitress/comtypes no meio e nada dela no log comum. Devolve o nome do erro se não abriu.
    """
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        if not caminho.exists():
            caminho.touch()
            _restringir_acl_windows(caminho)    # só o usuário, SYSTEM e Administradores
        handler = _AuditoriaRotativa(
            caminho, when="midnight", backupCount=AUDITORIA_RETENCAO_DIAS, encoding="utf-8", delay=True)
    except (OSError, ValueError) as exc:
        return type(exc).__name__
    handler.setFormatter(_FormatadorIso(AUDITORIA_FORMATO))
    for antigo in list(auditoria.handlers):
        auditoria.removeHandler(antigo)
        antigo.close()
    auditoria.addHandler(handler)
    auditoria.setLevel(logging.INFO)     # a trilha não some se o log comum estiver em WARNING
    auditoria.propagate = False
    return None


def _configurar_log() -> None:
    """
    Log no arquivo PROCLAMARE_LOG_FILE (rotativo) quando definido; senão no console.
    O handler vai no logger RAIZ, então cobre também werkzeug. Com pythonw sys.stderr é None: sem
    arquivo, usamos NullHandler (nada quebra). A auditoria tem arquivo próprio (veja _configurar_auditoria)
    e o waitress só registra avisos (cada stream interrompido gerava uma linha INFO).
    """
    handlers: list[logging.Handler] = []
    erro_arquivo: str | None = None
    caminho = _env("LOG_FILE", "").strip()
    if caminho:
        try:
            destino = Path(caminho)
            destino.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(
                logging.handlers.RotatingFileHandler(
                    destino, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
                    encoding="utf-8", delay=True,
                )
            )
        except (OSError, ValueError) as exc:
            erro_arquivo = type(exc).__name__
    if not handlers:
        handlers.append(logging.StreamHandler() if sys.stderr is not None else logging.NullHandler())
    for h in handlers:
        h.setFormatter(_FormatadorIso(LOG_FORMATO))
    nivel = logging.getLevelName(_env("LOG_LEVEL", "INFO").upper())
    logging.basicConfig(
        level=nivel if isinstance(nivel, int) else logging.INFO, handlers=handlers, force=True
    )
    logging.getLogger("waitress").setLevel(max(logging.WARNING, logging.getLogger().level))
    if erro_arquivo:
        log.warning("Não foi possível abrir PROCLAMARE_LOG_FILE (%s); usando o console.", erro_arquivo)
    caminho_audit = _caminho_auditoria(caminho) if not erro_arquivo else None
    if caminho_audit is not None:
        erro_audit = _configurar_auditoria(caminho_audit)
        if erro_audit:
            auditoria.propagate = True
            log.warning("Não foi possível abrir o arquivo de auditoria (%s); ela fica no log comum.", erro_audit)


def _criado_em_filetime() -> int | None:
    """Horário de criação do PRÓPRIO processo (FILETIME: 100 ns desde 1601); só Windows."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetCurrentProcess.argtypes = []
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        criacao, saida, kernel, usuario = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(
            k32.GetCurrentProcess(), ctypes.byref(criacao), ctypes.byref(saida),
            ctypes.byref(kernel), ctypes.byref(usuario),
        ):
            return None
        return (criacao.dwHighDateTime << 32) | criacao.dwLowDateTime
    except Exception:  # noqa: BLE001
        return None


def limpar_residuos_de_dados() -> None:
    """
    Na partida (P46): apaga temporários de gravação atômica abandonados há mais de 1 h (guardam a
    configuração inteira, segredos incluídos) e poda as cópias "*.corrompido-*" para as 3 mais novas.
    """
    pasta = CONFIG_PATH.parent
    with contextlib.suppress(Exception):
        n = programacao.limpar_temporarios_orfaos(
            pasta, (".config.", ".listas.", ".programacao.", ".seguranca."))
        podadas = sum(programacao.podar_copias(pasta / nome) for nome in ("config.json", LISTAS_ARQUIVO, PROGRAMACAO_ARQUIVO))
        if n or podadas:
            log.info("Limpeza na partida: %d temporário(s) órfão(s) e %d cópia(s) corrompida(s) antiga(s) apagados.",
                     n, podadas)


def _caminho_pid_file() -> Path | None:
    bruto = _env("PID_FILE", "").strip()
    return Path(bruto) if bruto else None


def _gravar_pid_file(caminho: Path, porta: int) -> bool:
    """Grava o JSON de identificação de forma atômica (temporário + os.replace)."""
    info = {
        "pid": os.getpid(), "porta": porta,
        "criado_em": _criado_em_filetime(), "executavel": sys.executable,
    }
    tmp = caminho.with_name(f"{caminho.name}.{os.getpid()}.tmp")
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(info), encoding="utf-8")
        os.replace(tmp, caminho)
        return True
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        log.error("Não foi possível gravar PROCLAMARE_PID_FILE (%s).", type(exc).__name__)
        return False


def _remover_pid_file() -> None:
    """Remove o arquivo SOMENTE se ele ainda contém o pid deste processo."""
    caminho = _caminho_pid_file()
    if caminho is None:
        return
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        if isinstance(dados, dict) and dados.get("pid") == os.getpid():
            caminho.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass  # ausente, ilegível ou de outro processo: não mexe


def _instalar_encerramento() -> None:
    """atexit + sinais (SIGINT/SIGTERM/SIGBREAK): garantem a remoção do pid file."""
    atexit.register(_remover_pid_file)

    def _ao_sinal(_numero: int, _frame: Any) -> None:
        # Desligar pelo painel: o PID só some depois que a porta fechar (_encerrar_servicos); senão o
        # lançador veria "desligado", subiria outro servidor e o bind exclusivo falharia. O vigia de
        # saída e o atexit cobrem o caso de o encerramento travar.
        if not _desligamento_agendado:
            _remover_pid_file()
        raise KeyboardInterrupt  # waitress/werkzeug encerram o laço normalmente

    for nome in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sinal = getattr(signal, nome, None)
        if sinal is not None:
            with contextlib.suppress(ValueError, OSError):  # fora da thread principal etc.
                signal.signal(sinal, _ao_sinal)


def _boas_vindas(linhas: list[str]) -> None:
    """Mensagem de início: no log e, se houver console (não é pythonw), na tela."""
    for linha in linhas:
        log.info(linha)
        if sys.stdout is not None and sys.stderr is not None and not _log_no_console():
            with contextlib.suppress(Exception):
                print(linha)


def _log_no_console() -> bool:
    """True se o log já sai no console (evita mensagem duplicada na tela)."""
    return any(type(h) is logging.StreamHandler for h in logging.getLogger().handlers)


def _criar_socket_escuta(host: str, porta: int) -> socket.socket:
    """
    Cria o socket de escuta já com bind+listen. No Windows usa SO_EXCLUSIVEADDRUSE
    (SO_REUSEADDR permitiria dois servidores na mesma porta, com o 2º roubando
    conexões); nos outros sistemas, SO_REUSEADDR (que lá não permite isso).
    Falha com OSError se a porta estiver em uso.
    """
    familia, tipo, proto, _, endereco = socket.getaddrinfo(
        host, porta, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )[0]
    sock = socket.socket(familia, tipo, proto)
    try:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(endereco)
        sock.listen(1024)
    except BaseException:
        sock.close()
        raise
    return sock


def _sondar_porta_livre(host: str, porta: int) -> None:
    """
    Só para o fallback werkzeug (que cria o próprio socket com SO_REUSEADDR): no
    Windows tenta antes um bind exclusivo e falha com OSError se a porta estiver em uso.
    """
    if os.name != "nt":
        return
    familia, tipo, proto, _, endereco = socket.getaddrinfo(
        host, porta, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )[0]
    with socket.socket(familia, tipo, proto) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(endereco)


# --- Servidor de produção (waitress) ---------------------------------------------------------
CANAL_TIMEOUT_S = 60            # P03: conexão ociosa por mais que isto é fechada (long-poll = 25 s e é "em andamento")
CONEXOES_MAX = 400              # connection_limit: abaixo do FD_SETSIZE (512) do select no Windows
CONEXOES_REMOTAS_MAX = 300      # soma das conexões de aparelhos remotos: o resto é reservado ao loopback
CONEXOES_POR_IP_MAX = 64        # conexões simultâneas de UM aparelho remoto (um navegador usa ~6 por aba; vários
                                # celulares atrás do mesmo NAT/roteador da loja dividem um IP)


def criar_servidor_waitress(sock: socket.socket, *, por_ip: int = CONEXOES_POR_IP_MAX,
                            remotas_max: int = CONEXOES_REMOTAS_MAX,
                            ip_isento: Callable[[str], bool] = waitress_limites.ip_e_loopback) -> Any:
    """
    Cria o waitress com TODAS as defesas de transporte do Proclamare (a mesma função serve ao main() e
    aos testes de integração, que sobem o servidor de verdade):
      * threads altas: cada long-poll do YouTube e cada anúncio segura uma thread;
      * clear_untrusted_proxy_headers=False (P01): sem isto o waitress APAGAVA X-Forwarded-For/Host/Proto/
        Forwarded antes do Flask e a regra "cabeçalho de proxy tira o status de local" não funcionava.
        Sem `trusted_proxy`, nenhum deles é interpretado (remote_addr continua sendo o do socket);
      * channel_timeout 60 s e connection_limit 400 (P03), mais teto de conexões por IP remoto com o
        loopback isento (waitress_limites.instalar), SEMPRE instalado;
      * teto do corpo: o waitress recebe o corpo INTEIRO (em disco, acima de 512 KB) antes de a rota
        checar o PIN. Por isso o teto grande (envio de músicas) só vale, por rota, para quem é local ou
        tem sessão (waitress_limites); todo o resto fica em 64 KB. PROCLAMARE_MUSICAS_ENVIO=0 desliga
        o envio e volta tudo a 64 KB.
    """
    global MUSICAS_ENVIO_HABILITADO
    from waitress import create_server   # (ImportError aqui é tratado por quem chama; o main() já checou)
    waitress_limites.configurar_aviso(por_ip, remotas_max, CONEXOES_MAX)

    srv = create_server(
        app, sockets=[sock], threads=32, channel_timeout=CANAL_TIMEOUT_S, cleanup_interval=10,
        connection_limit=CONEXOES_MAX, clear_untrusted_proxy_headers=False,
        max_request_body_size=MAX_CORPO_ENVIO if MUSICAS_ENVIO_HABILITADO else TETO_CORPO_JSON,
        ident="proclamare",
    )
    com_corpo = MUSICAS_ENVIO_HABILITADO
    if not waitress_limites.instalar(
            srv, teto_pequeno=TETO_CORPO_JSON if com_corpo else None,
            permitir_grande=_corpo_grande_permitido if com_corpo else None,
            por_ip=por_ip, remotas_max=remotas_max, ip_isento=ip_isento):
        # sem o filtro por rota, o teto grande valeria para TODOS: melhor sem envio que inseguro
        srv.adj.max_request_body_size = TETO_CORPO_JSON
        MUSICAS_ENVIO_HABILITADO = False
        log.error("Esta versão do waitress não aceita os filtros por rota/conexão: o envio de "
                  "músicas pelo painel foi DESLIGADO (as músicas na pasta continuam valendo).")
    return srv


PORTAS_PROTEGIDAS = frozenset({8502, 8599})   # produção e o ambiente isolado de QA: testes nunca sobem nelas


def _recusa_sob_isolamento(host: str, porta: int) -> str | None:
    """
    Sob o isolamento dos testes (tests.isolamento.isolar() define PROCLAMARE_TESTE_ISOLADO=1), o servidor NÃO
    pode subir em porta de produção/QA nem em endereço de rede: importar `tests` e depois chamar main() jamais
    deve abrir o painel da loja na rede. Devolve o motivo da recusa ou None.
    """
    if os.environ.get("PROCLAMARE_TESTE_ISOLADO") != "1":
        return None
    if porta in PORTAS_PROTEGIDAS:
        return f"porta {porta} é de produção/QA"
    if host not in ("127.0.0.1", "localhost", "::1"):
        return "endereço de rede"
    return None


def main() -> None:
    _configurar_log()
    if _PORTA_INVALIDA:
        log.warning("PROCLAMARE_PORT inválida; usando a porta padrão 8502.")
    host = _env("HOST", "0.0.0.0").strip() or "0.0.0.0"
    motivo_recusa = _recusa_sob_isolamento(host, PORT)
    if motivo_recusa:
        log.error("Ambiente de testes isolado: o servidor se recusa a subir (%s).", motivo_recusa)
        sys.exit(2)
    limpar_audios_antigos(forcar=True)
    limpar_residuos_de_dados()

    _boas_vindas([
        f"Proclamare rodando — abra http://127.0.0.1:{PORT} neste computador",
        f"ou http://{ip_local()}:{PORT} de outro computador na mesma rede.",
    ])

    try:
        import waitress  # noqa: F401 - só confere que existe; criar_servidor_waitress() importa o que usa
    except ImportError:
        log.warning(
            "waitress não instalado (pip install waitress): usando o servidor de "
            "desenvolvimento do Flask, que não é recomendado para uso contínuo."
        )
        from werkzeug.serving import make_server

        def criar() -> Any:
            _sondar_porta_livre(host, PORT)  # servidor de desenvolvimento: só a sondagem protege
            return make_server(host, PORT, app, threaded=True)

        def rodar(srv: Any) -> None:
            srv.serve_forever()
    else:
        def criar() -> Any:
            # Socket próprio e EXCLUSIVO (sem janela entre a checagem e o bind).
            return criar_servidor_waitress(_criar_socket_escuta(host, PORT))

        def rodar(srv: Any) -> None:
            srv.run()

    # O bind acontece aqui; se falhar, sai com erro SEM tocar no pid file de outro servidor.
    try:
        servidor = criar()
    except OSError as exc:
        log.error("Não foi possível escutar em %r porta %s (%s). A porta já está em uso?",
                  host, PORT, exc.strerror or type(exc).__name__)
        sys.exit(1)

    global _servidor_ativo
    _servidor_ativo = servidor   # v2.5: a rota "Desligar" acorda e interrompe este servidor
    _instalar_encerramento()
    if _volume_pendente() is not None:   # o servidor caiu/fechou com o volume da música baixo
        _iniciar_reaplicacao()
    atexit.register(SOM.parar)
    _login_restaurar_do_disco()   # contador diário, trava e níveis de escalada voltam do disco (N5)
    _iniciar_vigia_tuneis()   # v2.7.1 (P06): portproxy/túnel para esta porta? (só leitura)
    SOM.iniciar()  # primeira varredura LOCAL em segundo plano (só lê; não toca som)
    atexit.register(ACERVO.parar)
    ACERVO.iniciar()  # cria a pasta de músicas e varre em segundo plano (thread "musicas-varredura")
    atexit.register(lambda: PROGRAMACAO.parar())
    PROGRAMACAO.iniciar()  # v2.6: relógio da programação + thread de execução (só tocam o que o dono marcou)
    caminho_pid = _caminho_pid_file()
    if caminho_pid is not None and not _gravar_pid_file(caminho_pid, PORT):
        log.error("Seguindo sem arquivo de PID (já registrado acima); o lançador pode não "
                  "conseguir desligar este servidor sozinho.")
    try:
        rodar(servidor)
    except KeyboardInterrupt:
        pass
    finally:
        _encerrar_servicos(servidor)


def _encerrar_servicos(servidor: Any) -> None:
    """
    Fim do main() (Ctrl+C, SIGTERM ou "Desligar" do painel). Ordem: fecha a porta (o lançador
    pode subir outro servidor assim que o PID sumir), para os serviços e, por último, remove o
    arquivo de PID. Tudo idempotente (o atexit repete SOM.parar/ACERVO.parar) e à prova de erro:
    uma falha aqui nunca pode impedir o processo de terminar.
    """
    close = getattr(servidor, "close", None) or getattr(servidor, "server_close", None)
    pool = _yt_titulos_pool
    passos: list[Callable[[], Any]] = [
        close if callable(close) else (lambda: None),
        _seg_flush_falhas,   # os PINs errados em lote ainda não gravados vão ao disco
        lambda: PROGRAMACAO.parar(),   # v2.6: para o relógio, corta o que tocava e grava o "visto"
        SOM.parar,   # desregistra o callback do Windows (também registrado no atexit)
        ACERVO.parar,
        (lambda: pool.shutdown(wait=False, cancel_futures=True)) if pool is not None else (lambda: None),
    ]
    for passo in passos:
        try:
            passo()
        except Exception:  # noqa: BLE001
            log.warning("Falha ao encerrar um serviço.", exc_info=True)
    _remover_pid_file()
    if _desligamento_agendado:
        log.info("Proclamare encerrado.")


def _processo_vivo(pid: int) -> bool:
    """True se existe um processo com este pid (outro que não o atual)."""
    if pid <= 0 or pid == os.getpid():
        return False
    if os.name == "nt":
        try:
            import ctypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = ctypes.c_void_p
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            try:
                codigo = ctypes.c_ulong()
                return bool(k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(codigo))) and codigo.value == 259
            finally:
                k32.CloseHandle(ctypes.c_void_p(h))
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True  # existe, mas sem permissão para sinalizar


def _servidor_no_ar() -> bool:
    """
    True se há um Proclamare rodando: pid file válido (PROCLAMARE_PID_FILE ou
    logs/proclamare.pid) com processo vivo, OU /api/status respondendo em
    127.0.0.1:<porta>. A CLI de PIN não deve gravar o config.json com o servidor no ar.
    """
    for caminho in (_caminho_pid_file(), APP_DIR / "logs" / "proclamare.pid"):
        if caminho is None:
            continue
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            if isinstance(dados, dict) and isinstance(dados.get("pid"), int) and _processo_vivo(dados["pid"]):
                return True
        except (OSError, ValueError):
            continue
    try:
        with requests.Session() as sessao:
            sessao.trust_env = False  # sem proxy do ambiente: o endereço é sempre local
            r = sessao.get(f"http://127.0.0.1:{PORT}/api/status", timeout=1.5)
            return r.status_code == 200 and isinstance(r.json(), dict) and "internet" in r.json()
    except (requests.RequestException, ValueError):
        return False


def _ler_pin(pergunta: str) -> str:
    """Lê o PIN sem eco no terminal; com stdin redirecionado (script/teste) lê uma linha."""
    if sys.stdin is not None and sys.stdin.isatty():
        return getpass.getpass(pergunta)
    linha = sys.stdin.readline() if sys.stdin is not None else ""
    if not linha:
        raise EOFError
    return linha.strip()


def _cli_pin(remover: bool) -> int:
    """
    CLI: `python server.py --definir-pin` (pede o PIN duas vezes, sem eco) ou
    `--remover-pin`. Funciona sem subir o servidor, com as mesmas funções de
    validação e gravação da API. Um servidor já rodando percebe a mudança sozinho.
    """
    if _servidor_no_ar():
        print("Desligue o Proclamare antes (Menu Iniciar → Desligar Proclamare) ou use as "
              "Configurações no PC da loja.")
        return 1
    try:
        if remover:
            aplicar_pin(None)
            print("PIN removido: o acesso pela rede está desligado e todas as sessões foram encerradas.")
            return 0
        pin = _ler_pin("Novo PIN (6 a 8 números): ")
        validar_pin_novo(pin)
        if _ler_pin("Repita o PIN: ") != pin:
            print("Os dois PINs não são iguais. Nada foi alterado.")
            return 1
        aplicar_pin(pin)
        print("PIN definido. Todos os aparelhos conectados precisarão digitar o PIN de novo.")
        return 0
    except ErroApi as exc:
        print(exc.mensagem)
        return 1
    except (OSError, EOFError, KeyboardInterrupt) as exc:
        print(f"Não foi possível concluir ({type(exc).__name__}).")
        return 1


if __name__ == "__main__":
    _args = sys.argv[1:]
    if _args == ["--definir-pin"] or _args == ["--remover-pin"]:
        sys.exit(_cli_pin(remover=_args[0] == "--remover-pin"))
    if _args:
        print("Uso: python server.py [--definir-pin | --remover-pin]   (sem argumentos: inicia o servidor)")
        sys.exit(2)
    main()
