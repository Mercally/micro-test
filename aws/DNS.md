# DNS — `mercally.dev` en Route 53, lab en `aws.mercally.dev`

Decisión N16 (revisada). Hostinger no permite registros NS para subdominios, así que el
DNS de **todo `mercally.dev`** pasa a Route 53 (el dominio sigue registrado en Hostinger;
solo cambian sus nameservers). `mercally.com` no se toca. El stack 00 crea dos zonas:

| Zona | Contenido |
|---|---|
| `mercally.dev` (padre) | Registros previos copiados (A apex, `www`) + NS que delega `aws` |
| `aws.mercally.dev` (lab) | Todo lo del lab: validación ACM y `api-internal.aws.mercally.dev` (06) |

La delegación `aws` → zona del lab la crea la IaC dentro de Route 53; el único paso manual
es cambiar los nameservers en Hostinger. Motivo del dominio:
[ARQUITECTURA-C4.md § Dominio y certificado](diagrams/ARQUITECTURA-C4.md#dominio-y-certificado).

Estado verificado el 2026-11-19 (resolver 1.1.1.1):

| Comprobación | Resultado |
|---|---|
| Nameservers de `mercally.dev` | `ns1.dns-parking.com`, `ns2.dns-parking.com` (Hostinger) |
| Registros existentes | `mercally.dev A 161.97.122.48`, `www CNAME mercally.dev`; sin MX, TXT ni CAA |
| `aws.mercally.dev` | Libre |

`.dev` exige HTTPS en navegadores (HSTS preload). No afecta: todo el lab ya usa TLS.

Los bloques no llevan comentarios `#`: se pueden pegar tal cual en zsh.

## Paso 0 — Bajar la configuración anterior (`aws.mercally.com`)

Solo actúa si `micro-test-dns` existe con `aws.mercally.com`. Un stack ya creado con
`mercally.dev` **no** se borra: el paso 1 lo actualiza conservando su zona y nameservers.

```bash
OLD=aws.mercally.com
CURRENT=$(aws cloudformation describe-stacks --stack-name micro-test-dns \
  --query "Stacks[0].Outputs[?OutputKey=='ZoneName'].OutputValue" --output text 2>/dev/null)
if [ "$CURRENT" = "$OLD" ]; then
  aws cloudformation delete-stack --stack-name micro-test-dns
  aws cloudformation wait stack-delete-complete --stack-name micro-test-dns
fi
for ZONE in $(aws route53 list-hosted-zones-by-name --dns-name "$OLD" \
    --query "HostedZones[?Name=='$OLD.'].Id" --output text); do
  aws route53 delete-hosted-zone --id "$ZONE" && echo "zona $OLD borrada: $ZONE"
done
aws route53 list-hosted-zones --query "HostedZones[].[Name,Id]" --output table
```

La zona antigua tiene `DeletionPolicy: Retain`, por eso se borra aparte. En Hostinger no
queda nada que revertir.

## Paso 1 — Crear las zonas y copiar los registros actuales

```bash
aws cloudformation deploy --stack-name micro-test-dns \
  --template-file aws/cloudformation/00-dns.yaml

out() { aws cloudformation describe-stacks --stack-name micro-test-dns \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
DOMAIN=mercally.dev
PARENT_ID=$(out ParentHostedZoneId)

APEX_IP=$(dig @1.1.1.1 $DOMAIN A +short | head -1)
echo "apex A = $APEX_IP"
aws route53 change-resource-record-sets --hosted-zone-id "$PARENT_ID" --change-batch "{\"Changes\":[
  {\"Action\":\"UPSERT\",\"ResourceRecordSet\":{\"Name\":\"$DOMAIN\",\"Type\":\"A\",\"TTL\":300,\"ResourceRecords\":[{\"Value\":\"$APEX_IP\"}]}},
  {\"Action\":\"UPSERT\",\"ResourceRecordSet\":{\"Name\":\"www.$DOMAIN\",\"Type\":\"CNAME\",\"TTL\":300,\"ResourceRecords\":[{\"Value\":\"$DOMAIN\"}]}}]}"

out NameServers | tr ',' '\n'
```

`apex A =` debe mostrar `161.97.122.48`; si sale vacío, no ejecutar el UPSERT. La última
línea imprime los 4 nameservers para Hostinger.

Costo: ~$1/mes (2 zonas). Si en hPanel → DNS de `mercally.dev` hay más registros que
los listados arriba, copiarlos igual a la zona padre antes del paso 2.

## Paso 2 — Cambiar nameservers en Hostinger (único paso manual)

1. hPanel → **Dominios** → `mercally.dev` → **DNS / Nameservers** → **Cambiar nameservers**.
2. Elegir **nameservers personalizados** y **reemplazar** `ns1/ns2.dns-parking.com` por los
   4 del paso 1 (sin punto final). No dejar los de Hostinger mezclados.
3. Guardar. Desde ahora los registros de `mercally.dev` se editan en Route 53.

## Paso 3 — Verificar

```bash
dig @1.1.1.1 NS mercally.dev +short
dig @1.1.1.1 NS aws.mercally.dev +short
dig @1.1.1.1 mercally.dev A +short
LAB_ID=$(aws cloudformation describe-stacks --stack-name micro-test-dns \
  --query "Stacks[0].Outputs[?OutputKey=='HostedZoneId'].OutputValue" --output text)
CHECK='{"Name":"check.aws.mercally.dev","Type":"TXT","TTL":60,"ResourceRecords":[{"Value":"\"ok\""}]}'
aws route53 change-resource-record-sets --hosted-zone-id "$LAB_ID" \
  --change-batch "{\"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":$CHECK}]}"
sleep 30; dig @1.1.1.1 TXT check.aws.mercally.dev +short
aws route53 change-resource-record-sets --hosted-zone-id "$LAB_ID" \
  --change-batch "{\"Changes\":[{\"Action\":\"DELETE\",\"ResourceRecordSet\":$CHECK}]}"
```

Con `"ok"` la cadena Hostinger → `mercally.dev` → `aws.mercally.dev` funciona y el stack 06
valida ACM solo. No hace falta esperar para seguir con 01–05.

## Deshacer

1. Hostinger: volver a nameservers por defecto (`ns1/ns2.dns-parking.com`); revisar que
   su zona tenga el A y `www`.
2. Borrar stacks que crearon registros (06) y luego `micro-test-dns` (quita la delegación).
   Las dos zonas quedan retenidas: borrar sus registros propios (A/CNAME copiados) y luego
   `aws route53 delete-hosted-zone --id <ID>` para cada una.
