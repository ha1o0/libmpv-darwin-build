// Actual iOS libmpv transport reader, launched by ios_http_cache_smoke.py.
#include <mpv/client.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int option(mpv_handle *player, const char *name, const char *value)
{
    int result = mpv_set_option_string(player, name, value);
    if (result < 0) fprintf(stderr, "option %s: %s\n", name, mpv_error_string(result));
    return result;
}

int main(int argc, char **argv)
{
    if (argc != 6) return 2;
    const char *mode = argv[1], *url = argv[2], *cache_dir = argv[3];
    bool cache = strcmp(cache_dir, "-") != 0;
    mpv_handle *player = mpv_create();
    if (!player) return 3;
    const char *options[][2] = {
        {"config", "no"}, {"terminal", "no"}, {"vo", "null"},
        {"ao", "null"}, {"hwdec", "no"}, {"idle", "yes"},
        {"pause", "no"}, {"untimed", "yes"},
        {"network-timeout", "5"}, {"http-proxy", ""}, {"cache", "yes"},
        {"cache-on-disk", "no"}, {"curl-enabled", "yes"},
        {"tls-verify", "yes"}, {"floe-cache-max-bytes", "16777216"},
        {"floe-cache-source", "12345678-1234-1234-1234-123456789abc"},
    };
    for (size_t index = 0; index < sizeof(options) / sizeof(options[0]); index++) {
        if (option(player, options[index][0], options[index][1]) < 0) return 4;
    }
    if (option(player, "floe-cache-enabled", cache ? "yes" : "no") < 0 ||
        option(player, "access-references", !strcmp(mode, "hls") ? "yes" : "no") < 0 ||
        (cache && (option(player, "floe-cache-dir", cache_dir) < 0 ||
                   option(player, "floe-cache-key", argv[4]) < 0 ||
                   option(player, "floe-cache-url", url) < 0)) ||
        (strcmp(argv[5], "-") && option(player, "tls-ca-file", argv[5]) < 0)) return 5;
    if (mpv_initialize(player) < 0) return 6;
    mpv_observe_property(player, 101, "video-params/w", MPV_FORMAT_INT64);
    mpv_observe_property(player, 102, "video-params/h", MPV_FORMAT_INT64);
    mpv_observe_property(player, 103, "video-format", MPV_FORMAT_STRING);
    mpv_request_log_messages(player, "info");
    int64_t version = 0;
    int verified = 0;
    if (mpv_get_property(player, "floe-cache-version", MPV_FORMAT_INT64, &version) < 0 ||
        mpv_get_property(player, "options/tls-verify", MPV_FORMAT_FLAG, &verified) < 0 ||
        version != 4 || !verified) return 7;
    int command = mpv_command(player, (const char *[]) {"loadfile", url, NULL});
    int end_error = 0, reason = -1;
    bool loaded = false, ended = false;
    int64_t width = 0, height = 0;
    char *codec = NULL;
    uint64_t deadline = clock_gettime_nsec_np(CLOCK_UPTIME_RAW) + 20000000000ULL;
    while (command >= 0 && clock_gettime_nsec_np(CLOCK_UPTIME_RAW) < deadline) {
        mpv_event *event = mpv_wait_event(player, .05);
        if (event->event_id == MPV_EVENT_FILE_LOADED) loaded = true;
        if (event->event_id == MPV_EVENT_PROPERTY_CHANGE) {
            mpv_event_property *property = event->data;
            if (property->data && property->format == MPV_FORMAT_INT64) {
                if (event->reply_userdata == 101) width = *(int64_t *)property->data;
                if (event->reply_userdata == 102) height = *(int64_t *)property->data;
            }
            if (property->data && property->format == MPV_FORMAT_STRING &&
                event->reply_userdata == 103) {
                free(codec);
                codec = strdup(*(char **)property->data);
            }
        }
        if (event->event_id == MPV_EVENT_LOG_MESSAGE) {
            mpv_event_log_message *message = event->data;
            fprintf(stderr, "%s: %s", message->prefix, message->text);
        }
        if (event->event_id == MPV_EVENT_END_FILE) {
            mpv_event_end_file *end = event->data;
            end_error = end->error;
            reason = end->reason;
            ended = true;
            break;
        }
        if (!strcmp(mode, "image") && loaded && width > 0 && height > 0 && codec) break;
    }
    char *state = mpv_get_property_string(player, "floe-cache-state");
    printf("IOS_HTTP_SMOKE {\"mode\":\"%s\",\"loaded\":%s,\"ended\":%s,"
           "\"error\":%d,\"reason\":%d,\"cacheVersion\":%lld,\"strictTls\":true,"
           "\"width\":%lld,\"height\":%lld,\"codec\":\"%s\",\"cacheState\":%s}\n", mode, loaded ? "true" : "false",
           ended ? "true" : "false", end_error, reason, (long long)version,
           (long long)width, (long long)height, codec ? codec : "",
           state ? state : "null");
    mpv_free(state);
    free(codec);
    mpv_terminate_destroy(player); // Drain and commit the actual byte writer.
    bool success = loaded && ended && end_error == 0 && reason == MPV_END_FILE_REASON_EOF;
    if (!strcmp(mode, "image")) success = loaded && width > 0 && height > 0;
    if (!strcmp(mode, "reject")) return !loaded && ended && end_error < 0 ? 0 : 9;
    return success ? 0 : 8;
}
