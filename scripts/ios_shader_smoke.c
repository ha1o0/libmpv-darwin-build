// Exercise GLSL hooks on the actual libmpv IOSurface renderer, including
// paused redraw, ordered Anime4K chains, failed loads, clearing and cleanup.
#define main surface_smoke_main
#include "ios_surface_smoke.c"
#undef main

static uint64_t fingerprint(CVPixelBufferRef buffer)
{
    CVPixelBufferLockBaseAddress(buffer, kCVPixelBufferLock_ReadOnly);
    const uint8_t *base = CVPixelBufferGetBaseAddress(buffer);
    uint64_t hash = 1469598103934665603ULL;
    for (size_t y = 0; y < CVPixelBufferGetHeight(buffer); y++) {
        const uint8_t *row = base + y * CVPixelBufferGetBytesPerRow(buffer);
        for (size_t x = 0; x < CVPixelBufferGetWidth(buffer) * 4; x++)
            hash = (hash ^ row[x]) * 1099511628211ULL;
    }
    CVPixelBufferUnlockBaseAddress(buffer, kCVPixelBufferLock_ReadOnly);
    return hash;
}

static bool drain_logs(mpv_handle *mpv, bool allow_error)
{
    bool valid = true;
    for (;;) {
        mpv_event *event = mpv_wait_event(mpv, 0);
        if (event->event_id == MPV_EVENT_NONE) break;
        if (event->event_id == MPV_EVENT_LOG_MESSAGE) {
            mpv_event_log_message *log = event->data;
            fprintf(stderr, "[%s] %s", log->prefix, log->text);
            if (!allow_error && log->log_level <= MPV_LOG_LEVEL_ERROR)
                valid = false;
        }
    }
    return valid;
}

int main(int argc, char **argv)
{
    if (argc < 4) return 1; // pattern.y4m, diagnostic.glsl, official shaders...
    int result = 1;
    mpv_handle *mpv = mpv_create();
    mpv_render_context *ctx = NULL;
    CVPixelBufferRef buffer = make_pixel_buffer(128, 128);
    atomic_bool pending = false;
    mpv_set_option_string(mpv, "config", "no");
    mpv_set_option_string(mpv, "vo", "libmpv");
    mpv_set_option_string(mpv, "ao", "null");
    mpv_set_option_string(mpv, "hwdec", "no");
    mpv_set_option_string(mpv, "pause", "yes");
    if (mpv_initialize(mpv) < 0) goto done;
    mpv_request_log_messages(mpv, "debug");
    if (mpv_render_context_create(&ctx, mpv, (mpv_render_param[]) {
        {MPV_RENDER_PARAM_API_TYPE, MPV_RENDER_API_TYPE_IOSURFACE},
        {MPV_RENDER_PARAM_BACKEND, "gpu-next"}, {0}}) < 0) goto done;
    mpv_render_context_set_update_callback(ctx, updated, &pending);
    mpv_command(mpv, (const char *[]) {"loadfile", argv[1], NULL});
    bool ready = false;
    uint64_t deadline = clock_gettime_nsec_np(CLOCK_UPTIME_RAW) + 10000000000ULL;
    while (clock_gettime_nsec_np(CLOCK_UPTIME_RAW) < deadline) {
        mpv_event *event = mpv_wait_event(mpv, .01);
        if (event->event_id == MPV_EVENT_PLAYBACK_RESTART) ready = true;
        if (atomic_exchange(&pending, false)) {
            mpv_render_context_update(ctx);
            if (render(ctx, buffer) < 0) goto done;
        }
        if (ready) break;
    }
    if (!ready || render(ctx, buffer) < 0) goto done;
    uint64_t baseline = fingerprint(buffer);
    if (mpv_set_property_string(mpv, "glsl-shaders", argv[2]) < 0 ||
        render(ctx, buffer) < 0 || !has_color(buffer, false)) {
        fprintf(stderr, "Diagnostic hook did not change the retained paused frame\n");
        goto done;
    }
    if (!drain_logs(mpv, false)) goto done;
    // A missing asset must not replace a known working chain with half a chain.
    mpv_set_property_string(mpv, "glsl-shaders", "/nonexistent/ios-smoke.glsl");
    if (render(ctx, buffer) < 0 || !has_color(buffer, false)) goto done;
    drain_logs(mpv, true);
    if (mpv_set_property_string(mpv, "glsl-shaders", "") < 0 ||
        render(ctx, buffer) < 0 || fingerprint(buffer) != baseline) goto done;
    uint64_t previous = baseline;
    for (int i = 3; i < argc; i++) {
        if (mpv_command(mpv, (const char *[]) {"change-list", "glsl-shaders", "append", argv[i], NULL}) < 0 ||
            render(ctx, buffer) < 0 || fingerprint(buffer) == previous ||
            !drain_logs(mpv, false)) {
            fprintf(stderr, "Anime4K chain did not alter pixels, or shader compilation failed\n");
            goto done;
        }
        previous = fingerprint(buffer);
    }
    // PiP stays on the existing software route while GPU hooks remain loaded.
    // Returning inline must reproduce the exact enhanced paused frame.
    if (render_pip(ctx, buffer) < 0 || fingerprint(buffer) == previous ||
        render(ctx, buffer) < 0 || fingerprint(buffer) != previous) goto done;
    for (int i = 0; i < 3; i++) {
        if (mpv_set_property_string(mpv, "glsl-shaders", "") < 0 ||
            render(ctx, buffer) < 0 || fingerprint(buffer) != baseline) goto done;
        if (mpv_set_property_string(mpv, "glsl-shaders", argv[3]) < 0 ||
            render(ctx, buffer) < 0 || fingerprint(buffer) == baseline ||
            !drain_logs(mpv, false)) goto done;
    }
    puts("LIBMPV_IOS_ANIME4K_OK real pixels, paused switching, chain order, clear and reload");
    result = 0;
done:
    drain_logs(mpv, true);
    if (ctx) mpv_render_context_free(ctx);
    mpv_terminate_destroy(mpv);
    if (buffer) CVPixelBufferRelease(buffer);
    return result;
}
