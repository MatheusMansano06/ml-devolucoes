# BIBLIA POS-VENDA ML (FONTE DE VERDADE)

Data de consolidacao: 2026-05-27
Escopo: Integracao de devolucoes e reclamacoes (Post Purchase API) do Mercado Livre.

## Regra Mestra

Toda correcao, feature, regra de classificacao e automacao deste projeto deve seguir esta biblia como referencia principal.
Quando houver duvida, priorizar:

1. endpoint oficial atual (`/post-purchase/...`)
2. `available_actions` no claim
3. notificacoes `post_purchase` (`claims` e `claims_actions`)

## Endpoints Canonicos

1. Consultar claim:
   - `GET /post-purchase/v1/claims/{claim_id}`
2. Buscar claims:
   - `GET /post-purchase/v1/claims/search`
3. Consultar devolucao do claim:
   - `GET /post-purchase/v2/claims/{claim_id}/returns`
4. Consultar reviews da devolucao:
   - `GET /post-purchase/v1/returns/{return_id}/reviews`
5. Enviar revisao unificada (OK/falha):
   - `POST /post-purchase/v1/returns/{return_id}/return-review`
6. Resolucoes esperadas:
   - `GET /post-purchase/v1/claims/{claim_id}/expected-resolutions`
7. Oferecer reembolso parcial:
   - `POST /post-purchase/v1/claims/{claim_id}/expected-resolutions/partial-refund`
8. Reembolso total:
   - `POST /post-purchase/v1/claims/{claim_id}/expected-resolutions/refund`
9. Permitir devolucao:
   - `POST /post-purchase/v1/claims/{claim_id}/expected-resolutions/allow-return`

## Regras e Limitacoes Obrigatorias

1. Nao usar endpoints legados sem `/post-purchase`.
2. `partial-refund` nao equivale a 100% (total refund usa endpoint proprio).
3. Nem toda acao esta sempre disponivel: validar `available_actions` antes de agir.
4. `GET /returns/{return_id}/reviews` pode retornar 404 quando nao houver review; antes verificar `related_entities` com `reviews`.
5. Busca de claims precisa filtros de negocio; evitar consulta ampla so com `status=opened`.
6. Integracao deve tratar throttling e indisponibilidade (`429`, `502`, `503`, `504`) com retry/backoff.
7. Notificacoes `post_purchase` sao obrigatorias para near real-time.
8. O `resource` da notificacao pode vir com prefixo `/post-purchase/...` e a integracao deve aceitar esse formato.
9. Fluxo real depende do tipo de caso (ex.: PNR/PDD), status, stage e regras de logistica.

## Politica de Implementacao no Projeto

1. Qualquer mudanca em classificacao/fila deve ser validada contra esta biblia.
2. Se documentacao oficial mudar, atualizar este arquivo antes de alterar regra de negocio.
3. Em code review, divergencia com esta biblia bloqueia merge.

## Fontes Oficiais (base da biblia)

1. https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/gerenciar-resolucao-de-reclamacoes
2. https://developers.mercadolibre.com.ar/que-es-un-reclamo
3. https://developers.mercadolibre.com.ar/en_us/product-identifiers/ml-returns
4. https://developers.mercadolibre.com.ar/es_ar/gestionar-devoluciones
5. https://developers.mercadolibre.com.ar/es_ar/trabajar-con-reclamos/productos-recibe-notificaciones
6. https://developers.mercadolibre.com.ar/es_ar/errores
