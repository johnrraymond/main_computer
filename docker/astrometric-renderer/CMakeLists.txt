cmake_minimum_required(VERSION 3.21)
project(MainComputerAstrometricRenderer LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(OpenGL REQUIRED)
find_package(GLEW REQUIRED)
find_package(glm REQUIRED)
find_package(JPEG REQUIRED)
find_package(Threads REQUIRED)

add_executable(astrometric-renderer
    src/astrometric_renderer.cpp
)

target_include_directories(astrometric-renderer PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${JPEG_INCLUDE_DIR}
)

target_link_libraries(astrometric-renderer PRIVATE
    OpenGL::GL
    GLEW::GLEW
    glm::glm
    JPEG::JPEG
    Threads::Threads
    EGL
)

target_compile_definitions(astrometric-renderer PRIVATE
    ASTROMETRIC_SHADER_DIR="${CMAKE_CURRENT_SOURCE_DIR}/shaders"
)
