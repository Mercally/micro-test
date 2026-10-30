# Diagramas del laboratorio

[network.mmd](network.mmd) es fuente Mermaid; [network.svg](network.svg) se regenera desde ella. [addressing.csv](addressing.csv) refleja los ocho CIDR y tablas de `01-network.yaml`.

Líneas a subredes indican ubicación posible: cada NG usa dos subredes con un nodo inicial; RDS Single-AZ ocupa una AZ aunque su DB subnet group incluya ambas. Servicios regionales EKS, SQS, Step Functions y S3 están fuera de las subredes del cliente. Sus ENI/endpoints dan acceso privado; SQL usa directamente endpoint privado de RDS.

Los tres nodos de aplicación representan el mismo micro y digest, con IAM/configuración distintos. Flechas discontinuas a SQS/Step Functions representan permisos y conectividad preparados, pero llamadas todavía no implementadas en la imagen. Host administrador es requisito manual pendiente. No se despliega otro micro ni worker. Flechas HTTP muestran comunicación permitida, no llamadas automáticas ya programadas.

Cada rol tiene base lógica y bucket propios. SG, NetworkPolicy strict e IAM restringen flujos; tablas separadas no aíslan por sí solas. No existen redes externas corporativas ni conexión a terceros reales.

Regeneración local, desde raíz:

```bash
npx --yes --package @mermaid-js/mermaid-cli@12.0.0 mmdc -i aws/diagrams/network.mmd -o aws/diagrams/network.svg -b white
```

Ver [README principal](../README.md) para bootstrap de CoreDNS, matriz de permisos y pruebas. Ningún diagrama acredita despliegue o pruebas reales en AWS.
