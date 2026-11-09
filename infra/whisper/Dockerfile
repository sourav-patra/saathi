# whisper.cpp HTTP server (MIT) for T1 Hindi/Hinglish ASR on CPU (ARM64 or x86_64).
# ffmpeg is installed as an external program, only to convert OGG/Opus voice
# notes (--convert). It is never linked into Saathi's code.
FROM debian:bookworm-slim AS build
ARG WHISPER_CPP_REF=v1.7.6
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${WHISPER_CPP_REF} https://github.com/ggml-org/whisper.cpp /src
WORKDIR /src
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF \
 && cmake --build build -j"$(nproc)" --config Release --target whisper-server

FROM debian:bookworm-slim
ARG WHISPER_MODEL
ARG WHISPER_MODEL_URL
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg wget ca-certificates libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/build/bin/whisper-server /usr/local/bin/whisper-server
COPY --from=build /src/build/src/libwhisper.so* /src/build/ggml/src/libggml*.so* /usr/local/lib/
RUN ldconfig && mkdir -p /models \
 && wget -q -O /models/model.bin \
      "${WHISPER_MODEL_URL}"
EXPOSE 8080
CMD ["whisper-server", "--host", "0.0.0.0", "--port", "8080", "-m", "/models/model.bin", \
     "--convert", "-l", "hi", "-t", "4"]
