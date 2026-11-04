FROM docker.io/library/golang:1.24.2 AS build
WORKDIR /workspace
COPY . .
# CAPD upstream binds its workload load balancer to all host interfaces.
# Limit this dedicated lab build to loopback; do not change Docker or firewall.
RUN test "$(grep -c 'listenAddr := "0.0.0.0"' test/infrastructure/docker/internal/docker/loadbalancer.go)" = 1 \
 && sed -i 's/listenAddr := "0.0.0.0"/listenAddr := "127.0.0.1"/;s/listenAddr = "::"/listenAddr = "::1"/' test/infrastructure/docker/internal/docker/loadbalancer.go
WORKDIR /workspace/test/infrastructure/docker
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -mod=readonly -trimpath -o /manager .
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /manager /manager
# Docker infrastructure controller requires its management node's Docker socket.
USER 0
ENTRYPOINT ["/manager"]
