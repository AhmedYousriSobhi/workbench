# The real NVIDIA dcgm-exporter on NVIDIA's mock NVML (issue 426, ADR 0025): genuine DCGM series with no GPU and no driver.
# The mock (github.com/NVIDIA/k8s-test-infra, Apache-2.0) is built here at a pinned commit (tag v0.6.0), never at container start.
FROM golang:1.27-bookworm AS mock
ARG MOCK_COMMIT=3007a01404ce6c85bdd16acea3072b4d7df18b1d
ENV GOFLAGS=-buildvcs=false
RUN git init -q /src && cd /src && git remote add origin https://github.com/NVIDIA/k8s-test-infra.git \
    && git fetch -q --depth 1 origin "$MOCK_COMMIT" && git checkout -q FETCH_HEAD
RUN cd /src/pkg/gpu/mocknvml && make NO_PADDING=1

FROM nvcr.io/nvidia/k8s/dcgm-exporter:4.4.2-4.7.0-ubuntu22.04
COPY --from=mock /src/pkg/gpu/mocknvml/libnvidia-ml.so* /opt/mocknvml/
COPY --from=mock /src/pkg/gpu/mocknvml/configs/ /opt/mocknvml/configs/
COPY --from=mock /src/LICENSE /opt/mocknvml/LICENSE
ENV LD_LIBRARY_PATH=/opt/mocknvml
