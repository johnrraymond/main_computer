#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/glew.h>
#include <jpeglib.h>

#include <glm/glm.hpp>
#include <glm/gtc/constants.hpp>
#include <glm/gtc/matrix_transform.hpp>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cerrno>
#include <cstdlib>
#include <csignal>
#include <cstring>
#include <fstream>
#include <functional>
#include <iostream>
#include <mutex>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr double kSagittariusARs = 1.269e10;
constexpr float kDefaultFovDegrees = 60.0f;

std::atomic<bool> g_running{true};

int envInt(const char* name, int fallback, int minimum, int maximum) {
    const char* raw = std::getenv(name);
    if (!raw || !*raw) return fallback;
    try {
        int value = std::stoi(raw);
        return std::max(minimum, std::min(maximum, value));
    } catch (...) {
        return fallback;
    }
}

float envFloat(const char* name, float fallback, float minimum, float maximum) {
    const char* raw = std::getenv(name);
    if (!raw || !*raw) return fallback;
    try {
        float value = std::stof(raw);
        return std::max(minimum, std::min(maximum, value));
    } catch (...) {
        return fallback;
    }
}

std::string envString(const char* name, const std::string& fallback) {
    const char* raw = std::getenv(name);
    return raw && *raw ? std::string(raw) : fallback;
}

std::string readFile(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("Unable to read shader: " + path);
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

std::string jsonEscape(const std::string& value) {
    std::ostringstream out;
    for (char c : value) {
        switch (c) {
            case '\\': out << "\\\\"; break;
            case '"': out << "\\\""; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    out << "\\u" << std::hex << int(static_cast<unsigned char>(c));
                } else {
                    out << c;
                }
        }
    }
    return out.str();
}

double jsonNumber(const std::string& body, const std::string& key, double fallback = 0.0) {
    try {
        std::regex pattern("\"" + key + "\"\\s*:\\s*(-?(?:[0-9]+\\.?[0-9]*|\\.[0-9]+)(?:[eE][-+]?[0-9]+)?)");
        std::smatch match;
        if (std::regex_search(body, match, pattern)) {
            return std::stod(match[1].str());
        }
    } catch (...) {
    }
    return fallback;
}

std::string jsonStringValue(const std::string& body, const std::string& key, const std::string& fallback = "") {
    try {
        std::regex pattern("\"" + key + "\"\\s*:\\s*\"([^\"]*)\"");
        std::smatch match;
        if (std::regex_search(body, match, pattern)) {
            return match[1].str();
        }
    } catch (...) {
    }
    return fallback;
}

bool jsonBool(const std::string& body, const std::string& key, bool fallback = false) {
    try {
        std::regex pattern("\"" + key + "\"\\s*:\\s*(true|false|1|0)");
        std::smatch match;
        if (std::regex_search(body, match, pattern)) {
            std::string value = match[1].str();
            return value == "true" || value == "1";
        }
    } catch (...) {
    }
    return fallback;
}

bool sendAll(int fd, const void* data, size_t size) {
    const char* ptr = static_cast<const char*>(data);
    while (size > 0) {
        ssize_t sent = ::send(fd, ptr, size, MSG_NOSIGNAL);
        if (sent <= 0) return false;
        ptr += sent;
        size -= static_cast<size_t>(sent);
    }
    return true;
}

bool sendString(int fd, const std::string& text) {
    return sendAll(fd, text.data(), text.size());
}

struct Camera {
    glm::vec3 target{0.0f, 0.0f, 0.0f};
    float radius = 6.34194e10f;
    float minRadius = 1.45e10f;
    float maxRadius = 8.0e11f;
    float azimuth = 0.0f;
    float elevation = glm::pi<float>() / 2.0f;
    float orbitSpeed = 0.0065f;
    float panSpeed = 0.0012f;
    float zoomSpeed = 1.075f;
    float fovDegrees = kDefaultFovDegrees;

    glm::vec3 position() const {
        return {
            target.x + radius * std::sin(elevation) * std::cos(azimuth),
            target.y + radius * std::cos(elevation),
            target.z + radius * std::sin(elevation) * std::sin(azimuth),
        };
    }

    glm::vec3 forward() const {
        return glm::normalize(target - position());
    }

    glm::vec3 right() const {
        glm::vec3 f = forward();
        glm::vec3 r = glm::cross(f, glm::vec3(0, 1, 0));
        if (glm::length(r) < 1e-5f) return glm::vec3(1, 0, 0);
        return glm::normalize(r);
    }

    glm::vec3 up() const {
        return glm::normalize(glm::cross(right(), forward()));
    }

    void orbit(float dx, float dy) {
        azimuth -= dx * orbitSpeed;
        elevation -= dy * orbitSpeed;
        elevation = std::max(0.01f, std::min(glm::pi<float>() - 0.01f, elevation));
    }

    void pan(float dx, float dy) {
        target += -right() * dx * panSpeed * radius + up() * dy * panSpeed * radius;
    }

    void zoom(float wheelDelta) {
        float steps = std::max(-12.0f, std::min(12.0f, wheelDelta / 100.0f));
        if (steps > 0.0f) {
            radius *= std::pow(zoomSpeed, steps);
        } else if (steps < 0.0f) {
            radius /= std::pow(zoomSpeed, -steps);
        }
        radius = std::max(minRadius, std::min(maxRadius, radius));
    }

    void reset() {
        target = glm::vec3(0.0f);
        radius = 6.34194e10f;
        azimuth = 0.0f;
        elevation = glm::pi<float>() / 2.0f;
        fovDegrees = kDefaultFovDegrees;
    }
};

struct SharedState {
    std::mutex mutex;
    std::condition_variable cv;
    Camera camera;
    bool moving = true;
    bool dirty = true;
    int width = 640;
    int height = 360;
    int fps = 10;
    int jpegQuality = 86;
    int idleSteps = 520;
    int movingSteps = 220;
    float idleStepLength = 3.4e7f;
    float movingStepLength = 5.4e7f;
    bool httpReady = false;
    bool rendererThreadStarted = false;
    bool rendererThreadDone = false;
    bool rendererFatal = false;
    bool glReady = false;
    uint64_t frameSeq = 0;
    double frameMs = 0.0;
    std::string startupPhase = "created";
    std::string eglDisplay;
    std::string glVendor;
    std::string glRenderer;
    std::string glVersion;
    std::string lastError;
    std::vector<unsigned char> jpeg;
};

#ifndef EGL_PLATFORM_DEVICE_EXT
#define EGL_PLATFORM_DEVICE_EXT 0x313F
#endif

using EglQueryDevicesEXTProc = EGLBoolean (*)(EGLint, EGLDeviceEXT*, EGLint*);
using EglGetPlatformDisplayEXTProc = EGLDisplay (*)(EGLenum, void*, const EGLint*);

std::string eglErrorString(EGLint error) {
    switch (error) {
        case EGL_SUCCESS: return "EGL_SUCCESS";
        case EGL_NOT_INITIALIZED: return "EGL_NOT_INITIALIZED";
        case EGL_BAD_ACCESS: return "EGL_BAD_ACCESS";
        case EGL_BAD_ALLOC: return "EGL_BAD_ALLOC";
        case EGL_BAD_ATTRIBUTE: return "EGL_BAD_ATTRIBUTE";
        case EGL_BAD_CONTEXT: return "EGL_BAD_CONTEXT";
        case EGL_BAD_CONFIG: return "EGL_BAD_CONFIG";
        case EGL_BAD_CURRENT_SURFACE: return "EGL_BAD_CURRENT_SURFACE";
        case EGL_BAD_DISPLAY: return "EGL_BAD_DISPLAY";
        case EGL_BAD_SURFACE: return "EGL_BAD_SURFACE";
        case EGL_BAD_MATCH: return "EGL_BAD_MATCH";
        case EGL_BAD_PARAMETER: return "EGL_BAD_PARAMETER";
        case EGL_BAD_NATIVE_PIXMAP: return "EGL_BAD_NATIVE_PIXMAP";
        case EGL_BAD_NATIVE_WINDOW: return "EGL_BAD_NATIVE_WINDOW";
        case EGL_CONTEXT_LOST: return "EGL_CONTEXT_LOST";
        default: {
            std::ostringstream ss;
            ss << "EGL error 0x" << std::hex << error;
            return ss.str();
        }
    }
}

bool initializeDisplayCandidate(EGLDisplay candidate, const std::string& label, EGLDisplay& display, std::string& selected, std::string& attempts) {
    if (candidate == EGL_NO_DISPLAY) {
        attempts += label + ": EGL_NO_DISPLAY; ";
        return false;
    }

    EGLint major = 0;
    EGLint minor = 0;
    if (eglInitialize(candidate, &major, &minor)) {
        display = candidate;
        std::ostringstream ss;
        ss << label << " (EGL " << major << "." << minor << ")";
        selected = ss.str();
        return true;
    }

    attempts += label + ": " + eglErrorString(eglGetError()) + "; ";
    return false;
}

EGLDisplay openInitializedEglDisplay(std::string& selected, std::string& attempts) {
    EGLDisplay display = EGL_NO_DISPLAY;

    auto queryDevices = reinterpret_cast<EglQueryDevicesEXTProc>(eglGetProcAddress("eglQueryDevicesEXT"));
    auto getPlatformDisplay = reinterpret_cast<EglGetPlatformDisplayEXTProc>(eglGetProcAddress("eglGetPlatformDisplayEXT"));

    if (queryDevices && getPlatformDisplay) {
        EGLDeviceEXT devices[16]{};
        EGLint deviceCount = 0;
        if (queryDevices(16, devices, &deviceCount) && deviceCount > 0) {
            for (EGLint i = 0; i < deviceCount; ++i) {
                std::ostringstream label;
                label << "EGL device " << i;
                EGLDisplay candidate = getPlatformDisplay(EGL_PLATFORM_DEVICE_EXT, devices[i], nullptr);
                if (initializeDisplayCandidate(candidate, label.str(), display, selected, attempts)) {
                    return display;
                }
            }
        } else {
            attempts += "eglQueryDevicesEXT returned no EGL devices; ";
        }
    } else {
        attempts += "EGL_EXT_device_enumeration/EGL_EXT_platform_device not available; ";
    }

    EGLDisplay fallback = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (initializeDisplayCandidate(fallback, "EGL default display", display, selected, attempts)) {
        return display;
    }

    throw std::runtime_error("No usable headless EGL display was available. " + attempts);
}

class EglContext {
public:
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLSurface surface = EGL_NO_SURFACE;
    EGLContext context = EGL_NO_CONTEXT;
    std::string selectedDisplay;

    EglContext(int width, int height) {
        std::string attempts;
        display = openInitializedEglDisplay(selectedDisplay, attempts);

        if (!eglBindAPI(EGL_OPENGL_API)) {
            throw std::runtime_error("eglBindAPI(EGL_OPENGL_API) failed: " + eglErrorString(eglGetError()));
        }

        EGLint configAttribs[] = {
            EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
            EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
            EGL_RED_SIZE, 8,
            EGL_GREEN_SIZE, 8,
            EGL_BLUE_SIZE, 8,
            EGL_ALPHA_SIZE, 8,
            EGL_DEPTH_SIZE, 0,
            EGL_NONE
        };

        EGLConfig config = nullptr;
        EGLint numConfigs = 0;
        if (!eglChooseConfig(display, configAttribs, &config, 1, &numConfigs) || numConfigs < 1) {
            throw std::runtime_error("eglChooseConfig failed: " + eglErrorString(eglGetError()));
        }

        EGLint surfaceAttribs[] = {
            EGL_WIDTH, width,
            EGL_HEIGHT, height,
            EGL_NONE
        };
        surface = eglCreatePbufferSurface(display, config, surfaceAttribs);
        if (surface == EGL_NO_SURFACE) {
            throw std::runtime_error("eglCreatePbufferSurface failed: " + eglErrorString(eglGetError()));
        }

        const EGLint EGL_CONTEXT_MAJOR_VERSION_KHR = 0x3098;
        const EGLint EGL_CONTEXT_MINOR_VERSION_KHR = 0x30FB;
        const EGLint EGL_CONTEXT_OPENGL_PROFILE_MASK_KHR = 0x30FD;
        const EGLint EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT_KHR = 0x00000001;
        EGLint contextAttribs[] = {
            EGL_CONTEXT_MAJOR_VERSION_KHR, 4,
            EGL_CONTEXT_MINOR_VERSION_KHR, 3,
            EGL_CONTEXT_OPENGL_PROFILE_MASK_KHR, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT_KHR,
            EGL_NONE
        };

        context = eglCreateContext(display, config, EGL_NO_CONTEXT, contextAttribs);
        if (context == EGL_NO_CONTEXT) {
            throw std::runtime_error("eglCreateContext OpenGL 4.3 core failed: " + eglErrorString(eglGetError()));
        }
        if (!eglMakeCurrent(display, surface, surface, context)) {
            throw std::runtime_error("eglMakeCurrent failed: " + eglErrorString(eglGetError()));
        }

        glewExperimental = GL_TRUE;
        GLenum glewStatus = glewInit();
        glGetError(); // GLEW can set a harmless GL_INVALID_ENUM with core contexts.
        if (glewStatus != GLEW_OK) {
            throw std::runtime_error(std::string("glewInit failed: ") + reinterpret_cast<const char*>(glewGetErrorString(glewStatus)));
        }
    }

    ~EglContext() {
        if (display != EGL_NO_DISPLAY) {
            eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
            if (context != EGL_NO_CONTEXT) eglDestroyContext(display, context);
            if (surface != EGL_NO_SURFACE) eglDestroySurface(display, surface);
            eglTerminate(display);
        }
    }
};

GLuint compileComputeProgram(const std::string& source) {
    GLuint shader = glCreateShader(GL_COMPUTE_SHADER);
    const char* src = source.c_str();
    glShaderSource(shader, 1, &src, nullptr);
    glCompileShader(shader);

    GLint compiled = GL_FALSE;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
    if (!compiled) {
        GLint length = 0;
        glGetShaderiv(shader, GL_INFO_LOG_LENGTH, &length);
        std::string log(std::max(1, length), '\0');
        glGetShaderInfoLog(shader, length, nullptr, log.data());
        glDeleteShader(shader);
        throw std::runtime_error("Compute shader compile failed: " + log);
    }

    GLuint program = glCreateProgram();
    glAttachShader(program, shader);
    glLinkProgram(program);
    glDeleteShader(shader);

    GLint linked = GL_FALSE;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (!linked) {
        GLint length = 0;
        glGetProgramiv(program, GL_INFO_LOG_LENGTH, &length);
        std::string log(std::max(1, length), '\0');
        glGetProgramInfoLog(program, length, nullptr, log.data());
        glDeleteProgram(program);
        throw std::runtime_error("Compute program link failed: " + log);
    }
    return program;
}

std::vector<unsigned char> encodeJpeg(const std::vector<unsigned char>& rgba, int width, int height, int quality) {
    std::vector<unsigned char> rgb(static_cast<size_t>(width) * static_cast<size_t>(height) * 3);
    for (int y = 0; y < height; ++y) {
        int srcY = height - 1 - y; // OpenGL origin is bottom-left; JPEG/browser origin is top-left.
        for (int x = 0; x < width; ++x) {
            size_t src = (static_cast<size_t>(srcY) * width + x) * 4;
            size_t dst = (static_cast<size_t>(y) * width + x) * 3;
            rgb[dst + 0] = rgba[src + 0];
            rgb[dst + 1] = rgba[src + 1];
            rgb[dst + 2] = rgba[src + 2];
        }
    }

    jpeg_compress_struct cinfo{};
    jpeg_error_mgr jerr{};
    cinfo.err = jpeg_std_error(&jerr);
    jpeg_create_compress(&cinfo);

    unsigned char* outBuffer = nullptr;
    unsigned long outSize = 0;
    jpeg_mem_dest(&cinfo, &outBuffer, &outSize);

    cinfo.image_width = static_cast<JDIMENSION>(width);
    cinfo.image_height = static_cast<JDIMENSION>(height);
    cinfo.input_components = 3;
    cinfo.in_color_space = JCS_RGB;
    jpeg_set_defaults(&cinfo);
    jpeg_set_quality(&cinfo, quality, TRUE);
    jpeg_start_compress(&cinfo, TRUE);

    while (cinfo.next_scanline < cinfo.image_height) {
        JSAMPROW row = const_cast<JSAMPROW>(&rgb[static_cast<size_t>(cinfo.next_scanline) * width * 3]);
        jpeg_write_scanlines(&cinfo, &row, 1);
    }

    jpeg_finish_compress(&cinfo);
    std::vector<unsigned char> result(outBuffer, outBuffer + outSize);
    jpeg_destroy_compress(&cinfo);
    std::free(outBuffer);
    return result;
}

class Renderer {
public:
    Renderer(SharedState& shared) : state(shared), context(shared.width, shared.height) {
        const GLubyte* vendor = glGetString(GL_VENDOR);
        const GLubyte* renderer = glGetString(GL_RENDERER);
        const GLubyte* version = glGetString(GL_VERSION);
        {
            std::lock_guard<std::mutex> lock(state.mutex);
            state.eglDisplay = context.selectedDisplay;
            state.glVendor = vendor ? reinterpret_cast<const char*>(vendor) : "";
            state.glRenderer = renderer ? reinterpret_cast<const char*>(renderer) : "";
            state.glVersion = version ? reinterpret_cast<const char*>(version) : "";
            state.startupPhase = "compiling compute shader";
        }
        state.cv.notify_all();

        std::string shaderPath = std::string(ASTROMETRIC_SHADER_DIR) + "/astrometric_service.comp";
        program = compileComputeProgram(readFile(shaderPath));

        glGenTextures(1, &texture);
        glBindTexture(GL_TEXTURE_2D, texture);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA8, state.width, state.height);
        pixels.resize(static_cast<size_t>(state.width) * static_cast<size_t>(state.height) * 4);

        {
            std::lock_guard<std::mutex> lock(state.mutex);
            state.glReady = true;
            state.rendererFatal = false;
            state.startupPhase = "OpenGL ready; rendering first frame";
            state.lastError.clear();
        }
        state.cv.notify_all();
    }

    ~Renderer() {
        if (texture) glDeleteTextures(1, &texture);
        if (program) glDeleteProgram(program);
    }

    void renderLoop() {
        auto framePeriod = std::chrono::milliseconds(std::max(1, 1000 / std::max(1, state.fps)));
        while (g_running.load()) {
            Camera camera;
            bool moving = false;
            int quality = 86;
            {
                std::unique_lock<std::mutex> lock(state.mutex);
                state.cv.wait_for(lock, framePeriod, [&] { return state.dirty || !g_running.load(); });
                camera = state.camera;
                moving = state.moving || state.frameSeq == 0;
                quality = state.jpegQuality;
                state.dirty = false;
                if (state.frameSeq == 0) {
                    state.startupPhase = "rendering first frame";
                }
            }

            auto start = std::chrono::steady_clock::now();
            try {
                renderFrame(camera, moving);
                auto jpeg = encodeJpeg(pixels, state.width, state.height, quality);
                auto stop = std::chrono::steady_clock::now();
                std::lock_guard<std::mutex> lock(state.mutex);
                state.jpeg = std::move(jpeg);
                state.frameSeq += 1;
                state.frameMs = std::chrono::duration<double, std::milli>(stop - start).count();
                state.startupPhase = "streaming";
                state.rendererFatal = false;
                state.lastError.clear();
                state.cv.notify_all();
            } catch (const std::exception& exc) {
                std::lock_guard<std::mutex> lock(state.mutex);
                state.startupPhase = "rendering error; retrying";
                state.lastError = exc.what();
                state.cv.notify_all();
            }

            {
                std::lock_guard<std::mutex> lock(state.mutex);
                state.moving = false;
            }
        }
    }

private:
    SharedState& state;
    EglContext context;
    GLuint program = 0;
    GLuint texture = 0;
    std::vector<unsigned char> pixels;

    void uniform3(const char* name, const glm::vec3& v) {
        GLint location = glGetUniformLocation(program, name);
        if (location >= 0) glUniform3f(location, v.x, v.y, v.z);
    }

    void renderFrame(const Camera& camera, bool moving) {
        glUseProgram(program);
        glBindImageTexture(0, texture, 0, GL_FALSE, 0, GL_WRITE_ONLY, GL_RGBA8);

        glUniform1i(glGetUniformLocation(program, "uWidth"), state.width);
        glUniform1i(glGetUniformLocation(program, "uHeight"), state.height);
        glUniform1i(glGetUniformLocation(program, "uMoving"), moving ? 1 : 0);
        glUniform1f(glGetUniformLocation(program, "uTanHalfFov"), std::tan(glm::radians(camera.fovDegrees) * 0.5f));
        glUniform1f(glGetUniformLocation(program, "uAspect"), float(state.width) / float(std::max(1, state.height)));
        glUniform1f(glGetUniformLocation(program, "uDiskInner"), float(kSagittariusARs * 2.4));
        glUniform1f(glGetUniformLocation(program, "uDiskOuter"), float(kSagittariusARs * 7.2));
        glUniform1f(glGetUniformLocation(program, "uDiskThickness"), float(kSagittariusARs * 0.015));
        glUniform1i(glGetUniformLocation(program, "uIdleSteps"), state.idleSteps);
        glUniform1i(glGetUniformLocation(program, "uMovingSteps"), state.movingSteps);
        glUniform1f(glGetUniformLocation(program, "uIdleStepLength"), state.idleStepLength);
        glUniform1f(glGetUniformLocation(program, "uMovingStepLength"), state.movingStepLength);

        uniform3("uCamPos", camera.position());
        uniform3("uCamRight", camera.right());
        uniform3("uCamUp", camera.up());
        uniform3("uCamForward", camera.forward());

        GLuint groupsX = static_cast<GLuint>((state.width + 15) / 16);
        GLuint groupsY = static_cast<GLuint>((state.height + 15) / 16);
        glDispatchCompute(groupsX, groupsY, 1);
        glMemoryBarrier(GL_SHADER_IMAGE_ACCESS_BARRIER_BIT | GL_TEXTURE_FETCH_BARRIER_BIT);

        glBindTexture(GL_TEXTURE_2D, texture);
        glGetTexImage(GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data());
        GLenum err = glGetError();
        if (err != GL_NO_ERROR) {
            std::ostringstream ss;
            ss << "OpenGL render/readback error 0x" << std::hex << err;
            throw std::runtime_error(ss.str());
        }
    }
};

std::string cameraJson(const Camera& camera) {
    auto pos = camera.position();
    std::ostringstream out;
    out << "{"
        << "\"radius\":" << camera.radius << ","
        << "\"azimuth\":" << camera.azimuth << ","
        << "\"elevation\":" << camera.elevation << ","
        << "\"fov_degrees\":" << camera.fovDegrees << ","
        << "\"target\":[" << camera.target.x << "," << camera.target.y << "," << camera.target.z << "],"
        << "\"position\":[" << pos.x << "," << pos.y << "," << pos.z << "]"
        << "}";
    return out.str();
}

std::string healthJson(SharedState& state) {
    std::lock_guard<std::mutex> lock(state.mutex);
    std::ostringstream out;
    out << "{"
        << "\"ok\":true,"
        << "\"renderer\":\"main-computer-astrometric-renderer\","
        << "\"http_ready\":" << (state.httpReady ? "true" : "false") << ","
        << "\"renderer_thread_started\":" << (state.rendererThreadStarted ? "true" : "false") << ","
        << "\"renderer_thread_done\":" << (state.rendererThreadDone ? "true" : "false") << ","
        << "\"renderer_fatal\":" << (state.rendererFatal ? "true" : "false") << ","
        << "\"startup_phase\":\"" << jsonEscape(state.startupPhase) << "\","
        << "\"frame_seq\":" << state.frameSeq << ","
        << "\"frame_ms\":" << state.frameMs << ","
        << "\"width\":" << state.width << ","
        << "\"height\":" << state.height << ","
        << "\"fps\":" << state.fps << ","
        << "\"jpeg_quality\":" << state.jpegQuality << ","
        << "\"idle_steps\":" << state.idleSteps << ","
        << "\"moving_steps\":" << state.movingSteps << ","
        << "\"idle_step_length\":" << state.idleStepLength << ","
        << "\"moving_step_length\":" << state.movingStepLength << ","
        << "\"gl_ready\":" << (state.glReady ? "true" : "false") << ","
        << "\"stream_ready\":" << (!state.jpeg.empty() ? "true" : "false") << ","
        << "\"egl_display\":\"" << jsonEscape(state.eglDisplay) << "\","
        << "\"gl_vendor\":\"" << jsonEscape(state.glVendor) << "\","
        << "\"gl_renderer\":\"" << jsonEscape(state.glRenderer) << "\","
        << "\"gl_version\":\"" << jsonEscape(state.glVersion) << "\","
        << "\"last_error\":\"" << jsonEscape(state.lastError) << "\","
        << "\"camera\":" << cameraJson(state.camera)
        << "}";
    return out.str();
}

const char* httpStatusText(int status) {
    switch (status) {
        case 200: return "OK";
        case 400: return "Bad Request";
        case 404: return "Not Found";
        case 503: return "Service Unavailable";
        default: return "OK";
    }
}

void sendJson(int fd, int status, const std::string& body) {
    std::ostringstream header;
    header << "HTTP/1.1 " << status << " " << httpStatusText(status) << "\r\n"
           << "Content-Type: application/json; charset=utf-8\r\n"
           << "Cache-Control: no-store\r\n"
           << "Content-Length: " << body.size() << "\r\n"
           << "Connection: close\r\n\r\n";
    sendString(fd, header.str());
    sendString(fd, body);
}

void sendJpeg(int fd, SharedState& state) {
    std::vector<unsigned char> copy;
    {
        std::lock_guard<std::mutex> lock(state.mutex);
        copy = state.jpeg;
    }
    if (copy.empty()) {
        sendJson(fd, 503, "{\"ok\":false,\"error\":\"No rendered frame is available yet.\"}\n");
        return;
    }
    std::ostringstream header;
    header << "HTTP/1.1 200 OK\r\n"
           << "Content-Type: image/jpeg\r\n"
           << "Cache-Control: no-store, max-age=0\r\n"
           << "Content-Length: " << copy.size() << "\r\n"
           << "Connection: close\r\n\r\n";
    if (sendString(fd, header.str())) {
        sendAll(fd, copy.data(), copy.size());
    }
}

void streamMjpeg(int fd, SharedState& state) {
    std::ostringstream header;
    header << "HTTP/1.1 200 OK\r\n"
           << "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
           << "Cache-Control: no-store, max-age=0\r\n"
           << "Connection: close\r\n\r\n";
    if (!sendString(fd, header.str())) return;

    uint64_t lastSeq = 0;
    while (g_running.load()) {
        std::vector<unsigned char> copy;
        uint64_t seq = 0;
        {
            std::unique_lock<std::mutex> lock(state.mutex);
            state.cv.wait_for(lock, std::chrono::milliseconds(80), [&] {
                return state.frameSeq != lastSeq || !g_running.load();
            });
            if (!g_running.load()) break;
            seq = state.frameSeq;
            copy = state.jpeg;
        }
        if (copy.empty() || seq == lastSeq) continue;
        lastSeq = seq;

        std::ostringstream part;
        part << "--frame\r\n"
             << "Content-Type: image/jpeg\r\n"
             << "Content-Length: " << copy.size() << "\r\n\r\n";
        if (!sendString(fd, part.str())) break;
        if (!sendAll(fd, copy.data(), copy.size())) break;
        if (!sendString(fd, "\r\n")) break;
    }
}

void applyCameraPayload(SharedState& state, const std::string& body) {
    std::string type = jsonStringValue(body, "type", jsonStringValue(body, "action", ""));
    double dx = jsonNumber(body, "dx", 0.0);
    double dy = jsonNumber(body, "dy", 0.0);
    bool shift = jsonBool(body, "shift", false);

    std::lock_guard<std::mutex> lock(state.mutex);
    if (type == "reset") {
        state.camera.reset();
    } else if (type == "pan" || shift) {
        state.camera.pan(static_cast<float>(dx), static_cast<float>(dy));
    } else if (type == "orbit") {
        state.camera.orbit(static_cast<float>(dx), static_cast<float>(dy));
    } else if (type == "zoom") {
        double wheel = jsonNumber(body, "deltaY", jsonNumber(body, "wheel", dy));
        state.camera.zoom(static_cast<float>(wheel));
    } else if (type == "quality") {
        int quality = static_cast<int>(jsonNumber(body, "jpeg_quality", state.jpegQuality));
        state.jpegQuality = std::max(35, std::min(95, quality));
    }

    double fov = jsonNumber(body, "fov_degrees", -1.0);
    if (fov > 10.0 && fov < 120.0) {
        state.camera.fovDegrees = static_cast<float>(fov);
    }

    state.moving = true;
    state.dirty = true;
    state.cv.notify_all();
}

struct HttpRequest {
    std::string method;
    std::string path;
    std::string body;
};

void setClientTimeouts(int fd) {
    timeval timeout{};
    timeout.tv_sec = 3;
    timeout.tv_usec = 0;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
}

HttpRequest readRequest(int fd) {
    std::string data;
    char buffer[4096];
    while (data.find("\r\n\r\n") == std::string::npos && data.size() < 65536) {
        ssize_t n = recv(fd, buffer, sizeof(buffer), 0);
        if (n <= 0) break;
        data.append(buffer, buffer + n);
    }

    size_t headerEnd = data.find("\r\n\r\n");
    std::string headers = headerEnd == std::string::npos ? data : data.substr(0, headerEnd);
    std::string body = headerEnd == std::string::npos ? "" : data.substr(headerEnd + 4);

    std::istringstream firstLine(headers);
    HttpRequest req;
    firstLine >> req.method >> req.path;

    size_t contentLength = 0;
    std::regex clPattern("Content-Length:\\s*([0-9]+)", std::regex_constants::icase);
    std::smatch match;
    if (std::regex_search(headers, match, clPattern)) {
        contentLength = static_cast<size_t>(std::stoul(match[1].str()));
    }
    while (body.size() < contentLength && body.size() < 1024 * 1024) {
        ssize_t n = recv(fd, buffer, sizeof(buffer), 0);
        if (n <= 0) break;
        body.append(buffer, buffer + n);
    }
    if (body.size() > contentLength) body.resize(contentLength);
    req.body = body;
    return req;
}

void handleClient(int fd, SharedState& state) {
    setClientTimeouts(fd);
    HttpRequest req = readRequest(fd);
    if (req.method.empty() || req.path.empty()) {
        sendJson(fd, 400, "{\"ok\":false,\"error\":\"empty or malformed HTTP request\"}\n");
        close(fd);
        return;
    }
    std::string path = req.path;
    size_t queryPos = path.find('?');
    if (queryPos != std::string::npos) path = path.substr(0, queryPos);

    if (req.method == "GET" && path == "/health") {
        sendJson(fd, 200, healthJson(state) + "\n");
    } else if (req.method == "GET" && path == "/ready") {
        bool ready = false;
        {
            std::lock_guard<std::mutex> lock(state.mutex);
            ready = !state.jpeg.empty() && !state.rendererFatal;
        }
        sendJson(fd, ready ? 200 : 503, healthJson(state) + "\n");
    } else if (req.method == "GET" && path == "/frame.jpg") {
        sendJpeg(fd, state);
    } else if (req.method == "GET" && path == "/stream.mjpg") {
        streamMjpeg(fd, state);
    } else if (req.method == "POST" && path == "/camera") {
        applyCameraPayload(state, req.body);
        sendJson(fd, 200, healthJson(state) + "\n");
    } else {
        sendJson(fd, 404, "{\"ok\":false,\"error\":\"not found\"}\n");
    }
    close(fd);
}

void runServer(SharedState& state, const std::string& bindHost, int port) {
    int serverFd = socket(AF_INET, SOCK_STREAM, 0);
    if (serverFd < 0) throw std::runtime_error("socket() failed");

    int yes = 1;
    setsockopt(serverFd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<uint16_t>(port));
    if (inet_pton(AF_INET, bindHost.c_str(), &addr.sin_addr) != 1) {
        close(serverFd);
        throw std::runtime_error("Invalid bind address: " + bindHost);
    }

    if (bind(serverFd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        std::string error = std::strerror(errno);
        close(serverFd);
        throw std::runtime_error("bind() failed: " + error);
    }
    if (listen(serverFd, 64) < 0) {
        std::string error = std::strerror(errno);
        close(serverFd);
        throw std::runtime_error("listen() failed: " + error);
    }

    {
        std::lock_guard<std::mutex> lock(state.mutex);
        state.httpReady = true;
        if (state.startupPhase == "created") {
            state.startupPhase = "HTTP ready; starting renderer";
        }
    }
    state.cv.notify_all();

    std::cerr << "Astrometric renderer listening on " << bindHost << ":" << port << std::endl;

    while (g_running.load()) {
        sockaddr_in client{};
        socklen_t len = sizeof(client);
        int fd = accept(serverFd, reinterpret_cast<sockaddr*>(&client), &len);
        if (fd < 0) {
            if (errno == EINTR) continue;
            break;
        }
        std::thread(handleClient, fd, std::ref(state)).detach();
    }

    close(serverFd);
}

void runRendererThread(SharedState& state) {
    {
        std::lock_guard<std::mutex> lock(state.mutex);
        state.rendererThreadStarted = true;
        state.rendererThreadDone = false;
        state.rendererFatal = false;
        state.glReady = false;
        state.startupPhase = "initializing EGL/OpenGL";
        state.lastError.clear();
    }
    state.cv.notify_all();

    try {
        Renderer renderer(state);
        {
            std::lock_guard<std::mutex> lock(state.mutex);
            state.dirty = true;
            state.moving = true; // first frame uses the lower-latency moving integration profile
        }
        state.cv.notify_all();

        // EGL/OpenGL contexts are thread-local. Construct Renderer and keep every
        // GL command on this single thread; the HTTP server owns only control I/O.
        renderer.renderLoop();

        {
            std::lock_guard<std::mutex> lock(state.mutex);
            state.rendererThreadDone = true;
            if (!state.rendererFatal) {
                state.startupPhase = "renderer stopped";
            }
        }
        state.cv.notify_all();
    } catch (const std::exception& exc) {
        {
            std::lock_guard<std::mutex> lock(state.mutex);
            state.rendererThreadDone = true;
            state.rendererFatal = true;
            state.glReady = false;
            state.startupPhase = "renderer failed";
            state.lastError = exc.what();
        }
        state.cv.notify_all();
        std::cerr << "Astrometric renderer fatal error: " << exc.what() << std::endl;
    }
}

void signalHandler(int) {
    g_running.store(false);
}

} // namespace

int main() {
    std::signal(SIGINT, signalHandler);
    std::signal(SIGTERM, signalHandler);

    SharedState state;
    state.width = envInt("ASTROMETRIC_RENDERER_WIDTH", 640, 160, 1920);
    state.height = envInt("ASTROMETRIC_RENDERER_HEIGHT", 360, 120, 1080);
    state.fps = envInt("ASTROMETRIC_RENDERER_FPS", 10, 1, 60);
    state.jpegQuality = envInt("ASTROMETRIC_RENDERER_JPEG_QUALITY", 86, 35, 95);
    state.idleSteps = envInt("ASTROMETRIC_RENDERER_IDLE_STEPS", 520, 80, 6000);
    state.movingSteps = envInt("ASTROMETRIC_RENDERER_MOVING_STEPS", 220, 40, 3000);
    state.idleStepLength = envFloat("ASTROMETRIC_RENDERER_IDLE_STEP_LENGTH", 3.4e7f, 1.0e6f, 5.0e8f);
    state.movingStepLength = envFloat("ASTROMETRIC_RENDERER_MOVING_STEP_LENGTH", 5.4e7f, 1.0e6f, 8.0e8f);

    std::string bindHost = envString("ASTROMETRIC_RENDERER_BIND", "0.0.0.0");
    int port = envInt("ASTROMETRIC_RENDERER_PORT", 8794, 1024, 65535);

    try {
        std::thread serverThread([&] {
            try {
                runServer(state, bindHost, port);
            } catch (const std::exception& exc) {
                {
                    std::lock_guard<std::mutex> lock(state.mutex);
                    state.startupPhase = "HTTP server failed";
                    state.lastError = std::string("HTTP server failed: ") + exc.what();
                }
                state.cv.notify_all();
                g_running.store(false);
            }
        });
        serverThread.detach();

        std::thread rendererThread(runRendererThread, std::ref(state));
        rendererThread.detach();

        while (g_running.load()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
        state.cv.notify_all();
    } catch (const std::exception& exc) {
        std::cerr << "Astrometric renderer service fatal error: " << exc.what() << std::endl;
        return 1;
    }

    return 0;
}
