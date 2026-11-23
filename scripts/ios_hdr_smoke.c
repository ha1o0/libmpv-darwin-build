// Real MoltenVK pixels from a known 10-bit PQ ramp, rendered into an
// IOSurface-backed RGBA16F target. This verifies HDR target negotiation and
// preserves the libmpv subtitle/OSD composition path.
#define main previous_surface_smoke_main
#include "ios_surface_smoke.c"
#undef main
#include <math.h>
#include <stdint.h>

static CVPixelBufferRef make_hdr_pixel_buffer(size_t w, size_t h)
{
    const void *keys[] = { kCVPixelBufferIOSurfacePropertiesKey,
                           kCVPixelBufferMetalCompatibilityKey };
    CFDictionaryRef properties = CFDictionaryCreate(NULL, NULL, NULL, 0,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    const void *values[] = { properties, kCFBooleanTrue };
    CFDictionaryRef attrs = CFDictionaryCreate(NULL, keys, values, 2,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CVPixelBufferRef result = NULL;
    CVPixelBufferCreate(NULL, w, h, kCVPixelFormatType_64RGBAHalf, attrs, &result);
    CFRelease(attrs);
    CFRelease(properties);
    return result;
}

static float half_to_float(uint16_t value)
{
    const uint32_t sign = (value >> 15) & 1;
    const uint32_t exponent = (value >> 10) & 0x1f;
    const uint32_t fraction = value & 0x3ff;
    uint32_t bits;
    if (exponent == 0) {
        if (!fraction) bits = sign << 31;
        else {
            float result = ldexpf((float)fraction, -24);
            return sign ? -result : result;
        }
    } else if (exponent == 31) {
        bits = (sign << 31) | 0x7f800000u | (fraction << 13);
    } else {
        bits = (sign << 31) | ((exponent + 112) << 23) | (fraction << 13);
    }
    float result;
    memcpy(&result, &bits, sizeof(result));
    return result;
}

static int render_hdr(mpv_render_context *ctx, CVPixelBufferRef buffer, int color_space)
{
    int block = 0;
    return mpv_render_context_render(ctx, (mpv_render_param[]) {
        { MPV_RENDER_PARAM_IOSURFACE, CVPixelBufferGetIOSurface(buffer) },
        { MPV_RENDER_PARAM_IOSURFACE_COLOR_SPACE, &color_space },
        { MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block }, { 0 },
    });
}

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    mpv_handle *mpv = mpv_create();
    mpv_render_context *ctx = NULL;
    CVPixelBufferRef buffer = make_hdr_pixel_buffer(288, 32);
    atomic_bool dirty = false;
    int advanced = 1;
    mpv_set_option_string(mpv, "config", "no");
    mpv_set_option_string(mpv, "ao", "null");
    mpv_set_option_string(mpv, "vo", "libmpv");
    mpv_set_option_string(mpv, "pause", "yes");
    mpv_set_option_string(mpv, "keep-open", "yes");
    mpv_set_option_string(mpv, "hwdec", "no");
    mpv_set_option_string(mpv, "vf", "format=colormatrix=bt.2020-ncl:primaries=bt.2020:gamma=pq:max-luma=4000");
    mpv_set_option_string(mpv, "hdr-compute-peak", "auto");
    mpv_set_option_string(mpv, "hdr-peak-percentile", "100");
    mpv_set_option_string(mpv, "msg-level", "all=warn");
    if (mpv_initialize(mpv) < 0) return 2;
    if (mpv_render_context_create(&ctx,mpv,(mpv_render_param[]) {
        {MPV_RENDER_PARAM_API_TYPE,"iosurface"},{MPV_RENDER_PARAM_BACKEND,"gpu-next"},
        {MPV_RENDER_PARAM_ADVANCED_CONTROL,&advanced},{0}})<0) return 2;
    mpv_render_context_set_update_callback(ctx, updated, &dirty);
    if (mpv_command(mpv,(const char *[]){"loadfile",argv[1],NULL})<0) return 3;
    bool loaded=false,rendered=false;
    for (int i=0;i<200;i++) {
        mpv_event *event=mpv_wait_event(mpv,0.025);
        loaded|=event->event_id==MPV_EVENT_FILE_LOADED;
        if(atomic_exchange(&dirty,false)) {
            uint64_t flags=mpv_render_context_update(ctx);
            if(flags&MPV_RENDER_UPDATE_FRAME) { render_hdr(ctx,buffer,1);rendered=loaded; }
        }
        if(rendered && i>70) break;
    }
    for (int color_space = 1; color_space <= 2; color_space++) {
        for (int i = 0; i < 4; i++) {
            mpv_render_context_update(ctx);
            if (render_hdr(ctx, buffer, color_space) < 0) return 5;
        }
        CVPixelBufferLockBaseAddress(buffer, 0);
        uint16_t *p = (uint16_t *)CVPixelBufferGetBaseAddress(buffer);
        size_t stride = CVPixelBufferGetBytesPerRow(buffer) / sizeof(uint16_t);
        printf("HDR_FLOAT_GRAY colorspace=%s:", color_space == 1 ? "HDR10" : "HLG");
        float previous = -1.0f;
        for (int x = 0; x < 9; x++) {
            uint16_t *px = p + stride * 16 + (x * 32 + 16) * 4;
            float r = half_to_float(px[0]);
            float g = half_to_float(px[1]);
            float b = half_to_float(px[2]);
            printf(" %.5f,%.5f,%.5f", r, g, b);
            if (!(r > previous) || fabsf(r - g) > 0.02f || fabsf(r - b) > 0.02f ||
                !isfinite(r) || (x == 0 && r > 0.02f) || (x == 8 && r < 0.9f))
                return 6;
            previous = r;
        }
        printf("\n");
        fflush(stdout);
        CVPixelBufferUnlockBaseAddress(buffer, 0);
    }
    mpv_render_context_free(ctx);mpv_terminate_destroy(mpv);CVPixelBufferRelease(buffer);
    if (rendered) puts("LIBMPV_IOS_HDR_OK RGBA16F HDR10/HLG targets, neutral colors");
    return rendered?0:4;
}
