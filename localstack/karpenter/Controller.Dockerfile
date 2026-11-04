FROM docker.io/library/golang:1.24.2 AS build
WORKDIR /workspace
COPY . .
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -mod=readonly -trimpath -o /controller ./cmd/controller
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /controller /controller
ENTRYPOINT ["/controller"]
