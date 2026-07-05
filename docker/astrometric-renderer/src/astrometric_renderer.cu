#include <cuda_runtime.h>
#include <stdio.h>  // libjpeg declares FILE* APIs in jpeglib.h when stdio is visible.
#include <jpeglib.h>

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
#include <cctype>
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
constexpr float kSagittariusARsF = 1.269e10f;
constexpr float kDefaultFovDegrees = 60.0f;
constexpr float kPi = 3.14159265358979323846f;

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

struct Vec3 {
    float x;
    float y;
    float z;
};

__host__ __device__ inline Vec3 v3(float x, float y, float z) {
    return Vec3{x, y, z};
}

__host__ __device__ inline Vec3 operator+(Vec3 a, Vec3 b) {
    return v3(a.x + b.x, a.y + b.y, a.z + b.z);
}

__host__ __device__ inline Vec3 operator-(Vec3 a, Vec3 b) {
    return v3(a.x - b.x, a.y - b.y, a.z - b.z);
}

__host__ __device__ inline Vec3 operator*(Vec3 a, float s) {
    return v3(a.x * s, a.y * s, a.z * s);
}

__host__ __device__ inline Vec3 operator*(float s, Vec3 a) {
    return a * s;
}

__host__ __device__ inline Vec3 operator/(Vec3 a, float s) {
    return v3(a.x / s, a.y / s, a.z / s);
}

__host__ __device__ inline float dot(Vec3 a, Vec3 b) {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}

__host__ __device__ inline Vec3 cross(Vec3 a, Vec3 b) {
    return v3(
        a.y * b.z - a.z * b.y,
        a.z * b.x - a.x * b.z,
        a.x * b.y - a.y * b.x
    );
}

__host__ __device__ inline float length(Vec3 a) {
    return sqrtf(dot(a, a));
}

__host__ __device__ inline Vec3 normalize(Vec3 a) {
    float len = length(a);
    if (len < 1.0e-20f) return v3(0.0f, 0.0f, 0.0f);
    return a / len;
}

__host__ __device__ inline float clampf(float value, float low, float high) {
    return fminf(high, fmaxf(low, value));
}

__host__ __device__ inline float fractf(float value) {
    return value - floorf(value);
}

__host__ __device__ inline float mixf(float a, float b, float t) {
    return a * (1.0f - t) + b * t;
}

__host__ __device__ inline Vec3 mix3(Vec3 a, Vec3 b, float t) {
    return v3(mixf(a.x, b.x, t), mixf(a.y, b.y, t), mixf(a.z, b.z, t));
}

__host__ __device__ inline float smoothstep(float edge0, float edge1, float x) {
    float t = clampf((x - edge0) / fmaxf(edge1 - edge0, 1.0e-20f), 0.0f, 1.0f);
    return t * t * (3.0f - 2.0f * t);
}

struct Camera {
    Vec3 target{0.0f, 0.0f, 0.0f};
    float radius = 1.65e11f;
    float minRadius = 1.05e11f;
    float maxRadius = 1.0e12f;
    float azimuth = 0.18f;
    float elevation = 1.10f;
    float orbitSpeed = 0.0065f;
    float panSpeed = 0.0012f;
    float zoomSpeed = 1.075f;
    float fovDegrees = kDefaultFovDegrees;

    Vec3 position() const {
        return v3(
            target.x + radius * std::sin(elevation) * std::cos(azimuth),
            target.y + radius * std::cos(elevation),
            target.z + radius * std::sin(elevation) * std::sin(azimuth)
        );
    }

    Vec3 forward() const {
        return normalize(target - position());
    }

    Vec3 right() const {
        Vec3 f = forward();
        Vec3 r = cross(f, v3(0.0f, 1.0f, 0.0f));
        if (length(r) < 1e-5f) return v3(1.0f, 0.0f, 0.0f);
        return normalize(r);
    }

    Vec3 up() const {
        return normalize(cross(right(), forward()));
    }

    void orbit(float dx, float dy) {
        azimuth -= dx * orbitSpeed;
        elevation -= dy * orbitSpeed;
        elevation = std::max(0.01f, std::min(kPi - 0.01f, elevation));
    }

    void pan(float dx, float dy) {
        target = target + right() * (-dx * panSpeed * radius) + up() * (dy * panSpeed * radius);
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
        target = v3(0.0f, 0.0f, 0.0f);
        radius = 1.65e11f;
        azimuth = 0.18f;
        elevation = 1.10f;
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
    int idleSteps = 1900;
    int movingSteps = 800;
    float idleStepLength = 1.5e8f;
    float movingStepLength = 1.8e8f;
    std::string rendererMode = "gpu";
    std::string rendererBackend = "cuda";
    bool httpReady = false;
    bool rendererThreadStarted = false;
    bool rendererThreadDone = false;
    bool rendererFatal = false;
    bool glReady = false;
    bool cudaReady = false;
    uint64_t frameSeq = 0;
    double frameMs = 0.0;
    std::string startupPhase = "created";
    std::string eglDisplay = "not used";
    std::string glVendor = "CUDA";
    std::string glRenderer;
    std::string glVersion;
    std::string cudaDevice;
    int cudaDeviceOrdinal = -1;
    int cudaRuntimeVersion = 0;
    std::string lastError;
    std::vector<unsigned char> jpeg;
};

std::string cudaErrorText(cudaError_t status) {
    std::ostringstream out;
    out << cudaGetErrorName(status) << ": " << cudaGetErrorString(status);
    return out.str();
}

void cudaCheck(cudaError_t status, const std::string& where) {
    if (status != cudaSuccess) {
        throw std::runtime_error(where + " failed: " + cudaErrorText(status));
    }
}

struct CameraParams {
    int width;
    int height;
    int moving;
    Vec3 camPos;
    Vec3 camRight;
    Vec3 camUp;
    Vec3 camForward;
    float tanHalfFov;
    float aspect;
    float diskInner;
    float diskOuter;
    float diskThickness;
    int idleSteps;
    int movingSteps;
    float idleStepLength;
    float movingStepLength;
};

struct Ray {
    float x;
    float y;
    float z;
    float r;
    float theta;
    float phi;
    float dr;
    float dtheta;
    float dphi;
    float E;
    float L;
};

__device__ float hash13(Vec3 p) {
    p.x = fractf(p.x * 0.3183099f + 0.11f);
    p.y = fractf(p.y * 0.3183099f + 0.17f);
    p.z = fractf(p.z * 0.3183099f + 0.23f);
    p = p * 17.0f;
    return fractf(p.x * p.y * p.z * (p.x + p.y + p.z));
}

__device__ Ray initRay(Vec3 pos, Vec3 dir) {
    Ray ray{};
    ray.x = pos.x;
    ray.y = pos.y;
    ray.z = pos.z;
    ray.r = fmaxf(length(pos), kSagittariusARsF * 1.01f);
    ray.theta = acosf(clampf(pos.z / ray.r, -1.0f, 1.0f));
    ray.phi = atan2f(pos.y, pos.x);

    float sinTheta = fmaxf(sinf(ray.theta), 1e-4f);
    float dx = dir.x;
    float dy = dir.y;
    float dz = dir.z;
    ray.dr = sinf(ray.theta) * cosf(ray.phi) * dx + sinf(ray.theta) * sinf(ray.phi) * dy + cosf(ray.theta) * dz;
    ray.dtheta = (cosf(ray.theta) * cosf(ray.phi) * dx + cosf(ray.theta) * sinf(ray.phi) * dy - sinf(ray.theta) * dz) / ray.r;
    ray.dphi = (-sinf(ray.phi) * dx + cosf(ray.phi) * dy) / (ray.r * sinTheta);

    ray.L = ray.r * ray.r * sinTheta * ray.dphi;
    float f = fmaxf(1.0f - kSagittariusARsF / ray.r, 1e-4f);
    float angular = ray.r * ray.r * (ray.dtheta * ray.dtheta + sinTheta * sinTheta * ray.dphi * ray.dphi);
    float dt_dL = sqrtf(fmaxf((ray.dr * ray.dr) / f + angular, 1e-8f));
    ray.E = f * dt_dL;
    return ray;
}

__device__ bool intercept(const Ray& ray) {
    return ray.r <= kSagittariusARsF * 1.025f;
}

__device__ void geodesicRHS(const Ray& ray, Vec3& d1, Vec3& d2) {
    float r = fmaxf(ray.r, kSagittariusARsF * 1.001f);
    float theta = clampf(ray.theta, 0.001f, 3.14059f);
    float dr = ray.dr;
    float dtheta = ray.dtheta;
    float dphi = ray.dphi;
    float f = fmaxf(1.0f - kSagittariusARsF / r, 1e-4f);
    float dt_dL = ray.E / f;
    float sinTheta = fmaxf(sinf(theta), 1e-4f);
    float cosTheta = cosf(theta);

    d1 = v3(dr, dtheta, dphi);
    d2.x = - (kSagittariusARsF / (2.0f * r * r)) * f * dt_dL * dt_dL
         + (kSagittariusARsF / (2.0f * r * r * f)) * dr * dr
         + r * (dtheta * dtheta + sinTheta * sinTheta * dphi * dphi);
    d2.y = -2.0f * dr * dtheta / r + sinTheta * cosTheta * dphi * dphi;
    d2.z = -2.0f * dr * dphi / r - 2.0f * cosTheta / sinTheta * dtheta * dphi;
}

__device__ void rk4Step(Ray& ray, float dL) {
    Vec3 k1a, k1b;
    geodesicRHS(ray, k1a, k1b);

    Ray r2 = ray;
    r2.r += 0.5f * dL * k1a.x;
    r2.theta += 0.5f * dL * k1a.y;
    r2.phi += 0.5f * dL * k1a.z;
    r2.dr += 0.5f * dL * k1b.x;
    r2.dtheta += 0.5f * dL * k1b.y;
    r2.dphi += 0.5f * dL * k1b.z;
    Vec3 k2a, k2b;
    geodesicRHS(r2, k2a, k2b);

    Ray r3 = ray;
    r3.r += 0.5f * dL * k2a.x;
    r3.theta += 0.5f * dL * k2a.y;
    r3.phi += 0.5f * dL * k2a.z;
    r3.dr += 0.5f * dL * k2b.x;
    r3.dtheta += 0.5f * dL * k2b.y;
    r3.dphi += 0.5f * dL * k2b.z;
    Vec3 k3a, k3b;
    geodesicRHS(r3, k3a, k3b);

    Ray r4 = ray;
    r4.r += dL * k3a.x;
    r4.theta += dL * k3a.y;
    r4.phi += dL * k3a.z;
    r4.dr += dL * k3b.x;
    r4.dtheta += dL * k3b.y;
    r4.dphi += dL * k3b.z;
    Vec3 k4a, k4b;
    geodesicRHS(r4, k4a, k4b);

    ray.r += dL * (k1a.x + 2.0f * k2a.x + 2.0f * k3a.x + k4a.x) / 6.0f;
    ray.theta += dL * (k1a.y + 2.0f * k2a.y + 2.0f * k3a.y + k4a.y) / 6.0f;
    ray.phi += dL * (k1a.z + 2.0f * k2a.z + 2.0f * k3a.z + k4a.z) / 6.0f;
    ray.dr += dL * (k1b.x + 2.0f * k2b.x + 2.0f * k3b.x + k4b.x) / 6.0f;
    ray.dtheta += dL * (k1b.y + 2.0f * k2b.y + 2.0f * k3b.y + k4b.y) / 6.0f;
    ray.dphi += dL * (k1b.z + 2.0f * k2b.z + 2.0f * k3b.z + k4b.z) / 6.0f;

    ray.theta = clampf(ray.theta, 0.001f, 3.14059f);
    ray.x = ray.r * sinf(ray.theta) * cosf(ray.phi);
    ray.y = ray.r * sinf(ray.theta) * sinf(ray.phi);
    ray.z = ray.r * cosf(ray.theta);
}

struct DiskHit {
    bool hit;
    Vec3 pos;
    float radius;
    float angle;
    float radialT;
    float edge;
};

__device__ DiskHit intersectDiskPlane(Vec3 oldPos, Vec3 newPos, const CameraParams& params) {
    DiskHit hit{};
    hit.hit = false;
    hit.pos = newPos;
    hit.radius = 0.0f;
    hit.angle = 0.0f;
    hit.radialT = 0.0f;
    hit.edge = 0.0f;

    // Match the upstream shader's contract: the disk is hit only when the ray
    // crosses the equatorial x/z plane.  Do not use a "near plane" fallback:
    // grazing rays that merely skim close to y == 0 create the visible
    // horizontal scratch/clipping line across the disk.
    float product = oldPos.y * newPos.y;
    if (!(product < 0.0f)) {
        return hit;
    }

    float dy = newPos.y - oldPos.y;
    if (fabsf(dy) < 1.0e-6f) {
        return hit;
    }

    float segmentT = clampf(-oldPos.y / dy, 0.0f, 1.0f);
    Vec3 pos = oldPos + (newPos - oldPos) * segmentT;
    float radius = sqrtf(pos.x * pos.x + pos.z * pos.z);
    if (radius < params.diskInner || radius > params.diskOuter) {
        return hit;
    }

    hit.hit = true;
    hit.pos = pos;
    hit.radius = radius;
    hit.angle = atan2f(pos.z, pos.x);
    hit.radialT = clampf((radius - params.diskInner) / fmaxf(1.0f, params.diskOuter - params.diskInner), 0.0f, 1.0f);
    hit.edge = smoothstep(params.diskInner, params.diskInner * 1.02f, radius)
             * (1.0f - smoothstep(params.diskOuter * 0.985f, params.diskOuter, radius));
    return hit;
}

__device__ Vec3 skyColor(Vec3 dir) {
    Vec3 d = normalize(dir);
    float milky = powf(fmaxf(0.0f, 1.0f - fabsf(d.y) * 1.6f), 2.5f);
    Vec3 floored = v3(floorf(d.x * 1400.0f), floorf(d.y * 1400.0f), floorf(d.z * 1400.0f));
    float grid = hash13(floored);
    float star = smoothstep(0.9965f, 1.0f, grid);
    Vec3 nebula = mix3(v3(0.015f, 0.025f, 0.055f), v3(0.08f, 0.035f, 0.13f), milky);
    return nebula + v3(star * 1.0f, star * 0.88f, star * 0.68f);
}

__global__ void renderKernel(uchar4* out, CameraParams params) {
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= params.width || y >= params.height) return;

    float u = (2.0f * (static_cast<float>(x) + 0.5f) / static_cast<float>(params.width) - 1.0f)
            * params.aspect * params.tanHalfFov;
    float v = (1.0f - 2.0f * (static_cast<float>(y) + 0.5f) / static_cast<float>(params.height))
            * params.tanHalfFov;
    Vec3 dir = normalize(params.camRight * u + params.camUp * v + params.camForward);
    Ray ray = initRay(params.camPos, dir);

    Vec3 previous = v3(ray.x, ray.y, ray.z);
    Vec3 color = v3(0.0f, 0.0f, 0.0f);
    bool hitBlackHole = false;
    bool hitDisk = false;
    DiskHit diskHit{};

    int steps = params.moving != 0 ? params.movingSteps : params.idleSteps;
    float dL = params.moving != 0 ? params.movingStepLength : params.idleStepLength;

    for (int i = 0; i < steps; ++i) {
        if (intercept(ray)) {
            hitBlackHole = true;
            break;
        }
        rk4Step(ray, dL);
        Vec3 current = v3(ray.x, ray.y, ray.z);
        DiskHit candidate = intersectDiskPlane(previous, current, params);
        if (candidate.hit) {
            diskHit = candidate;
            hitDisk = true;
            break;
        }
        previous = current;
        if (ray.r > 1.0e13f) break;
    }

    Vec3 background = skyColor(dir);
    if (hitBlackHole) {
        color = v3(0.0f, 0.0f, 0.0f);
    } else if (hitDisk) {
        // The upstream shader emitted an RGBA disk layer that OpenGL blended
        // over whatever had already been drawn.  MJPEG has no alpha channel, so
        // this renderer must explicitly composite the disk hit over a
        // background instead of treating the disk hit as a solid final image.
        // Keeping the camera outside diskOuter prevents the camera from
        // starting inside the accretion disk annulus, which was the real cause
        // of the "clipped sheet" view.
        float t = diskHit.radialT;
        float originalR = clampf(diskHit.radius / fmaxf(1.0f, params.diskOuter), 0.0f, 1.0f);
        Vec3 upstreamDisk = v3(1.0f, fmaxf(0.16f, originalR), 0.2f);
        Vec3 warmOuter = mix3(v3(1.0f, 0.70f, 0.22f), v3(1.0f, 0.34f, 0.08f), t);
        float innerGlow = 1.0f + 0.22f * (1.0f - t);
        float limb = 0.88f + 0.12f * fmaxf(0.0f, sinf(diskHit.angle) * 0.5f + 0.5f);
        Vec3 diskColor = mix3(upstreamDisk, warmOuter, 0.35f) * innerGlow * limb;
        float diskAlpha = clampf((0.32f + 0.62f * originalR) * diskHit.edge, 0.0f, 0.92f);
        color = mix3(background, diskColor, diskAlpha);
    } else {
        color = background;
    }

    float vignette = smoothstep(1.25f, 0.15f, length(v3(u / fmaxf(params.aspect, 0.01f), v, 0.0f)));
    color = color * mixf(0.76f, 1.0f, vignette);

    unsigned char r = static_cast<unsigned char>(clampf(color.x, 0.0f, 1.0f) * 255.0f);
    unsigned char g = static_cast<unsigned char>(clampf(color.y, 0.0f, 1.0f) * 255.0f);
    unsigned char b = static_cast<unsigned char>(clampf(color.z, 0.0f, 1.0f) * 255.0f);
    out[static_cast<size_t>(y) * static_cast<size_t>(params.width) + static_cast<size_t>(x)] = make_uchar4(r, g, b, 255);
}

std::vector<unsigned char> encodeJpeg(const std::vector<unsigned char>& rgba, int width, int height, int quality) {
    std::vector<unsigned char> rgb(static_cast<size_t>(width) * static_cast<size_t>(height) * 3);
    for (int y = 0; y < height; ++y) {
        int srcY = y;
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

std::vector<unsigned char> makeSmokeRgba(int width, int height, uint64_t seq) {
    std::vector<unsigned char> rgba(static_cast<size_t>(width) * static_cast<size_t>(height) * 4);
    const int centerX = std::max(1, width / 2);
    const int centerY = std::max(1, height / 2);
    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            float nx = static_cast<float>(x - centerX) / static_cast<float>(centerX);
            float ny = static_cast<float>(y - centerY) / static_cast<float>(centerY);
            float r = std::sqrt(nx * nx + ny * ny);
            bool ring = std::fabs(r - 0.48f) < 0.018f || std::fabs(r - 0.72f) < 0.014f;
            bool crosshair = std::abs(x - centerX) < 1 || std::abs(y - centerY) < 1;
            bool tick = ((x + static_cast<int>(seq * 5)) % 48) < 3 && (y % 48) < 3;
            size_t i = (static_cast<size_t>(y) * width + x) * 4;
            unsigned char base = static_cast<unsigned char>(std::max(0.0f, 28.0f - r * 18.0f));
            rgba[i + 0] = ring ? 240 : (crosshair ? 120 : (tick ? 80 : base));
            rgba[i + 1] = ring ? 190 : (crosshair ? 210 : (tick ? 140 : static_cast<unsigned char>(base + 8)));
            rgba[i + 2] = ring ? 60 : (crosshair ? 255 : (tick ? 220 : static_cast<unsigned char>(base + 18)));
            rgba[i + 3] = 255;
        }
    }
    return rgba;
}



class CudaRenderer {
public:
    explicit CudaRenderer(SharedState& shared) : state(shared) {
        int deviceCount = 0;
        cudaCheck(cudaGetDeviceCount(&deviceCount), "cudaGetDeviceCount");
        if (deviceCount < 1) {
            throw std::runtime_error("No CUDA-capable NVIDIA GPU is visible inside the renderer container.");
        }

        int device = envInt("ASTROMETRIC_RENDERER_CUDA_DEVICE", 0, 0, deviceCount - 1);
        cudaCheck(cudaSetDevice(device), "cudaSetDevice");
        cudaCheck(cudaFree(nullptr), "cuda runtime initialization");

        cudaDeviceProp prop{};
        cudaCheck(cudaGetDeviceProperties(&prop, device), "cudaGetDeviceProperties");
        int runtimeVersion = 0;
        cudaRuntimeGetVersion(&runtimeVersion);

        size_t pixelCount = static_cast<size_t>(state.width) * static_cast<size_t>(state.height);
        cudaCheck(cudaMalloc(&devicePixels, pixelCount * sizeof(uchar4)), "cudaMalloc framebuffer");
        hostRgba.resize(pixelCount * 4);

        {
            std::lock_guard<std::mutex> lock(state.mutex);
            state.cudaReady = true;
            state.glReady = true; // compatibility with the existing frontend/status tests
            state.rendererFatal = false;
            state.startupPhase = "CUDA ready; rendering first frame";
            state.lastError.clear();
            state.cudaDevice = prop.name;
            state.cudaDeviceOrdinal = device;
            state.cudaRuntimeVersion = runtimeVersion;
            state.glVendor = "CUDA";
            state.glRenderer = state.cudaDevice;
            std::ostringstream version;
            version << "CUDA runtime " << runtimeVersion;
            state.glVersion = version.str();
            state.eglDisplay = "not used; CUDA kernel backend";
        }
        state.cv.notify_all();
    }

    ~CudaRenderer() {
        if (devicePixels) {
            cudaFree(devicePixels);
            devicePixels = nullptr;
        }
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
                    state.startupPhase = "rendering first CUDA frame";
                }
            }

            auto start = std::chrono::steady_clock::now();
            try {
                renderFrame(camera, moving);
                auto jpeg = encodeJpeg(hostRgba, state.width, state.height, quality);
                auto stop = std::chrono::steady_clock::now();
                {
                    std::lock_guard<std::mutex> lock(state.mutex);
                    state.jpeg = std::move(jpeg);
                    state.frameSeq += 1;
                    state.frameMs = std::chrono::duration<double, std::milli>(stop - start).count();
                    state.startupPhase = "streaming CUDA renderer";
                    state.rendererFatal = false;
                    state.lastError.clear();
                }
                state.cv.notify_all();
            } catch (const std::exception& exc) {
                {
                    std::lock_guard<std::mutex> lock(state.mutex);
                    state.startupPhase = "CUDA rendering error; retrying";
                    state.lastError = exc.what();
                }
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
    uchar4* devicePixels = nullptr;
    std::vector<unsigned char> hostRgba;

    void renderFrame(const Camera& camera, bool moving) {
        CameraParams params{};
        params.width = state.width;
        params.height = state.height;
        params.moving = moving ? 1 : 0;
        params.camPos = camera.position();
        params.camRight = camera.right();
        params.camUp = camera.up();
        params.camForward = camera.forward();
        params.tanHalfFov = std::tan((camera.fovDegrees * kPi / 180.0f) * 0.5f);
        params.aspect = static_cast<float>(state.width) / static_cast<float>(std::max(1, state.height));
        params.diskInner = static_cast<float>(kSagittariusARs * 2.2);
        params.diskOuter = static_cast<float>(kSagittariusARs * 5.2);
        params.diskThickness = static_cast<float>(kSagittariusARs * 0.025);
        params.idleSteps = state.idleSteps;
        params.movingSteps = state.movingSteps;
        params.idleStepLength = state.idleStepLength;
        params.movingStepLength = state.movingStepLength;

        dim3 block(16, 16);
        dim3 grid(
            static_cast<unsigned int>((state.width + block.x - 1) / block.x),
            static_cast<unsigned int>((state.height + block.y - 1) / block.y)
        );
        renderKernel<<<grid, block>>>(devicePixels, params);
        cudaCheck(cudaGetLastError(), "renderKernel launch");
        cudaCheck(cudaDeviceSynchronize(), "renderKernel synchronize");
        cudaCheck(cudaMemcpy(hostRgba.data(), devicePixels, hostRgba.size(), cudaMemcpyDeviceToHost), "cudaMemcpy framebuffer");
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
        << "\"renderer_mode\":\"" << jsonEscape(state.rendererMode) << "\","
        << "\"renderer_backend\":\"" << jsonEscape(state.rendererBackend) << "\","
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
        << "\"cuda_ready\":" << (state.cudaReady ? "true" : "false") << ","
        << "\"stream_ready\":" << (!state.jpeg.empty() ? "true" : "false") << ","
        << "\"egl_display\":\"" << jsonEscape(state.eglDisplay) << "\","
        << "\"gl_vendor\":\"" << jsonEscape(state.glVendor) << "\","
        << "\"gl_renderer\":\"" << jsonEscape(state.glRenderer) << "\","
        << "\"gl_version\":\"" << jsonEscape(state.glVersion) << "\","
        << "\"cuda_device\":\"" << jsonEscape(state.cudaDevice) << "\","
        << "\"cuda_device_ordinal\":" << state.cudaDeviceOrdinal << ","
        << "\"cuda_runtime_version\":" << state.cudaRuntimeVersion << ","
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

void runCudaRendererThread(SharedState& state) {
    {
        std::lock_guard<std::mutex> lock(state.mutex);
        state.rendererThreadStarted = true;
        state.rendererThreadDone = false;
        state.rendererFatal = false;
        state.glReady = false;
        state.cudaReady = false;
        state.startupPhase = "initializing CUDA renderer";
        state.lastError.clear();
    }
    state.cv.notify_all();

    try {
        CudaRenderer renderer(state);
        {
            std::lock_guard<std::mutex> lock(state.mutex);
            state.dirty = true;
            state.moving = true; // first frame uses the lower-latency moving integration profile
        }
        state.cv.notify_all();

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
            state.cudaReady = false;
            state.startupPhase = "renderer failed";
            state.lastError = exc.what();
        }
        state.cv.notify_all();
        std::cerr << "Astrometric CUDA renderer fatal error: " << exc.what() << std::endl;
    }
}

void runSmokeRendererThread(SharedState& state) {
    {
        std::lock_guard<std::mutex> lock(state.mutex);
        state.rendererThreadStarted = true;
        state.rendererThreadDone = false;
        state.rendererFatal = false;
        state.glReady = false;
        state.cudaReady = false;
        state.eglDisplay = "diagnostic smoke mode";
        state.glVendor = "no GPU";
        state.glRenderer = "CPU diagnostic JPEG generator";
        state.glVersion = "smoke";
        state.cudaDevice = "";
        state.startupPhase = "diagnostic smoke stream starting; CUDA bypassed";
        state.lastError.clear();
    }
    state.cv.notify_all();

    uint64_t localSeq = 0;
    auto framePeriod = std::chrono::milliseconds(std::max(1, 1000 / std::max(1, state.fps)));
    while (g_running.load()) {
        auto start = std::chrono::steady_clock::now();
        int width = 0;
        int height = 0;
        int quality = 86;
        {
            std::lock_guard<std::mutex> lock(state.mutex);
            width = state.width;
            height = state.height;
            quality = state.jpegQuality;
        }
        auto rgba = makeSmokeRgba(width, height, localSeq++);
        auto jpeg = encodeJpeg(rgba, width, height, quality);
        auto stop = std::chrono::steady_clock::now();
        {
            std::lock_guard<std::mutex> lock(state.mutex);
            state.jpeg = std::move(jpeg);
            state.frameSeq += 1;
            state.frameMs = std::chrono::duration<double, std::milli>(stop - start).count();
            state.startupPhase = "diagnostic smoke streaming";
            state.rendererFatal = false;
        }
        state.cv.notify_all();
        std::this_thread::sleep_for(framePeriod);
    }

    {
        std::lock_guard<std::mutex> lock(state.mutex);
        state.rendererThreadDone = true;
        state.startupPhase = "diagnostic smoke stopped";
    }
    state.cv.notify_all();
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
    state.idleSteps = envInt("ASTROMETRIC_RENDERER_IDLE_STEPS", 1900, 80, 6000);
    state.movingSteps = envInt("ASTROMETRIC_RENDERER_MOVING_STEPS", 800, 40, 3000);
    state.idleStepLength = envFloat("ASTROMETRIC_RENDERER_IDLE_STEP_LENGTH", 1.5e8f, 1.0e6f, 5.0e8f);
    state.movingStepLength = envFloat("ASTROMETRIC_RENDERER_MOVING_STEP_LENGTH", 1.8e8f, 1.0e6f, 8.0e8f);
    state.rendererMode = envString("ASTROMETRIC_RENDERER_MODE", "gpu");
    state.rendererBackend = envString("ASTROMETRIC_RENDERER_BACKEND", "cuda");
    std::transform(state.rendererMode.begin(), state.rendererMode.end(), state.rendererMode.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    std::transform(state.rendererBackend.begin(), state.rendererBackend.end(), state.rendererBackend.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    if (state.rendererMode != "gpu" && state.rendererMode != "smoke") {
        state.rendererMode = "gpu";
    }
    if (state.rendererBackend != "cuda") {
        state.rendererBackend = "cuda";
    }

    std::string bindHost = envString("ASTROMETRIC_RENDERER_BIND", "0.0.0.0");
    int port = envInt("ASTROMETRIC_RENDERER_PORT", 8794, 1024, 65535);

    std::cerr << "Astrometric renderer starting mode=" << state.rendererMode
              << " backend=" << state.rendererBackend
              << " bind=" << bindHost << ":" << port
              << " size=" << state.width << "x" << state.height
              << " fps=" << state.fps << std::endl;

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

        if (state.rendererMode == "smoke") {
            std::thread rendererThread(runSmokeRendererThread, std::ref(state));
            rendererThread.detach();
        } else {
            std::thread rendererThread(runCudaRendererThread, std::ref(state));
            rendererThread.detach();
        }

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
