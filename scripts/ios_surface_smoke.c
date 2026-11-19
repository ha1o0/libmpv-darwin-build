// Checks real gpu-next output into a BGRA CVPixelBuffer (Flutter/AVKit target).
#include <CoreVideo/CoreVideo.h>
#include <Mpv/Mpv.h>
#include <stdatomic.h>
#include <stdio.h>
#include <time.h>

static void updated(void *ctx)
{
    atomic_store((atomic_bool *)ctx, true);
}

static CVPixelBufferRef make_pixel_buffer(size_t w, size_t h)
{
    const void *keys[] = { kCVPixelBufferIOSurfacePropertiesKey,
                           kCVPixelBufferMetalCompatibilityKey };
    CFDictionaryRef properties = CFDictionaryCreate(NULL, NULL, NULL, 0,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    const void *values[] = { properties, kCFBooleanTrue };
    CFDictionaryRef attrs = CFDictionaryCreate(NULL, keys, values, 2,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CVPixelBufferRef result = NULL;
    CVPixelBufferCreate(NULL, w, h, kCVPixelFormatType_32BGRA, attrs, &result);
    CFRelease(attrs);
    CFRelease(properties);
    return result;
}

static int render(mpv_render_context *render_context, CVPixelBufferRef buffer)
{
    int block = 0;
    int status = mpv_render_context_render(render_context, (mpv_render_param[]) {
        { MPV_RENDER_PARAM_IOSURFACE, CVPixelBufferGetIOSurface(buffer) },
        { MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block }, { 0 },
    });
    if (status < 0)
        fprintf(stderr, "mpv_render_context_render: %s\n", mpv_error_string(status));
    return status;
}

static bool has_color(CVPixelBufferRef buffer, bool blue)
{
    if (CVPixelBufferLockBaseAddress(buffer, kCVPixelBufferLock_ReadOnly))
        return false;
    const uint8_t *base = CVPixelBufferGetBaseAddress(buffer);
    const uint8_t *center = base + CVPixelBufferGetBytesPerRow(buffer) *
        (CVPixelBufferGetHeight(buffer) / 2) + (CVPixelBufferGetWidth(buffer) / 2) * 4;
    bool matches = center[blue ? 0 : 2] > 170 && center[1] < 85 && center[blue ? 2 : 0] < 85;
    CVPixelBufferUnlockBaseAddress(buffer, kCVPixelBufferLock_ReadOnly);
    return matches;
}

int main(int argc, char **argv)
{
    if (argc != 2 && argc != 3)
        return 1;
    const bool test_seek = argc == 3;
    bool did_seek = false;
    mpv_handle *mpv = mpv_create();
    mpv_render_context *render_context = NULL;
    CVPixelBufferRef a = make_pixel_buffer(32, 32), b = make_pixel_buffer(64, 64);
    atomic_bool pending = false;
    int result = 1;
    if (!mpv || !a || !b || mpv_set_option_string(mpv, "config", "no") < 0 ||
        mpv_set_option_string(mpv, "vo", "libmpv") < 0 ||
        mpv_set_option_string(mpv, "ao", "null") < 0 ||
        mpv_set_option_string(mpv, "hwdec", "no") < 0 ||
        mpv_set_option_string(mpv, "pause", "yes") < 0 || mpv_initialize(mpv) < 0)
        goto done;

    mpv_request_log_messages(mpv, "debug");
    int status = mpv_render_context_create(&render_context, mpv, (mpv_render_param[]) {
        { MPV_RENDER_PARAM_API_TYPE, MPV_RENDER_API_TYPE_IOSURFACE },
        { MPV_RENDER_PARAM_BACKEND, "gpu-next" }, { 0 },
    });
    if (status < 0) {
        fprintf(stderr, "MoltenVK create: %s\n", mpv_error_string(status));
        goto done;
    }
    mpv_render_context_set_update_callback(render_context, updated, &pending);
    if (mpv_command(mpv, (const char *[]) { "loadfile", argv[1], NULL }) < 0)
        goto done;
    const uint64_t deadline = clock_gettime_nsec_np(CLOCK_UPTIME_RAW) +
        (test_seek ? 25000000000ULL : 8000000000ULL);
    while (clock_gettime_nsec_np(CLOCK_UPTIME_RAW) < deadline) {
        mpv_event *event = mpv_wait_event(mpv, 0.02);
        if (event->event_id == MPV_EVENT_LOG_MESSAGE) {
            mpv_event_log_message *log = event->data;
            fprintf(stderr, "[%s] %s", log->prefix, log->text);
        }
        if (!atomic_exchange(&pending, false))
            continue;
        if (render(render_context, a) < 0)
            goto done;
        if (!has_color(a, did_seek))
            continue;
        if (render(render_context, a) < 0 || !has_color(a, did_seek))
            goto done;
        if (render(render_context, b) < 0 || !has_color(b, did_seek) || !has_color(a, did_seek))
            goto done;
        if (test_seek && !did_seek) {
            if (mpv_command(mpv, (const char *[]) { "seek", "1.25", "absolute+exact", NULL }) < 0)
                goto done;
            did_seek = true;
            continue;
        }
        puts(test_seek ? "LIBMPV_IOS_IOSURFACE_OK HEVC Main 10 first frame and exact seek"
                       : "LIBMPV_IOS_IOSURFACE_OK rendered two BGRA targets while paused");
        result = 0;
        break;
    }
done:
    if (result) {
        while (mpv) {
            mpv_event *event = mpv_wait_event(mpv, 0);
            if (event->event_id == MPV_EVENT_NONE) break;
            if (event->event_id == MPV_EVENT_LOG_MESSAGE) {
                mpv_event_log_message *log = event->data;
                fprintf(stderr, "[%s] %s", log->prefix, log->text);
            }
        }
        fprintf(stderr, "No verified IOSurface frame received\n");
    }
    if (render_context) mpv_render_context_free(render_context);
    if (mpv) mpv_terminate_destroy(mpv);
    if (a) CVPixelBufferRelease(a);
    if (b) CVPixelBufferRelease(b);
    return result;
}
