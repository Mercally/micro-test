# Angular sales UI

Angular UI for submitting purchases and checking their asynchronous status. The
JWT authorizer is a Lambda attached to API Gateway; the browser never receives its
signing key. The JWT field is memory-only and should contain the short-lived token
generated in `localstack/.state/business/purchase.http` (`@token`, without `Bearer`).

## Run from WSL

Requirements: Node.js 20.19+ or 22.12+, npm, a running LocalStack lab, and a
valid generated `purchase.http` token.

```bash
./localstack/lab http
npm --prefix frontend ci
npm --prefix frontend start
```

Run the first command from the repo root to generate a fresh one-hour practice JWT.
Then open `http://127.0.0.1:4200`. Copy `@baseUrl` and `@token` from the generated
`localstack/.state/business/purchase.http` into the connection fields. The
development proxy forwards `/gateway/*` to LocalStack on `127.0.0.1:4566`;
this avoids browser CORS and does not expose LocalStack on the LAN.

The API has no list endpoint, so the master table is this browser's local list of
sales created through the UI. Sale IDs, references and statuses are stored in
localStorage. The JWT is not persisted. Refresh a row to check its current state.

API Gateway's Lambda authorizer validates the JWT signature, issuer, audience,
subject and expiration. API Gateway forwards the original JWT and validated
`context.authorizer.subject` as `X-Authenticated-User` to the microservice.
The microservice can consume the verified subject without receiving the signing key.

## Deploy to LocalStack S3

After syncing the repo to the WSL lab laptop, install Node.js/npm there and run
from the repository root:

```bash
npm --prefix frontend ci
localstack/.venv/bin/python localstack/frontend/frontend.py deploy \
  --endpoint http://127.0.0.1:4566
localstack/.venv/bin/python localstack/frontend/frontend.py status \
  --endpoint http://127.0.0.1:4566
```

`frontend` builds Angular, creates the owned `micro-test-local-frontend` S3 bucket
and uploads the static files. It refuses to publish unless the business API has
the `purchase-jwt` Lambda authorizer attached to the create/status routes. The
business stack provisions that authorizer and its Lambda; this frontend deployment
does not replace or expose the signing secret.

Open the `frontendUrl` printed by the command (normally
`http://127.0.0.1:4566/micro-test-local-frontend/index.html`). This uses the
LocalStack path-style S3 object URL on the same origin as API Gateway, so the app
can call the authenticated API without a CORS proxy. Copy `@baseUrl` and `@token`
from `localstack/.state/business/purchase.http` into the UI.

The bucket permits public reads for local static files only; LocalStack must
remain bound to loopback as configured by the lab. Do not copy this bucket policy
to AWS. Deployment builds Angular with the S3 object path as its base href so
the JavaScript and CSS assets load from the same bucket prefix.

`frontend-status` verifies the S3 object, deployed API and authorizer connection
without rebuilding or uploading. Deployment is opt-in and does not add Node.js
as a prerequisite to `./localstack/lab up`.

### Open it from another computer

Do not expose LocalStack port 4566 on the LAN. From the computer where you want
to use the browser, open a terminal and keep this SSH tunnel running:

```bash
ssh -N -L 4567:127.0.0.1:4566 josue@192.168.1.16
```

Then open `http://127.0.0.1:4567/micro-test-local-frontend/index.html`. The UI
routes API requests through this same-origin tunnel. Keep the tunnel open while
testing and close it afterward; it also provides access to other LocalStack APIs.

For the future AWS deployment and CloudWatch inventory, see
[frontend and observability](../aws/FRONTEND-OBSERVABILITY.md). The AWS design is
not deployed; the file distinguishes the LocalStack setup from the AWS target.
