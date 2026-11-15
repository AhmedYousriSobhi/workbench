# Per-node BMC forwarder (ADR 0018, issue 454): one static Go binary on scratch, no OS, no shell, about 2 MB resident.
FROM golang:1.27-bookworm AS build
WORKDIR /src
COPY forward.go .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /forward forward.go

FROM scratch
COPY --from=build /forward /forward
USER 65534:65534
ENV GOMAXPROCS=1 GOGC=50
ENTRYPOINT ["/forward"]
