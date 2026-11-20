#!/usr/bin/env bash
# Atualiza o GeoRural DataHub na VM: baixa a versao mais nova do back e do front,
# gera as imagens e reinicia so o que mudou. Uso: ./deploy.sh
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "Falta o .env. Rode: cp .env.example .env  e preencha." >&2
    exit 1
fi

set -a
. ./.env
set +a

: "${BACK_BRANCH:=dev}"
: "${FRONT_BRANCH:=dev}"
: "${OCI_CONFIG_DIR:=$HOME/.oci}"
export OCI_CONFIG_DIR

for var in SITE_URL AUTH_URL SPRING_DATASOURCE_URL DB_USERNAME DB_PASSWORD OCI_NAMESPACE OCI_BUCKET \
           KEYCLOAK_ADMIN_PASSWORD KEYCLOAK_DB_PASSWORD KEYCLOAK_BACKEND_SECRET; do
    if [ -z "${!var:-}" ]; then
        echo "Variavel $var vazia no .env." >&2
        exit 1
    fi
done

if [ "$KEYCLOAK_BACKEND_SECRET" = "georural-backend-dev-secret" ]; then
    echo "KEYCLOAK_BACKEND_SECRET ainda e o de desenvolvimento. Gere outro: openssl rand -hex 24" >&2
    exit 1
fi

if [ ! -f "$OCI_CONFIG_DIR/config" ]; then
    echo "Nao achei $OCI_CONFIG_DIR/config (o back precisa dele para o Object Storage)." >&2
    exit 1
fi

# src/ e so uma copia de trabalho do deploy: qualquer mudanca local la dentro e descartada.
atualizar() {
    local url=$1 pasta=$2 branch=$3
    if [ -d "$pasta/.git" ]; then
        git -C "$pasta" fetch --quiet origin "$branch"
        git -C "$pasta" checkout --quiet -B "$branch" "origin/$branch"
        git -C "$pasta" reset --quiet --hard "origin/$branch"
    else
        git clone --quiet --branch "$branch" "$url" "$pasta"
    fi
    echo "  $pasta @ $branch -> $(git -C "$pasta" log -1 --format='%h %s')"
}

echo "Baixando codigo..."
atualizar https://github.com/AtlazDB/GeoRural-DataHub-BackEnd.git src/backend "$BACK_BRANCH"
atualizar https://github.com/AtlazDB/GeoRural-DataHub-FrontEnd.git src/frontend "$FRONT_BRANCH"

# Realm de producao, a partir do de desenvolvimento do back: sai quem tem senha conhecida
# (operador, analista... com georural123) e o front passa a aceitar o endereco publico.
# So e importado na primeira subida do Keycloak; depois disso, mudancas sao pelo console.
echo "Gerando o realm do Keycloak..."
mkdir -p keycloak-import
python3 - src/backend/keycloak/georural-realm.json keycloak-import/georural-realm.json "$SITE_URL" <<'PY'
import json, sys

origem, destino, site = sys.argv[1], sys.argv[2], sys.argv[3].rstrip("/")
with open(origem, encoding="utf-8") as f:
    realm = json.load(f)

realm["users"] = [u for u in realm.get("users", [])
                  if u.get("username", "").startswith("service-account-")]

for client in realm["clients"]:
    if client["clientId"] == "georural-frontend":
        client["redirectUris"] = [site + "/*"]
        client["webOrigins"] = [site]
        client.setdefault("attributes", {})["post.logout.redirect.uris"] = site + "/*"

with open(destino, "w", encoding="utf-8") as f:
    json.dump(realm, f, ensure_ascii=False, indent=2)
PY

# Rede compartilhada com o Airflow (infra/airflow): o back chama o airflow-apiserver por ela.
docker network inspect georural >/dev/null 2>&1 || docker network create georural >/dev/null

echo "Gerando imagens (o site continua no ar)..."
docker compose build

echo "Subindo containers..."
docker compose up -d --remove-orphans

docker compose ps
echo
echo "Pronto. Logs do back: docker compose logs -f backend"
