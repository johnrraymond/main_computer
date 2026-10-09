FROM eclipse-temurin:25-jdk AS build

ARG BESU_REF=26.9.0
ARG BESU_EXPECTED_COMMIT_PREFIX=ee9c64c

RUN apt-get update \
    && apt-get install -y --no-install-recommends git python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${BESU_REF}" --recurse-submodules \
      https://github.com/besu-eth/besu.git /src \
    && cd /src \
    && test "$(git rev-parse HEAD | cut -c1-7)" = "${BESU_EXPECTED_COMMIT_PREFIX}"

COPY NativeMintPrecompiledContract.java /tmp/NativeMintPrecompiledContract.java
COPY patch_besu.py /tmp/patch_besu.py
RUN python3 /tmp/patch_besu.py /src /tmp/NativeMintPrecompiledContract.java

WORKDIR /src
RUN ./gradlew --no-daemon installDist -x test

FROM eclipse-temurin:25-jre
COPY --from=build /src/build/install/besu /opt/besu
ENV PATH="/opt/besu/bin:${PATH}"
ENTRYPOINT ["/opt/besu/bin/besu"]
