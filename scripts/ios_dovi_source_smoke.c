#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <Mpv/client.h>

int main(int argc, char **argv) {
    if (argc != 4) { fprintf(stderr, "usage: source-spec p8.1.mkv p7-mel.mp4 p7-fel.mp4\n"); return 64; }
    mpv_handle *h = mpv_create();
    if (!h) return 1;
    mpv_set_option_string(h, "config", "no");
    mpv_set_option_string(h, "vo", "null");
    mpv_set_option_string(h, "ao", "null");
    mpv_set_option_string(h, "hwdec", "no");
    mpv_set_option_string(h, "keep-open", "yes");
    mpv_set_option_string(h, "idle", "yes");
    if (mpv_initialize(h) < 0) return 2;
    const char *files[] = {"regular.mkv", "mel.mp4", "fel.mp4"};
    const char *expected[] = {"\"compatibility-id\":1", "\"enhancement-layer\":\"MEL\"", "\"enhancement-layer\":\"FEL\""};
    for (int i = 0; i < 3; i++) {
        const char *cmd[] = {"loadfile", argv[i + 1], "replace", NULL};
        if (mpv_command(h,cmd) < 0) return 3;
        int ok = 0;
        for (int attempt=0; attempt<100; attempt++) {
            mpv_wait_event(h,.05);
            char *value = mpv_get_property_string(h, "video-dolby-vision");
            if (value && strstr(value,expected[i])) {
                printf("DV_SPEC %s %s\n",files[i],value);
                ok=1;
            }
            mpv_free(value);
            if (ok) break;
        }
        if (!ok) { fprintf(stderr,"Missing expected metadata for %s\n",files[i]); return 10+i; }
    }
    const char *stop[]={"stop",NULL};
    mpv_command(h,stop);
    for (int i=0;i<10;i++) mpv_wait_event(h,.05);
    char *stale=mpv_get_property_string(h,"video-dolby-vision");
    if(stale) {fprintf(stderr,"Stale DV metadata after stop: %s\n",stale);return 20;}
    mpv_terminate_destroy(h);
    printf("DV_SOURCE_METADATA_OK\n");
    return 0;
}
