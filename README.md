# PolícIA — serviço no OpenShift (ecossistema PF)

Central do PolícIA (API FastAPI + front nginx + túnel WebSocket) implantada seguindo o guia
**"Python — Serviço no OpenShift (padrão PF)"**: builds via Jenkins (`build_image`), deploy via
Helm chart `pf-deployment` (`deploy_<ambiente>`), Backstage em `configuracao/`.

## Estrutura

```
├── openshift/
│   ├── build.yaml        # BuildConfigs+ImageStreams (policia-api/tunel/web/postgres/pgbouncer)
│   └── banco.yaml        # Postgres+PgBouncer DEV (transitório, manual — fora do Helm)
├── configuracao/
│   ├── catalog-info.yaml         # System `policia` + Location (Backstage)
│   └── policia-{api,web,tunel}-info.yaml   # Components (Backstage)
├── docs/legado/          # manifests manuais substituídos (referência histórica)
└── Dockerfile.{api,web,postgres,pgbouncer}
```

Os **Helm values por ambiente** ficam no repositório separado `configuracao-ambientes`
(`ambientes/desenvolvimento/policia/policia-*.yaml`) — é o que o chart `pf-deployment` consome.

## Decisões de arquitetura (exceções documentadas)

- **Python**: justificado por OCR/visão/IA (deps científicas sem equivalente Java) —
  caso previsto no próprio guia ("código com deps científicas").
- **Docker strategy** (e não S2I `pf-python-311`): a API precisa de binários de sistema
  (`postgresql-client`, `ffmpeg`) que a imagem S2I não traz. `openshift/build.yaml` mantém
  `dockerStrategy` com os `Dockerfile.*`.
- **Repositório multisserviço**: 1 repo → 5 imagens (o `policia-tunel` reusa o `Dockerfile.api`
  com command `tunel_app:app`, via ImageStream próprio).
- **Sem Vault (por ora)**: segredos de DEV no bloco `secret:` do Helm values. **Bloqueante para
  hom/prod**: migrar para Vault/ExternalSecrets (`vault: "true"` no catalog-info) antes de subir.
- **Postgres no namespace (por ora)**: `openshift/banco.yaml` manual até o Postgres gerenciado
  ser provisionado; Service `postgres` é nome fixo esperado por `db_utils.DB_HOST`.

## Fluxo de deploy (esteira)

1. Push no GitLab/GitHub (branch `develop`).
2. Jenkins `<sistema>/<servico>/build_image` com `TARGET_VERSION=develop` → aplica
   `openshift/build.yaml` e builda as imagens (tag = versão, nunca `:latest`).
3. Jenkins `deploy_desenvolvimento` com `VERSAO=develop` → Helm `pf-deployment` com os values
   de `configuracao-ambientes` → Deployment+Service (+Route do web) em `pf-policia-dev`.
4. Banco (transitório): `oc apply -f openshift/banco.yaml` uma vez no namespace.
5. Estável → job `release` (testes + Sonar + tag) → promover hom/prod.

### Verificação rápida

```bash
R=$(oc get route policia-web -o jsonpath='{.spec.host}')
curl -k https://$R/login.html -o /dev/null -w '%{http_code}\n'   # 200
curl -k https://$R/api/health                                     # {"ok":true,...}
oc get pods -l app=policia                                        # api/web/tunel via Helm
```

## O `.env` (lido **e escrito** pela aplicação)

`api/funcoes/env_utils.py` faz read-modify-write num `.env`. No padrão novo o `command:` do
policia-api **materializa** as env vars do pod (bloco `secret:` do values) em
`/app/runtime/.env` (emptyDir) antes de iniciar o gunicorn.

**Consequência conhecida**: edições feitas na tela Administração valem até o pod reiniciar.
Para tornar permanente, altere o values (ou Vault, quando entrar) e redesploye.

Execução: `gunicorn -w $API_WORKERS -k uvicorn.workers.UvicornWorker -b 0.0.0.0:3000`
(padrão do guia; regra de workers `(2*CPU)+1` sobre `resources.limits.cpu`). Probes em `/health`
(alias de `/api/health`). O túnel roda `uvicorn tunel_app:app` :3001, **sempre 1 réplica**.

## Operação

- **Migrações**: `db/migracoes/*.sql` vão dentro da imagem; `migrar_utils` aplica no startup
  (a `startupProbe` da API tolera ~10 min de migração inicial).
- **Escala da API**: `autoscaling.replicaCount` no values — exige RWX nos volumes de dados
  (hoje RWO = 1 réplica). Confirmar com o SAS como montar PVCs extras no `pf-deployment`.
- **Nós de delegacia**: continuam fora do cluster; alcançam o túnel pelo path `/tunel/` da
  Route do `policia-web` (nginx faz proxy WebSocket para o Service `policia-tunel`).
- **Tiles OSM**: exige saída HTTPS do pod web para `tile.openstreetmap.org`.
- **Backups**: fora do cluster (`oc exec deployment/postgres -- pg_dump ...`) enquanto o
  banco for no namespace.

## Pendências para hom/prod

- [ ] Vault + ExternalSecrets (`vault: "true"` no catalog-info) e retirada dos segredos do values.
- [ ] Postgres gerenciado (demanda CITSmart) e remoção do `openshift/banco.yaml`.
- [ ] PVCs `data`/`logs`/`cache` via chart (validar suporte do `pf-deployment` com o SAS).
- [ ] Jobs Jenkins (`build_image`, `deploy_*`, `release`) criados pelo SAS.
- [ ] Confirmar `owner:` do catalog-info com a unidade dona.
