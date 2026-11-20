# Deploy da aplicacao

GeoRural DataHub (back + front + Keycloak) em `docker compose`, na mesma VM da OCI que roda o
Airflow, dentro de `~/georural-app`. O banco e o **Autonomous Database** da OCI, o mesmo que o
Airflow usa. O deploy e automatico: todo merge na `dev` do back ou do front dispara o workflow
**Deploy** no GitHub Actions, que entra na VM por SSH e roda o `./deploy.sh`. Tambem da para
rodar na mao, de dentro da VM.

```
                 443 (HTTPS, Caddy no host)
   georural.<dominio> ──> :8081 nginx (front) ──/api──> backend :8080 ──> Autonomous Database
   auth.<dominio>     ──> :8180 Keycloak ──> Postgres     │  │
                                                          │  └──> Object Storage (zona bruta)
                                rede Docker "georural" ───┴──> airflow-apiserver (infra/airflow)
```

Nada do compose escuta para fora: front, back e Keycloak ficam em `127.0.0.1` e o Caddy publica
em HTTPS. Na Security List da OCI ficam abertas so as portas 22, 80 e 443.

## Arquivos

| Arquivo | O que e |
|---|---|
| `docker-compose.yaml` | Back, front, Keycloak e o Postgres do Keycloak |
| `backend.Dockerfile` | Build do Spring Boot com Java 17 |
| `frontend.Dockerfile` | Build do Vue + nginx (recebe o endereco do Keycloak no build) |
| `nginx.conf` | Serve o front e repassa `/api` para o back |
| `deploy.sh` | Baixa o codigo, gera o realm do Keycloak, cria a rede e sobe tudo |
| `.env.example` | Modelo do `.env`. O `.env` real fica so na VM |

O codigo do back e do front **nao** vem daqui: o `deploy.sh` clona os repositorios
em `src/` (fora do git) na branch definida no `.env`.

## Dar acesso a alguem do grupo

Cada pessoa gera uma chave SSH no proprio PC (`ssh-keygen -t ed25519`) e manda so a
parte **publica** (`~/.ssh/id_ed25519.pub`). Quem ja tem acesso adiciona na VM:

```bash
echo "ssh-ed25519 AAAA... nome-da-pessoa" >> ~/.ssh/authorized_keys
```

## Primeira vez (uma pessoa so)

**1. Pre-requisitos na OCI.** Autonomous Database com o IP da VM na lista de acesso e o mTLS
desligado, usuario `GEO_RURAL` criado, bucket da zona bruta e o Airflow ja no ar
(`infra/airflow/README.md`).

**2. Credencial da OCI.** O back usa `~/.oci/config` + chave API (perfil `DEFAULT`). Use um
usuario tecnico da OCI, nunca a chave de uma pessoa. Precisa existir na VM, no usuario que roda
o deploy:

```bash
ls ~/.oci          # config e o .pem
chmod 600 ~/.oci/*
```

Se o `config` veio de um PC Windows, corrija o `key_file`: `C:\Users\...\oci_api_key.pem`
nao existe na VM e o back nao sobe (`Could not find private key`). Use
`key_file=~/.oci/oci_api_key.pem`.

**3. Dominio e HTTPS.** O Keycloak so aceita login por HTTPS fora de `localhost`. Sem dominio
proprio, use o sslip.io (`georural.<IP-com-tracos>.sslip.io`), que nao precisa de configuracao.
Em `/etc/caddy/Caddyfile`:

```
georural.<dominio> {
    reverse_proxy localhost:8081
}
auth.<dominio> {
    reverse_proxy localhost:8180
}
```

```bash
sudo systemctl reload caddy      # o certificado sai sozinho (portas 80 e 443 abertas)
```

**4. Baixar e configurar:**

```bash
git clone https://github.com/AtlazDB/GeoRural-DataHub.git ~/georural-repo
ln -s ~/georural-repo/infra/app ~/georural-app
cd ~/georural-app
cp .env.example .env
nano .env          # cada variavel diz de onde tirar o valor
chmod 600 .env
```

**5. Subir:**

```bash
./deploy.sh
docker compose logs -f backend   # espere "Started GeoRuralApplication"
```

Na primeira vez o Flyway cria as tabelas no `GEO_RURAL`. Depois disso, de os GRANTs ao usuario
do Airflow, se ele for separado.

**6. Primeiro usuario.** O realm de producao nasce **sem** os usuarios de teste do back
(`operador`, `gestor`... com senha conhecida). Entre em `https://auth.<dominio>` com
`KEYCLOAK_ADMIN`, troque para o realm **georural**, crie o seu usuario em *Users* e de o perfil
`GESTOR` em *Role mapping*. Os demais usuarios podem ser criados pela propria aplicacao.

Pronto: `https://georural.<dominio>`.

## Atualizar (qualquer pessoa do grupo)

O GitHub Actions faz isso a cada merge na `dev`. Na mao:

```bash
ssh usuario@IP_PUBLICO_DA_VM
cd ~/georural-repo && git pull     # so se mudou algo em infra/app
cd ~/georural-app && ./deploy.sh
```

O `build` roda com o site no ar. A indisponibilidade e so o reinicio do back
(~30 s).

## Comandos uteis

Sempre de dentro de `~/georural-app`:

```bash
docker compose ps                  # o que esta rodando
docker compose logs -f backend     # logs do back
docker compose logs -f keycloak    # logs do login
docker compose restart backend     # reinicia so o back
```

## Armadilhas conhecidas

- **O realm so e importado na primeira subida do Keycloak.** Mudancas no
  `keycloak/georural-realm.json` do back nao chegam sozinhas a producao: faca pelo console.
  O mesmo vale para `KEYCLOAK_ADMIN_PASSWORD` e `KEYCLOAK_BACKEND_SECRET` depois da primeira vez.
- **`docker compose down -v` apaga os usuarios do Keycloak.** Use `down` sem `-v`.
- **`SITE_URL` e `AUTH_URL` precisam bater com o Caddyfile**, com `https://` e sem barra no fim.
  O back recusa tokens cujo emissor nao seja `AUTH_URL/realms/georural`.
- **Trocar o `AUTH_URL` exige novo build do front**, porque o endereco do Keycloak vai para o
  JavaScript no build. O `./deploy.sh` ja faz isso.
- **A VM usa Instance Principal, mas o back nao.** O Airflow autentica na OCI sem
  chave em disco; o back so sabe ler `~/.oci/config`. Por isso o passo 2.

## Pendencias

- [ ] Fazer o back aceitar Instance Principal (fallback quando nao houver
      `~/.oci/config`), para nao depender de chave em disco na VM.
- [ ] Backup periodico do schema `GEO_RURAL` (Data Pump) e do banco do Keycloak fora da OCI.
