// Compare real BGRA output before/after the mobile SDR target policy.
#define main previous_surface_smoke_main
#include "ios_surface_smoke.c"
#undef main

static int capture(const char *file, bool hdr, bool explicit_target, uint8_t *out)
{
    mpv_handle *mpv = mpv_create();
    mpv_render_context *ctx = NULL;
    CVPixelBufferRef buffer = make_pixel_buffer(64, 32);
    atomic_bool dirty = false;
    int advanced = 1;
    const char *options[][2] = {
        {"config", "no"}, {"ao", "null"}, {"vo", "libmpv"},
        {"pause", "yes"}, {"keep-open", "yes"}, {"hwdec", "no"},
        {"hdr-compute-peak", "auto"}, {"hdr-peak-percentile", "100"},
    };
    for (int i = 0; i < sizeof(options) / sizeof(options[0]); i++)
        if (mpv_set_option_string(mpv, options[i][0], options[i][1]) < 0) return 1;
    if (hdr && mpv_set_option_string(mpv, "vf",
        "format=colormatrix=bt.2020-ncl:primaries=bt.2020:gamma=pq:max-luma=4000") < 0) return 1;
    if (explicit_target) {
        const char *target[][2] = {
            {"target-colorspace-hint", "no"}, {"target-prim", "bt.709"},
            {"target-trc", "srgb"}, {"target-peak", "203"},
        };
        for (int i = 0; i < sizeof(target) / sizeof(target[0]); i++)
            if (mpv_set_option_string(mpv, target[i][0], target[i][1]) < 0) return 1;
    }
    if (mpv_initialize(mpv) < 0 || mpv_render_context_create(&ctx, mpv,
        (mpv_render_param[]) {
            {MPV_RENDER_PARAM_API_TYPE, "iosurface"},
            {MPV_RENDER_PARAM_BACKEND, "gpu-next"},
            {MPV_RENDER_PARAM_ADVANCED_CONTROL, &advanced}, {0},
        }) < 0) return 2;
    mpv_render_context_set_update_callback(ctx, updated, &dirty);
    if (mpv_command(mpv, (const char *[]) {"loadfile", file, NULL}) < 0) return 3;
    bool loaded = false, rendered = false;
    for (int i = 0; i < 200; i++) {
        mpv_event *event = mpv_wait_event(mpv, 0.01);
        loaded |= event->event_id == MPV_EVENT_FILE_LOADED;
        if (atomic_exchange(&dirty, false) &&
            (mpv_render_context_update(ctx) & MPV_RENDER_UPDATE_FRAME))
            rendered |= loaded && render(ctx, buffer) >= 0;
        if (rendered && i > 75) break;
    }
    for (int i = 0; i < 4; i++) {
        mpv_render_context_update(ctx);
        if (render(ctx, buffer) < 0) return 4;
    }
    if (CVPixelBufferLockBaseAddress(buffer, kCVPixelBufferLock_ReadOnly)) return 4;
    const uint8_t *pixels = CVPixelBufferGetBaseAddress(buffer);
    size_t stride = CVPixelBufferGetBytesPerRow(buffer);
    for (int row = 0; row < 32; row++) memcpy(out + row * 256, pixels + row * stride, 256);
    CVPixelBufferUnlockBaseAddress(buffer, kCVPixelBufferLock_ReadOnly);
    mpv_render_context_free(ctx);
    mpv_terminate_destroy(mpv);
    CVPixelBufferRelease(buffer);
    return rendered ? 0 : 5;
}

int main(int argc, char **argv)
{
    if (argc != 3) return 1;
    for (int hdr = 0; hdr < 2; hdr++) {
        uint8_t baseline[64 * 32 * 4], explicit_target[64 * 32 * 4];
        if (capture(argv[hdr + 1], hdr, false, baseline) ||
            capture(argv[hdr + 1], hdr, true, explicit_target)) return 2;
        int max_delta = 0, colored = 0;
        for (int i = 0; i < sizeof(baseline); i++) {
            if (i % 4 == 3) continue;
            int delta = abs((int)baseline[i] - explicit_target[i]);
            if (delta > max_delta) max_delta = delta;
            colored |= baseline[i] > 32;
        }
        printf("MOBILE_SDR_TARGET hdr=%d max_channel_delta=%d\n", hdr, max_delta);
        if (!colored || max_delta > 2) return 3;
    }
    puts("LIBMPV_IOS_MOBILE_TARGET_OK SDR colors and PQ luminance preserve default output");
    return 0;
}
