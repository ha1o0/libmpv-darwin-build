// Compare actual P5 RPU CPU output against the same frame through libplacebo.
#define main original_surface_main
#include "ios_surface_smoke.c"
#undef main
#include <math.h>
#include <stdlib.h>

static CVPixelBufferRef packed_buffer(void) {
    CFDictionaryRef empty = CFDictionaryCreate(NULL,NULL,NULL,0,
        &kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
    const void *keys[]={kCVPixelBufferIOSurfacePropertiesKey,kCVPixelBufferMetalCompatibilityKey};
    const void *values[]={empty,kCFBooleanTrue};
    CFDictionaryRef attrs=CFDictionaryCreate(NULL,keys,values,2,
        &kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
    CVPixelBufferRef pixel=NULL;
    CVPixelBufferCreate(NULL,640,360,kCVPixelFormatType_ARGB2101010LEPacked,attrs,&pixel);
    CFRelease(attrs);CFRelease(empty);return pixel;
}
static int gpu(mpv_render_context *ctx,CVPixelBufferRef p) {
    int block=0,color=1;
    return mpv_render_context_render(ctx,(mpv_render_param[]){
        {MPV_RENDER_PARAM_IOSURFACE,CVPixelBufferGetIOSurface(p)},
        {MPV_RENDER_PARAM_IOSURFACE_COLOR_SPACE,&color},
        {MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME,&block},{0}});
}
static int cpu(mpv_render_context *ctx,CVPixelBufferRef p,int mode) {
    int skip=1,block=0,size[]={640,360};size_t stride=CVPixelBufferGetBytesPerRow(p);
    CVPixelBufferLockBaseAddress(p,0);
    int status=mpv_render_context_render(ctx,(mpv_render_param[]){
        {MPV_RENDER_PARAM_IOS_PIP_SW,&mode},{MPV_RENDER_PARAM_SKIP_RENDERING,&skip},
        {MPV_RENDER_PARAM_SW_SIZE,size},{MPV_RENDER_PARAM_SW_FORMAT,"x2rgb10le"},
        {MPV_RENDER_PARAM_SW_STRIDE,&stride},{MPV_RENDER_PARAM_SW_POINTER,CVPixelBufferGetBaseAddress(p)},
        {MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME,&block},{0}});
    CVPixelBufferUnlockBaseAddress(p,0);return status;
}
static bool colors(CVPixelBufferRef a,CVPixelBufferRef b) {
    CVPixelBufferLockBaseAddress(a,0);CVPixelBufferLockBaseAddress(b,0);
    const uint8_t *pa=CVPixelBufferGetBaseAddress(a),*pb=CVPixelBufferGetBaseAddress(b);
    double error=0,energy=0;int count=0,bad=0;
    for(int y=10;y<350;y+=2)for(int x=10;x<630;x+=2) {
        uint32_t va=((uint32_t*)(pa+y*CVPixelBufferGetBytesPerRow(a)))[x];
        uint32_t vb=((uint32_t*)(pb+y*CVPixelBufferGetBytesPerRow(b)))[x];
        for(int c=0;c<3;c++) {
            double p=((va>>(c*10))&1023)/1023.0,q=((vb>>(c*10))&1023)/1023.0;
            error+=fabs(p-q);energy+=p;bad+=fabs(p-q)>.06;count++;
        }
    }
    CVPixelBufferUnlockBaseAddress(a,0);CVPixelBufferUnlockBaseAddress(b,0);
    fprintf(stderr,"DV_PIP_COLOR mean=%.6f bad=%.6f energy=%.6f\n",error/count,(double)bad/count,energy/count);
    return energy/count>.03 && error/count<.02 && (double)bad/count<.04;
}
static int differences(CVPixelBufferRef a,CVPixelBufferRef b) {
    CVPixelBufferLockBaseAddress(a,0);CVPixelBufferLockBaseAddress(b,0);
    int changed=0;for(int y=0;y<360;y++)for(int x=0;x<640;x++){
        uint32_t va=((uint32_t*)((uint8_t*)CVPixelBufferGetBaseAddress(a)+y*CVPixelBufferGetBytesPerRow(a)))[x];
        uint32_t vb=((uint32_t*)((uint8_t*)CVPixelBufferGetBaseAddress(b)+y*CVPixelBufferGetBytesPerRow(b)))[x];
        changed+=va!=vb;
    }
    CVPixelBufferUnlockBaseAddress(a,0);CVPixelBufferUnlockBaseAddress(b,0);return changed;
}
static bool seek_frame(mpv_handle *mpv,mpv_render_context *ctx,CVPixelBufferRef p,atomic_bool *dirty,const char *time) {
    if(mpv_command(mpv,(const char*[]){"seek",time,"absolute+exact",NULL})<0)return false;
    bool rendered=false;
    for(int n=0;n<200;n++) {
        mpv_wait_event(mpv,.02);
        if(atomic_exchange(dirty,false)){
            if(mpv_render_context_update(ctx)&MPV_RENDER_UPDATE_FRAME) rendered=gpu(ctx,p)>=0;
        }
        double position=0;
        if(rendered && mpv_get_property(mpv,"time-pos",MPV_FORMAT_DOUBLE,&position)>=0 && fabs(position-atof(time))<.08)return true;
    }
    return false;
}
int main(int argc,char **argv) {
    if(argc!=3)return 1;
    mpv_handle *mpv=mpv_create();mpv_render_context *ctx=NULL;atomic_bool dirty=false;
    CVPixelBufferRef reference=packed_buffer(),composed=packed_buffer(),baseline=packed_buffer();
    const char *options[][2]={{"config","no"},{"vo","libmpv"},{"ao","null"},{"pause","yes"},
      {"hwdec","no"},{"keep-open","yes"},{"sub-pos","60"},{"sub-font-size","45"},
      {"scale","bilinear"},{"dscale","bilinear"},{"cscale","bilinear"},{"dither-depth","no"}};
    for(unsigned i=0;i<sizeof(options)/sizeof(options[0]);i++) if(mpv_set_option_string(mpv,options[i][0],options[i][1])<0)return 2;
    if(mpv_initialize(mpv)<0)return 3;
    if(mpv_render_context_create(&ctx,mpv,(mpv_render_param[]){{MPV_RENDER_PARAM_API_TYPE,"iosurface"},{MPV_RENDER_PARAM_BACKEND,"gpu-next"},{0}})<0)return 4;
    mpv_render_context_set_update_callback(ctx,updated,&dirty);
    int capability=0;
    if(mpv_render_context_get_info(ctx,(mpv_render_param){MPV_RENDER_PARAM_IOS_PIP_SW,&capability})<0||capability<4)return 5;
    if(mpv_command(mpv,(const char*[]){"loadfile",argv[1],NULL})<0)return 6;
    bool loaded=false;
    for(int n=0;n<250;n++){
        mpv_event *e=mpv_wait_event(mpv,.02);loaded|=e->event_id==MPV_EVENT_FILE_LOADED;
        if(atomic_exchange(&dirty,false) && (mpv_render_context_update(ctx)&MPV_RENDER_UPDATE_FRAME)) gpu(ctx,reference);
        if(loaded&&n>40)break;
    }
    for(int phase=0;phase<2;phase++) {
        if(!seek_frame(mpv,ctx,reference,&dirty,phase?"7":"3"))return 7;
        int status=cpu(ctx,baseline,4);
        if(status<0){fprintf(stderr,"DV CPU compose error %d\n",status);return 8;}
        if(!colors(reference,baseline))return 9;
    }
    double before=0,after=0;int paused=0;
    mpv_get_property(mpv,"time-pos",MPV_FORMAT_DOUBLE,&before);
    if(mpv_command(mpv,(const char*[]){"sub-add",argv[2],"select",NULL})<0)return 10;
    bool subtitles=false;
    for(int n=0;n<100;n++){
        mpv_wait_event(mpv,.02);
        // Software-only consume in background; no primary GPU operations.
        if(cpu(ctx,composed,3)>=0 && differences(baseline,composed)>50){subtitles=true;break;}
    }
    if(!subtitles)return 11;
    if(mpv_get_property(mpv,"time-pos",MPV_FORMAT_DOUBLE,&after)<0||fabs(after-before)>.05||
       mpv_get_property(mpv,"pause",MPV_FORMAT_FLAG,&paused)<0||!paused)return 12;
    if(mpv_set_property_string(mpv,"sid","no")<0)return 13;
    for(int n=0;n<5;n++) {mpv_wait_event(mpv,.02);gpu(ctx,reference);}
    if(cpu(ctx,composed,4)<0||!colors(reference,composed))return 14;
    // Software path must never change its source to a fake normal-YUV frame.
    char *matrix=mpv_get_property_string(mpv,"video-params/colormatrix");
    bool dovi=matrix && !strcmp(matrix,"dolbyvision");mpv_free(matrix);
    if(!dovi)return 15;
    puts("LIBMPV_IOS_DOVI_PIP_OK real P5 CPU/GPU colors, 10-bit subtitles, paused seek and return");
    mpv_render_context_free(ctx);mpv_terminate_destroy(mpv);
    CVPixelBufferRelease(reference);CVPixelBufferRelease(composed);CVPixelBufferRelease(baseline);
    return 0;
}
