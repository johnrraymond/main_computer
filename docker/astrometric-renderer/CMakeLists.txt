cmake_minimum_required(VERSION 3.21)
project(MainComputerAstrometricRenderer LANGUAGES CXX CUDA)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CUDA_STANDARD 17)
set(CMAKE_CUDA_STANDARD_REQUIRED ON)

# Keep the image usable on common NVIDIA cards without needing GPU access
# during docker build.  The final virtual PTX target gives some forward
# compatibility for newer cards.
if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES)
    set(CMAKE_CUDA_ARCHITECTURES 52 61 75 86 89 89-virtual)
endif()

find_package(CUDAToolkit REQUIRED)
find_package(JPEG REQUIRED)
find_package(Threads REQUIRED)

add_executable(astrometric-renderer
    src/astrometric_renderer.cu
)

target_include_directories(astrometric-renderer PRIVATE
    ${JPEG_INCLUDE_DIR}
)

target_link_libraries(astrometric-renderer PRIVATE
    CUDA::cudart
    JPEG::JPEG
    Threads::Threads
)

target_compile_options(astrometric-renderer PRIVATE
    $<$<COMPILE_LANGUAGE:CUDA>:--use_fast_math>
)
