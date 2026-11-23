// Real MoltenVK pixels from a known 10-bit PQ ramp, rendered into an
// IOSurface-backed RGBA16F target. This verifies HDR target negotiation and
// preserves the libmpv subtitle/OSD composition path.
#define main previous_surface_smoke_main
#include "ios_surface_smoke.c"
#undef main
#include <math.h>
#include <stdint.h>

static CVPixelBufferRef make_hdr_pixel_buffer_format(size_t w, size_t h, OSType format)
{
    const void *keys[] = { kCVPixelBufferIOSurfacePropertiesKey,
                           kCVPixelBufferMetalCompatibilityKey };
    CFDictionaryRef properties = CFDictionaryCreate(NULL, NULL, NULL, 0,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    const void *values[] = { properties, kCFBooleanTrue };
    CFDictionaryRef attrs = CFDictionaryCreate(NULL, keys, values, 2,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CVPixelBufferRef result = NULL;
    CVPixelBufferCreate(NULL, w, h, format, attrs, &result);
    CFRelease(attrs);
    CFRelease(properties);
    return result;
}

static CVPixelBufferRef make_hdr_pixel_buffer(size_t w, size_t h)
{
    return make_hdr_pixel_buffer_format(w, h, kCVPixelFormatType_64RGBAHalf);
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

static bool read_gray(CVPixelBufferRef buffer, float pixels[9][3])
{
    if (CVPixelBufferLockBaseAddress(buffer, kCVPixelBufferLock_ReadOnly))
        return false;
    uint16_t *base = CVPixelBufferGetBaseAddress(buffer);
    size_t stride = CVPixelBufferGetBytesPerRow(buffer) / sizeof(uint16_t);
    for (int x = 0; x < 9; x++)
        for (int c = 0; c < 3; c++)
            pixels[x][c] = half_to_float(base[stride * 16 + (x * 32 + 16) * 4 + c]);
    CVPixelBufferUnlockBaseAddress(buffer, kCVPixelBufferLock_ReadOnly);
    return true;
}

static bool verify_passthrough_contract(mpv_handle *mpv, mpv_render_context *ctx,
                                        CVPixelBufferRef buffer)
{
    float reference[9][3], changed[9][3];
    if (render_hdr(ctx, buffer, 1) < 0 || !read_gray(buffer, reference))
        return false;
    // PQ code values for 203 and 4000 nit must survive, not be compressed to
    // a default 1000-nit display. Quantization of the 10-bit YUV input is allowed.
    if (fabsf(reference[5][0] - 0.58069f) > 0.005f ||
        fabsf(reference[8][0] - 0.90257f) > 0.005f)
        return false;
    if (CVPixelBufferLockBaseAddress(buffer, kCVPixelBufferLock_ReadOnly))
        return false;
    uint16_t *base = CVPixelBufferGetBaseAddress(buffer);
    size_t stride = CVPixelBufferGetBytesPerRow(buffer) / sizeof(uint16_t);
    bool primary_ok = true;
    for (int x = 0; x < 3; x++)
        for (int c = 0; c < 3; c++) {
            float value = half_to_float(base[stride * 4 + (x * 32 + 16) * 4 + c]);
            primary_ok &= fabsf(value - (x == c ? 0.58069f : 0.0f)) < 0.01f;
        }
    CVPixelBufferUnlockBaseAddress(buffer, kCVPixelBufferLock_ReadOnly);
    if (!primary_ok) return false;
    // Reproduce the app's conflicting SDR options. The explicit native HDR
    // surface contract wins; the layer must never receive mislabeled SDR.
    const char *options[][2] = {
        {"target-prim", "bt.709"}, {"target-trc", "srgb"}, {"target-peak", "203"},
        {"tone-mapping", "hable"}, {"tone-mapping", "reinhard"},
        {"tone-mapping", "clip"}, {"hdr-compute-peak", "yes"},
    };
    for (unsigned i = 0; i < sizeof(options) / sizeof(options[0]); i++) {
        if (mpv_set_property_string(mpv, options[i][0], options[i][1]) < 0)
            return false;
        for (int n = 0; n < 3; n++)
            if (render_hdr(ctx, buffer, 1) < 0) return false;
        if (!read_gray(buffer, changed)) return false;
        for (int x = 0; x < 9; x++)
            for (int c = 0; c < 3; c++)
                if (fabsf(changed[x][c] - reference[x][c]) > 0.002f)
                    return false;
    }
    if (render_hdr(ctx, buffer, 99) != MPV_ERROR_INVALID_PARAMETER)
        return false;
    for (int n = 0; n < 3; n++)
        if (render_hdr(ctx, buffer, 0) < 0) return false;
    if (!read_gray(buffer, changed) || fabsf(changed[8][0] - reference[8][0]) < 0.02f)
        return false;
    mpv_set_property_string(mpv, "target-prim", "auto");
    mpv_set_property_string(mpv, "target-trc", "auto");
    mpv_set_property_string(mpv, "target-peak", "auto");
    mpv_set_property_string(mpv, "tone-mapping", "auto");
    mpv_set_property_string(mpv, "hdr-compute-peak", "auto");
    puts("LIBMPV_IOS_HDR_CONTRACT_OK PQ values preserved; SDR overrides and tone curves cannot mislabel HDR");
    return true;
}

static bool verify_packed_hdr(mpv_render_context *ctx, CVPixelBufferRef reference)
{
    CVPixelBufferRef packed = make_hdr_pixel_buffer_format(288, 32, kCVPixelFormatType_ARGB2101010LEPacked);
    if (!packed) return false;
    bool ok = true;
    for (int mode = 1; mode <= 2 && ok; mode++) {
        if (render_hdr(ctx, reference, mode) < 0 || render_hdr(ctx, packed, mode) < 0) {
            ok = false;
            break;
        }
        CVPixelBufferLockBaseAddress(reference, kCVPixelBufferLock_ReadOnly);
        CVPixelBufferLockBaseAddress(packed, kCVPixelBufferLock_ReadOnly);
        const uint8_t *a = CVPixelBufferGetBaseAddress(reference);
        const uint8_t *b = CVPixelBufferGetBaseAddress(packed);
        for (int row = 0; row < 2; row++) {
            int y = row == 0 ? 4 : 16;
            for (int x = 0; x < 9; x++) {
                const uint16_t *half = (const uint16_t *)(a + y * CVPixelBufferGetBytesPerRow(reference)) + (x * 32 + 16) * 4;
                uint32_t word = ((const uint32_t *)(b + y * CVPixelBufferGetBytesPerRow(packed)))[x * 32 + 16];
                for (int c = 0; c < 3; c++) {
                    float value = ((word >> ((2 - c) * 10)) & 1023) / 1023.0f;
                    ok &= fabsf(value - half_to_float(half[c])) < 0.003f;
                }
            }
        }
        CVPixelBufferUnlockBaseAddress(packed, kCVPixelBufferLock_ReadOnly);
        CVPixelBufferUnlockBaseAddress(reference, kCVPixelBufferLock_ReadOnly);
    }
    CVPixelBufferRelease(packed);
    if (ok) puts("LIBMPV_IOS_HDR_PACKED_OK BGR10A2 matches RGBA16F primaries and ramp; four bytes per pixel");
    return ok;
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
    if (!verify_passthrough_contract(mpv, ctx, buffer)) return 7;
    if (!verify_packed_hdr(ctx, buffer)) return 8;
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
