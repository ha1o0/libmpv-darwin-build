// Exercise actual same-decoder PiP output, geometry and libass composition.
#include <CoreVideo/CoreVideo.h>
#include <Mpv/Mpv.h>
#include <math.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

static void updated(void *context)
{
    atomic_store((atomic_bool *)context, true);
}

static CVPixelBufferRef buffer(int width, int height)
{
    const void *keys[] = { kCVPixelBufferIOSurfacePropertiesKey };
    CFDictionaryRef empty = CFDictionaryCreate(NULL, NULL, NULL, 0,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    const void *values[] = { empty };
    CFDictionaryRef attrs = CFDictionaryCreate(NULL, keys, values, 1,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CVPixelBufferRef result = NULL;
    CVPixelBufferCreate(NULL, width, height, kCVPixelFormatType_32BGRA, attrs, &result);
    CFRelease(attrs);
    CFRelease(empty);
    return result;
}

static bool geometry_colors(CVPixelBufferRef pixel);

static int compose_mode(mpv_render_context *ctx, CVPixelBufferRef pixel, int mode)
{
    int skip = 1, block = 0;
    int dimensions[] = { CVPixelBufferGetWidth(pixel), CVPixelBufferGetHeight(pixel) };
    size_t stride = CVPixelBufferGetBytesPerRow(pixel);
    if (CVPixelBufferLockBaseAddress(pixel, 0)) return MPV_ERROR_GENERIC;
    int status = mpv_render_context_render(ctx, (mpv_render_param[]) {
        { MPV_RENDER_PARAM_IOS_PIP_SW, &mode },
        { MPV_RENDER_PARAM_SKIP_RENDERING, &skip },
        { MPV_RENDER_PARAM_SW_SIZE, dimensions },
        { MPV_RENDER_PARAM_SW_FORMAT, "bgr0" },
        { MPV_RENDER_PARAM_SW_STRIDE, &stride },
        { MPV_RENDER_PARAM_SW_POINTER, CVPixelBufferGetBaseAddress(pixel) },
        { MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block }, {0},
    });
    CVPixelBufferUnlockBaseAddress(pixel, 0);
    return status;
}

static int compose(mpv_render_context *ctx, CVPixelBufferRef pixel)
{
    return compose_mode(ctx, pixel, 1);
}

// Count neutral subtitle pixels at the format's peak and near HDR reference
// white. The test pattern itself is saturated red and blue.
static bool subtitle_levels(CVPixelBufferRef pixel, int *peak, int *reference)
{
    *peak = *reference = 0;
    if (CVPixelBufferLockBaseAddress(pixel, kCVPixelBufferLock_ReadOnly)) return false;
    int width = CVPixelBufferGetWidth(pixel), height = CVPixelBufferGetHeight(pixel);
    size_t stride = CVPixelBufferGetBytesPerRow(pixel);
    const uint8_t *base = CVPixelBufferGetBaseAddress(pixel);
    for (int y = 0; y < height; y++) {
        for (int x = 0; x < width; x++) {
            const uint8_t *p = base + y * stride + x * 4;
            *peak += p[0] > 190 && p[1] > 190 && p[2] > 190;
            *reference += p[0] > 138 && p[0] < 158 && p[1] > 138 && p[1] < 158 &&
                p[2] > 138 && p[2] < 158;
        }
    }
    CVPixelBufferUnlockBaseAddress(pixel, kCVPixelBufferLock_ReadOnly);
    return true;
}

// A PQ frame keeps its code values in the software target. Mode 1 blends
// sRGB subtitles unchanged (white at the PQ peak); mode 2 must place white at
// reference white, 203 nits, which is PQ code 0.58 (148 of 255).
static bool hdr_subtitles(mpv_handle *mpv, mpv_render_context *ctx, CVPixelBufferRef pixel)
{
    uint64_t deadline = clock_gettime_nsec_np(CLOCK_UPTIME_RAW) + 8000000000ULL;
    while (clock_gettime_nsec_np(CLOCK_UPTIME_RAW) < deadline) {
        mpv_event *event = mpv_wait_event(mpv, 0.02);
        if (event->event_id == MPV_EVENT_LOG_MESSAGE) {
            mpv_event_log_message *message = event->data;
            fprintf(stderr, "[%s] %s", message->prefix, message->text);
        }
        char *transfer = mpv_get_property_string(mpv, "video-out-params/gamma");
        bool pq = transfer && !strcmp(transfer, "pq");
        mpv_free(transfer);
        int peak = 0, reference = 0;
        if (!pq || compose_mode(ctx, pixel, 1) < 0 ||
            !subtitle_levels(pixel, &peak, &reference) || peak <= 20) continue;
        int sdr_peak = peak;
        if (compose_mode(ctx, pixel, 2) < 0 || !subtitle_levels(pixel, &peak, &reference))
            return false;
        fprintf(stderr, "HDR subtitle levels: mode 1 peak=%d, mode 2 peak=%d reference=%d\n",
                sdr_peak, peak, reference);
        return peak == 0 && reference > 20 && geometry_colors(pixel);
    }
    return false;
}

static bool color(const uint8_t *pixel, bool blue)
{
    return pixel[blue ? 0 : 2] > 170 && pixel[1] < 85 && pixel[blue ? 2 : 0] < 85;
}

static bool geometry(CVPixelBufferRef pixel, bool rotated, bool subtitles)
{
    if (CVPixelBufferLockBaseAddress(pixel, kCVPixelBufferLock_ReadOnly)) return false;
    int width = CVPixelBufferGetWidth(pixel), height = CVPixelBufferGetHeight(pixel);
    size_t stride = CVPixelBufferGetBytesPerRow(pixel);
    const uint8_t *base = CVPixelBufferGetBaseAddress(pixel);
    const uint8_t *red = base + (height / (rotated ? 4 : 2)) * stride +
        (width / (rotated ? 2 : 4)) * 4;
    const uint8_t *blue = base + (height * (rotated ? 3 : 2) / 4) * stride +
        (width * (rotated ? 2 : 3) / 4) * 4;
    bool ok = color(red, false) && color(blue, true);
    if (subtitles) {
        int white = 0;
        for (int y = 0; y < height; y++) {
            for (int x = 0; x < width; x++) {
                const uint8_t *p = base + y * stride + x * 4;
                white += p[0] > 190 && p[1] > 190 && p[2] > 190;
            }
        }
        ok = ok && white > 20;
    }
    CVPixelBufferUnlockBaseAddress(pixel, kCVPixelBufferLock_ReadOnly);
    return ok;
}

// The video itself must be untouched by the subtitle mapping.
static bool geometry_colors(CVPixelBufferRef pixel)
{
    return geometry(pixel, true, false);
}

static bool wait_for_output(mpv_handle *mpv, mpv_render_context *ctx,
                            CVPixelBufferRef pixel, bool rotated, bool subtitles)
{
    uint64_t deadline = clock_gettime_nsec_np(CLOCK_UPTIME_RAW) + 8000000000ULL;
    while (clock_gettime_nsec_np(CLOCK_UPTIME_RAW) < deadline) {
        mpv_event *event = mpv_wait_event(mpv, 0.02);
        if (event->event_id == MPV_EVENT_LOG_MESSAGE) {
            mpv_event_log_message *message = event->data;
            fprintf(stderr, "[%s] %s", message->prefix, message->text);
        }
        if (compose(ctx, pixel) >= 0 && geometry(pixel, rotated, subtitles)) return true;
    }
    return false;
}

int main(int argc, char **argv)
{
    if (argc != 3) return 1;
    mpv_handle *mpv = mpv_create();
    mpv_render_context *ctx = NULL;
    CVPixelBufferRef inline_frame = buffer(640, 320), pip_frame = buffer(320, 640);
    atomic_bool pending = false;
    int result = 1;
    char *video_id = NULL;
    double position = 0;
    if (!mpv || !inline_frame || !pip_frame ||
        mpv_set_option_string(mpv, "config", "no") < 0 ||
        mpv_set_option_string(mpv, "vo", "libmpv") < 0 ||
        mpv_set_option_string(mpv, "ao", "null") < 0 ||
        mpv_set_option_string(mpv, "hwdec", "no") < 0 ||
        mpv_set_option_string(mpv, "pause", "yes") < 0 ||
        mpv_set_option_string(mpv, "sub-pos", "50") < 0 ||
        mpv_initialize(mpv) < 0) goto done;
    mpv_request_log_messages(mpv, "warn");
    if (mpv_render_context_create(&ctx, mpv, (mpv_render_param[]) {
        { MPV_RENDER_PARAM_API_TYPE, MPV_RENDER_API_TYPE_IOSURFACE },
        { MPV_RENDER_PARAM_BACKEND, "gpu-next" }, {0},
    }) < 0) goto done;
    mpv_render_context_set_update_callback(ctx, updated, &pending);
    if (mpv_command(mpv, (const char *[]) { "loadfile", argv[1], NULL }) < 0 ||
        !wait_for_output(mpv, ctx, inline_frame, false, false)) goto done;
    video_id = mpv_get_property_string(mpv, "vid");
    if (!video_id || mpv_get_property(mpv, "time-pos", MPV_FORMAT_DOUBLE, &position) < 0) goto done;
    if (mpv_set_property_string(mpv, "video-rotate", "90") < 0 ||
        !wait_for_output(mpv, ctx, pip_frame, true, false)) goto done;
    if (mpv_command(mpv, (const char *[]) { "sub-add", argv[2], "select", NULL }) < 0 ||
        !wait_for_output(mpv, ctx, pip_frame, true, true)) goto done;
    char *after_id = mpv_get_property_string(mpv, "vid");
    double after_position = 0;
    int paused = 0;
    bool preserved = after_id && !strcmp(video_id, after_id) &&
        mpv_get_property(mpv, "time-pos", MPV_FORMAT_DOUBLE, &after_position) >= 0 &&
        fabs(position - after_position) < 0.05 &&
        mpv_get_property(mpv, "pause", MPV_FORMAT_FLAG, &paused) >= 0 && paused;
    mpv_free(after_id);
    if (!preserved) goto done;
    if (mpv_set_property_string(mpv, "vf", "format:gamma=pq:primaries=bt.2020") < 0 ||
        !hdr_subtitles(mpv, ctx, pip_frame)) {
        fprintf(stderr, "HDR subtitle mapping validation failed\n");
        goto done;
    }
    puts("LIBMPV_IOS_PIP_COMPOSITION_OK geometry, upright subtitles, paused track and position, "
         "HDR subtitles at reference white");
    result = 0;
done:
    if (result) fprintf(stderr, "Same-context PiP geometry/subtitle validation failed\n");
    mpv_free(video_id);
    if (ctx) mpv_render_context_free(ctx);
    if (mpv) mpv_terminate_destroy(mpv);
    if (inline_frame) CVPixelBufferRelease(inline_frame);
    if (pip_frame) CVPixelBufferRelease(pip_frame);
    return result;
}
