# Política de segurança

## Como relatar uma falha (em particular)

**Não abra uma issue pública com detalhes de uma falha de segurança**: isso expõe as lojas antes da correção.

1. **Preferido:** na página do repositório no GitHub, vá em **Security** > **Advisories** >
   **Report a vulnerability** e descreva o que aconteceu, como reproduzir e o impacto.
2. **Se esse botão não aparecer** (o relato privado é uma configuração do repositório que o dono precisa ligar e
   pode ainda estar desligada): abra uma issue pública **sem nenhum detalhe técnico**, só com o título
   "Contato de segurança" e uma forma de falar com você (por exemplo, "responda aqui pedindo um canal
   privado"). O mantenedor responde e combina um canal privado antes de você enviar qualquer detalhe.
3. Em qualquer caso, **não inclua** PIN, `config.json`, Client Secret do Spotify, IPs reais da loja nem
   texto de anúncios de verdade.

## O que esperar

- Resposta inicial em até **7 dias** e, quando a falha for confirmada, previsão de correção em até
  **30 dias** (prazos de boa-fé: este é um projeto mantido por uma pessoa).
- O seu nome é citado no aviso da correção, se você quiser.
- Pedimos que a falha só seja divulgada depois da correção.

## Versões suportadas

| Versão | Suporte |
|---|---|
| Última versão da branch `main` (série 2.7.x) | Recebe correções de segurança |
| Versões anteriores (2.6 e abaixo) | Sem suporte: atualize |

## Escopo

Vale relatar: contorno do PIN ou do controle de "PC local", acesso a arquivos fora da pasta de músicas,
vazamento de segredos (`config.json`, Client Secret, tokens), execução de código, negação de serviço
sem PIN, falhas no instalador/lançador.

Limitações conhecidas (não são falhas):

- O painel fala **HTTP** (sem criptografia) na rede da loja; para HTTPS use um proxy conforme o
  `docs/GUIA.md` (e `PROCLAMARE_CONFIAR_LOOPBACK=0`). Quem está na mesma Wi-Fi pode ver o tráfego.
- O PC da loja (loopback) é administrador do painel por decisão de projeto: qualquer programa ou
  conta do próprio PC controla o painel sem PIN. No modo padrão (`PROCLAMARE_CONFIAR_LOOPBACK=auto`)
  o Proclamare detecta **alguns** encaminhamentos (`netsh portproxy`, e `cloudflared`/`ngrok`/`ssh`
  com a porta na linha de comando); ao achar, o PC deixa de ser livre: com PIN passa a pedir o PIN
  também, e sem PIN o acesso fica bloqueado até fechar o encaminhamento (volta em até 1 minuto) ou definir o PIN. `PROCLAMARE_CONFIAR_LOOPBACK=1` dá acesso total, sem PIN, a quem entra pelo encaminhamento. A vigia também avisa quando não consegue ler um processo suspeito.
  **Não são detectados** `cloudflared` como serviço, `tailscale serve` e proxies (nginx/Caddy):
  nesses casos use `PROCLAMARE_CONFIAR_LOOPBACK=0`.
- A pasta do programa precisa estar protegida pelo sistema (o instalador recusa pastas graváveis por
  todos e recomenda `%LOCALAPPDATA%\Programs\Proclamare`). Se você liberar a pasta para outros usuários
  por conta própria, é risco aceito, fora do escopo.
- Atrás de NAT/repetidor Wi-Fi, vários aparelhos aparecem com o mesmo IP (limites de PIN e de conexões
  valem para o conjunto).
- Os bloqueios de login por aparelho escalam e sobrevivem a reinício; o da faixa de rede é fixo (10 min) e não vale para aparelhos que já entraram com PIN nos últimos 90 dias.
- Quem tem o PIN pode anunciar, tocar e programar horários (é o que o PIN autoriza).

Para o que fazer se o PIN vazar ou um aparelho for perdido, veja "Se algo der errado (segurança)"
em `docs/GUIA.md`.
