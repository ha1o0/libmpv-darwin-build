// Included by ios_surface_smoke.c to use its existing IOSurface pixel checks.
// Read the same buffered HEVC packets as desktop previews. The secondary
// player accepts only the snapshot and never opens the original input.
#include <math.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

static bool check_cache_preview(mpv_handle *main_player)
{
    char directory[1024], clip[1200], metadata[1210];
    const char *temporary = getenv("TMPDIR");
    int length = snprintf(directory, sizeof(directory), "%s/cache-preview-XXXXXX",
                          temporary ? temporary : "/tmp");
    if (length < 0 || length >= sizeof(directory) || !mkdtemp(directory))
        return false;
    snprintf(clip, sizeof(clip), "%s/frame.nut", directory);
    snprintf(metadata, sizeof(metadata), "%s.json", clip);
    mpv_handle *preview = NULL;
    mpv_render_context *ctx = NULL;
    CVPixelBufferRef target = NULL;
    bool ok = false;
    double before = -1, after = -2;
    if (mpv_get_property(main_player, "time-pos", MPV_FORMAT_DOUBLE, &before) < 0)
        goto done;
    uint64_t deadline = clock_gettime_nsec_np(CLOCK_UPTIME_RAW) + 8000000000ULL;
    int status;
    do {
        status = mpv_command(main_player,
            (const char *[]) {"floe-cache-snapshot", "1.25", clip, NULL});
        if (status >= 0) break;
        mpv_wait_event(main_player, 0.05);
    } while (clock_gettime_nsec_np(CLOCK_UPTIME_RAW) < deadline);
    struct stat info;
    if (status < 0 || stat(clip, &info) || info.st_size <= 32)
        goto done;
    FILE *file = fopen(metadata, "rb");
    if (!file) goto done;
    char timing[1024] = {0};
    size_t count = fread(timing, 1, sizeof(timing) - 1, file);
    fclose(file);
    char *position = count ? strstr(timing, "\"position\"") : NULL;
    position = position ? strchr(position, ':') : NULL;
    if (!position) goto done;
    char *end;
    double offset = strtod(position + 1, &end);
    if (end == position + 1 || !isfinite(offset) || offset < 0) goto done;
    char start[64];
    snprintf(start, sizeof(start), "%.9f", offset);
    preview = mpv_create();
    if (!preview || mpv_set_option_string(preview, "config", "no") < 0 ||
        mpv_set_option_string(preview, "vo", "libmpv") < 0 ||
        mpv_set_option_string(preview, "ao", "null") < 0 ||
        mpv_set_option_string(preview, "audio", "no") < 0 ||
        mpv_set_option_string(preview, "sub", "no") < 0 ||
        mpv_set_option_string(preview, "hwdec", "no") < 0 ||
        mpv_set_option_string(preview, "pause", "yes") < 0 ||
        mpv_set_option_string(preview, "cache", "no") < 0 ||
        mpv_set_option_string(preview, "access-references", "no") < 0 ||
        mpv_set_option_string(preview, "demuxer-lavf-format", "nut") < 0 ||
        mpv_set_option_string(preview, "target-trc", "srgb") < 0 ||
        mpv_set_option_string(preview, "target-prim", "bt.709") < 0 ||
        mpv_set_option_string(preview, "start", start) < 0 ||
        mpv_initialize(preview) < 0)
        goto done;
    if (mpv_render_context_create(&ctx, preview, (mpv_render_param[]) {
            {MPV_RENDER_PARAM_API_TYPE, MPV_RENDER_API_TYPE_IOSURFACE},
            {MPV_RENDER_PARAM_BACKEND, "gpu-next"}, {0},
        }) < 0)
        goto done;
    atomic_bool pending = false;
    mpv_render_context_set_update_callback(ctx, updated, &pending);
    target = make_pixel_buffer(160, 90);
    if (!target || mpv_command(preview, (const char *[]) {"loadfile", clip, NULL}) < 0)
        goto done;
    deadline = clock_gettime_nsec_np(CLOCK_UPTIME_RAW) + 15000000000ULL;
    while (clock_gettime_nsec_np(CLOCK_UPTIME_RAW) < deadline) {
        mpv_wait_event(preview, 0.02);
        if (atomic_exchange(&pending, false) && render(ctx, target) >= 0 &&
            has_color(target, true)) {
            ok = mpv_get_property(main_player, "time-pos", MPV_FORMAT_DOUBLE, &after) >= 0 &&
                 fabs(before - after) < 0.001;
            break;
        }
    }
    if (ok) puts("LIBMPV_IOS_CACHE_PREVIEW_OK NUT snapshot decoded; main position unchanged");
done:
    if (ctx) mpv_render_context_free(ctx);
    if (preview) mpv_terminate_destroy(preview);
    if (target) CVPixelBufferRelease(target);
    unlink(metadata);
    unlink(clip);
    rmdir(directory);
    return ok;
}
