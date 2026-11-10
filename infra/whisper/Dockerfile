# whisper.cpp HTTP server (MIT) for T1 Hindi/Hinglish ASR on CPU (ARM64 or x86_64).
# ASR image (D-46, PL-09): no media converter anywhere, base image included.
# The server reads 16 kHz mono WAV only. Voice notes (Ogg/Opus) are decoded by
# `opusdec` before the request, never inside this server.
# Only opusdec and opusenc are kept from the opus-tools package.
FROM debian:bookworm-slim AS build
ARG WHISPER_CPP_REF=v1.7.6
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${WHISPER_CPP_REF} https://github.com/ggml-org/whisper.cpp /src
WORKDIR /src
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF -DGGML_OPENMP=OFF \
 && cmake --build build -j"$(nproc)" --config Release --target whisper-server

FROM debian:bookworm-slim
ARG WHISPER_MODEL
ARG WHISPER_MODEL_URL
RUN apt-get update && apt-get install -y --no-install-recommends \
      opus-tools wget ca-certificates && rm -rf /var/lib/apt/lists/* \
 && find /usr/bin -maxdepth 1 -name 'opus*' ! -name opusdec ! -name opusenc -delete
COPY --from=build /src/build/bin/whisper-server /usr/local/bin/whisper-server
COPY --from=build /src/build/src/libwhisper.so* /src/build/ggml/src/libggml*.so* /usr/local/lib/
RUN ldconfig && mkdir -p /models \
 && wget -q -O /models/model.bin \
      "${WHISPER_MODEL_URL}"
EXPOSE 8080
CMD ["whisper-server", "--host", "0.0.0.0", "--port", "8080", "-m", "/models/model.bin", \
     "-l", "hi", "-t", "4"]
