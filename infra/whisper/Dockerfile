# whisper.cpp HTTP server (MIT) for T1 Hindi/Hinglish ASR on CPU (ARM64 or x86_64).
# Base: Debian trixie, the same release as python:3.12-slim used by Dockerfile.worker.
# ASR image (D-46, PL-09): no media converter anywhere, base image included.
# The server reads 16 kHz mono WAV only. Voice notes (Ogg/Opus) are decoded by
# `opusdec` before the request, never inside this server.
# Only opusdec and opusenc are kept from the opus-tools package.
FROM debian:trixie-slim AS build
ARG WHISPER_CPP_REF=v1.7.6
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake git wget ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${WHISPER_CPP_REF} https://github.com/ggml-org/whisper.cpp /src
WORKDIR /src
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF -DGGML_OPENMP=OFF \
 && cmake --build build -j"$(nproc)" --config Release --target whisper-server

ARG WHISPER_MODEL
ARG WHISPER_MODEL_URL
ARG WHISPER_MODEL_SHA256
# AUDIT_NO_MODEL=1 is for the CI licence audit build only (image-audit.yml): it leaves an empty
# placeholder so the package manifest can be written without the model. The production build
# (docker-compose) never sets it, and then the URL and the sha256 are required and verified.
ARG AUDIT_NO_MODEL=0
RUN mkdir -p /models && if [ "${AUDIT_NO_MODEL}" = "1" ]; then \
      : > /models/model.bin; \
    else \
      test -n "${WHISPER_MODEL_URL}" || { echo "WHISPER_MODEL_URL is not set" >&2; exit 1; }; \
      test -n "${WHISPER_MODEL_SHA256}" || { echo "WHISPER_MODEL_SHA256 is not set" >&2; exit 1; }; \
      wget -q -O /models/model.bin "${WHISPER_MODEL_URL}" \
      && echo "${WHISPER_MODEL_SHA256}  /models/model.bin" | sha256sum -c -; \
    fi

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      opus-tools && rm -rf /var/lib/apt/lists/* \
 && find /usr/bin -maxdepth 1 -name 'opus*' ! -name opusdec ! -name opusenc -delete
COPY --from=build /src/build/bin/whisper-server /usr/local/bin/whisper-server
COPY --from=build /src/build/src/libwhisper.so* /src/build/ggml/src/libggml*.so* /usr/local/lib/
COPY --from=build /models/model.bin /models/model.bin
RUN ldconfig
EXPOSE 8080
CMD ["whisper-server", "--host", "0.0.0.0", "--port", "8080", "-m", "/models/model.bin", \
     "-l", "hi", "-t", "4"]
