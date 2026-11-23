// Real MoltenVK pixels from a known 10-bit PQ ramp, without display HDR assumptions.
#define main previous_surface_smoke_main
#include "ios_surface_smoke.c"
#undef main

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    mpv_handle *mpv = mpv_create();
    mpv_render_context *ctx = NULL;
    CVPixelBufferRef buffer = make_pixel_buffer(288, 32);
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
            if(flags&MPV_RENDER_UPDATE_FRAME) { render(ctx,buffer);rendered=loaded; }
        }
        if(rendered && i>70) break;
    }
    int previous_mid = -1;
    for(int mode=0;mode<3;mode++) {
        if(mode==1 && mpv_set_property_string(mpv,"target-peak","100") < 0) return 5;
        if(mode==2 && mpv_set_property_string(mpv,"hdr-compute-peak","no") < 0) return 5;
        for(int i=0;i<4;i++) { mpv_render_context_update(ctx);render(ctx,buffer); }
        CVPixelBufferLockBaseAddress(buffer,0);
        uint8_t *p=CVPixelBufferGetBaseAddress(buffer);
        printf("HDR_GRAY mode=%d:",mode);
        int previous = -1, mid = -1;
        for(int x=0;x<9;x++) {
            uint8_t *px=p+CVPixelBufferGetBytesPerRow(buffer)*16+(x*32+16)*4;
            printf(" %u,%u,%u",px[2],px[1],px[0]);
            if (px[0] <= previous || abs((int)px[0]-px[1]) > 2 ||
                abs((int)px[0]-px[2]) > 2 || (x == 0 && px[0] > 4) ||
                (x == 8 && px[0] < 245)) return 6;
            previous = px[0];
            if (x == 4) mid = px[0];
        }
        if (mode && mid <= previous_mid + 8) return 7;
        previous_mid = mid;
        printf("\n");fflush(stdout);
        CVPixelBufferUnlockBaseAddress(buffer,0);
    }
    mpv_render_context_free(ctx);mpv_terminate_destroy(mpv);CVPixelBufferRelease(buffer);
    if (rendered) puts("LIBMPV_IOS_HDR_OK PQ grayscale, neutral colors and real mapping options");
    return rendered?0:4;
}
