// Checks real gpu-next output into a BGRA CVPixelBuffer (Flutter/AVKit target).
#include <CoreVideo/CoreVideo.h>
#include <Mpv/Mpv.h>
#include <stdatomic.h>
#include <dlfcn.h>
#include <limits.h>
#include <string.h>
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

static int render_pip(mpv_render_context *ctx, CVPixelBufferRef buffer)
{
    int skip = 1, mode = 1, block = 0;
    int size[] = { CVPixelBufferGetWidth(buffer), CVPixelBufferGetHeight(buffer) };
    size_t stride = CVPixelBufferGetBytesPerRow(buffer);
    if (CVPixelBufferLockBaseAddress(buffer, 0))
        return MPV_ERROR_GENERIC;
    int status = mpv_render_context_render(ctx, (mpv_render_param[]) {
        { MPV_RENDER_PARAM_IOS_PIP_SW, &mode },
        { MPV_RENDER_PARAM_SKIP_RENDERING, &skip },
        { MPV_RENDER_PARAM_SW_SIZE, size },
        { MPV_RENDER_PARAM_SW_FORMAT, "bgr0" },
        { MPV_RENDER_PARAM_SW_STRIDE, &stride },
        { MPV_RENDER_PARAM_SW_POINTER, CVPixelBufferGetBaseAddress(buffer) },
        { MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block }, {0},
    });
    CVPixelBufferUnlockBaseAddress(buffer, 0);
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

static bool check_decoded_pip_fallback(mpv_render_context *ctx)
{
    void *frame = NULL;
    int skip = 1, block = 0;
    // The ordinary SKIP_RENDERING API may touch the GPU. The PiP variant
    // must reject an unsupported software frame without consuming it.
    int status = mpv_render_context_render(ctx, (mpv_render_param[]) {
        { MPV_RENDER_PARAM_IOS_PIP_FRAME, &frame },
        { MPV_RENDER_PARAM_SKIP_RENDERING, &skip },
        { MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block }, { 0 },
    });
    if (frame) CVPixelBufferRelease(frame);
    return status == MPV_ERROR_UNSUPPORTED && !frame;
}

// Fail only the BGR0 screenshot allocation after the tiny source is paused.
// The error must stay in the screenshot scope, while playback keeps its frame.
static bool screenshot_allocation_failure_is_local(mpv_handle *mpv)
{
    void (*max_alloc)(size_t) = dlsym(RTLD_DEFAULT, "av_max_alloc");
    if (!max_alloc)
        return false;
    max_alloc(2048);
    mpv_node result = {0};
    int status = mpv_command_ret(mpv,
        (const char *[]) { "screenshot-raw", "video", NULL }, &result);
    max_alloc(INT_MAX);
    mpv_free_node_contents(&result);
    bool scoped = false, fatal = false;
    for (;;) {
        mpv_event *event = mpv_wait_event(mpv, 0);
        if (event->event_id == MPV_EVENT_NONE)
            break;
        if (event->event_id == MPV_EVENT_LOG_MESSAGE) {
            mpv_event_log_message *log = event->data;
            if (strstr(log->text, "Out of memory.")) {
                scoped |= !strcmp(log->prefix, "screenshot");
                fatal |= !strcmp(log->prefix, "cplayer");
            }
        }
        fatal |= event->event_id == MPV_EVENT_END_FILE;
    }
    return status < 0 && scoped && !fatal;
}

#include "ios_cache_preview_smoke.c"

int main(int argc, char **argv)
{
    if (argc != 2 && argc != 3)
        return 1;
    const bool test_seek = argc == 3;
    bool did_seek = false;
    bool checked_pip_fallback = false;
    mpv_handle *mpv = mpv_create();
    mpv_render_context *render_context = NULL;
    CVPixelBufferRef a = make_pixel_buffer(32, 32), b = make_pixel_buffer(64, 64);
    atomic_bool pending = false;
    int result = 1;
    if (!mpv || !a || !b || mpv_set_option_string(mpv, "config", "no") < 0 ||
        mpv_set_option_string(mpv, "vo", "libmpv") < 0 ||
        mpv_set_option_string(mpv, "ao", "null") < 0 ||
        mpv_set_option_string(mpv, "hwdec", "no") < 0 ||
        (test_seek && (mpv_set_option_string(mpv, "cache", "yes") < 0 ||
                      mpv_set_option_string(mpv, "demuxer-seekable-cache", "yes") < 0 ||
                      mpv_set_option_string(mpv, "demuxer-max-bytes", "33554432") < 0 ||
                      mpv_set_option_string(mpv, "demuxer-readahead-secs", "20") < 0)) ||
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
    void *decoded = NULL;
    if (mpv_render_context_get_info(render_context, (mpv_render_param) {
            MPV_RENDER_PARAM_IOS_PIP_FRAME, &decoded }) < 0 || decoded)
        goto done;
    int pip_supported = 0;
    if (mpv_render_context_get_info(render_context, (mpv_render_param) {
            MPV_RENDER_PARAM_IOS_PIP_SW, &pip_supported }) < 0 || pip_supported < 2)
        goto done;
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
        if (!checked_pip_fallback) {
            mpv_render_frame_info info = {0};
            if (mpv_render_context_get_info(render_context, (mpv_render_param) {
                    MPV_RENDER_PARAM_NEXT_FRAME_INFO, &info }) < 0)
                goto done;
            if ((info.flags & MPV_RENDER_FRAME_INFO_PRESENT) &&
                !(info.flags & (MPV_RENDER_FRAME_INFO_REDRAW | MPV_RENDER_FRAME_INFO_REPEAT))) {
                if (!check_decoded_pip_fallback(render_context)) {
                    fprintf(stderr, "Decoded PiP accepted or lost a software fallback frame\n");
                    goto done;
                }
                checked_pip_fallback = true;
            }
        }
        if (render(render_context, a) < 0)
            goto done;
        if (!has_color(a, did_seek))
            continue;
        if (!checked_pip_fallback)
            goto done;
        // The same context must compose the retained paused frame, and then
        // return to the original Vulkan output without a track / decoder swap.
        if (render_pip(render_context, b) < 0 || !has_color(b, did_seek) ||
            render(render_context, a) < 0 || !has_color(a, did_seek))
            goto done;
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
        if (!test_seek && (!screenshot_allocation_failure_is_local(mpv) ||
            render(render_context, a) < 0 || !has_color(a, false))) {
            fprintf(stderr, "Screenshot allocation failure escaped its command scope\n");
            goto done;
        }
        if (test_seek && (!check_cache_preview(mpv) ||
            render(render_context, a) < 0 || !has_color(a, true))) {
            fprintf(stderr, "Cached preview failed or changed primary rendering\n");
            goto done;
        }
        puts(test_seek ? "LIBMPV_IOS_IOSURFACE_OK HEVC Main 10 first frame and exact seek"
                       : "LIBMPV_IOS_IOSURFACE_OK rendered two BGRA targets while paused");
        puts("LIBMPV_IOS_PIP_OK same-context software composition and return to Vulkan");
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
