FROM nvidia/opengl:1.2-glvnd-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       cmake \
       curl \
       g++ \
       libegl1-mesa-dev \
       libglew-dev \
       libglm-dev \
       libjpeg-dev \
       libglvnd-dev \
       make \
       pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/astrometric-renderer
COPY CMakeLists.txt ./
COPY src ./src
COPY shaders ./shaders
COPY upstream ./upstream

RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --config Release --parallel

EXPOSE 8794
CMD ["./build/astrometric-renderer"]
