# DNS — delegar `aws.mercally.com` de Hostinger a Route 53

Decisión N16. `mercally.com` permanece en Hostinger sin cambios; solo el subdominio
`aws.mercally.com` pasa a Route 53, donde la IaC crea el certificado ACM y el host
interno `api-internal.aws.mercally.com`. Ver motivo en
[ARQUITECTURA-C4.md § Dominio y certificado](diagrams/ARQUITECTURA-C4.md#dominio-y-certificado).

Estado verificado el 2026-11-19 (resolver público 1.1.1.1):

| Comprobación | Resultado |
|---|---|
| Nameservers de `mercally.com` | `ns1.dns-parking.com`, `ns2.dns-parking.com` (Hostinger) |
| Registros CAA de `mercally.com` | Ninguno: cualquier CA, incluida Amazon, puede emitir |
| `aws.mercally.com` | Sin registros: libre para delegar |

## Paso 1 — Crear la zona en Route 53

```bash
aws cloudformation deploy --region us-east-1 \
  --stack-name micro-test-dns \
  --template-file aws/cloudformation/00-dns.yaml \
  --parameter-overrides ZoneName=aws.mercally.com

aws cloudformation describe-stacks --region us-east-1 --stack-name micro-test-dns \
  --query "Stacks[0].Outputs[?OutputKey=='NameServers'].OutputValue" --output text
```

Devuelve 4 servidores, por ejemplo `ns-123.awsdns-45.com,ns-678.awsdns-12.net,...`
(los tuyos serán distintos). Costo: ~$0.50/mes.

## Paso 2 — Delegar en Hostinger (único paso manual)

1. hPanel → **Dominios** → `mercally.com` → **DNS / Nameservers** → **Administrar registros DNS**.
2. Agregar **4 registros**, uno por cada servidor del paso 1:

   | Tipo | Nombre | Apunta a | TTL |
   |---|---|---|---|
   | NS | `aws` | `ns-123.awsdns-45.com` | 3600 |
   | NS | `aws` | `ns-678.awsdns-12.net` | 3600 |
   | NS | `aws` | `ns-…awsdns-….org` | 3600 |
   | NS | `aws` | `ns-…awsdns-….co.uk` | 3600 |

   Nombre `aws` (Hostinger agrega `.mercally.com`). Sin punto final si el panel lo rechaza.
3. **No** cambiar los nameservers del dominio completo ni tocar otros registros.

Si el panel no ofrece el tipo NS para un subdominio, detenerse y avisar: hay que
buscar otra vía antes de continuar.

## Paso 3 — Verificar la delegación

```bash
dig @1.1.1.1 NS aws.mercally.com +short
```

Debe listar los 4 servidores `awsdns`. Suele tardar minutos; hasta unas horas según
caché. Prueba completa desde Route 53:

```bash
ZONE_ID=$(aws cloudformation describe-stacks --region us-east-1 --stack-name micro-test-dns \
  --query "Stacks[0].Outputs[?OutputKey=='HostedZoneId'].OutputValue" --output text)
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" --change-batch \
  '{"Changes":[{"Action":"UPSERT","ResourceRecordSet":{"Name":"check.aws.mercally.com","Type":"TXT","TTL":60,"ResourceRecords":[{"Value":"\"ok\""}]}}]}'
dig @1.1.1.1 TXT check.aws.mercally.com +short   # → "ok"
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" --change-batch \
  '{"Changes":[{"Action":"DELETE","ResourceRecordSet":{"Name":"check.aws.mercally.com","Type":"TXT","TTL":60,"ResourceRecords":[{"Value":"\"ok\""}]}}]}'
```

Con `"ok"` la delegación funciona y el stack 06 podrá validar ACM automáticamente.

## Deshacer

1. Hostinger: borrar los 4 registros NS `aws`.
2. Borrar registros que hayan creado otros stacks y luego el stack `micro-test-dns`.
   La zona tiene `DeletionPolicy: Retain`: eliminarla a mano en Route 53 si ya no se usa
   (`aws route53 delete-hosted-zone --id <ZONE_ID>`, requiere zona vacía salvo NS/SOA).
