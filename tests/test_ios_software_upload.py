"""Exercise the production upload fallback's size and metadata contract."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SoftwareUploadTests(unittest.TestCase):
    def test_bounds_and_preserves_hdr_and_dolby_payload(self):
        patch = ROOT / 'patches/mpv-ios-software-upload-pressure.patch'
        added = '\n'.join(line[1:] for line in patch.read_text().splitlines()
                          if line.startswith('+') and not line.startswith('+++'))
        start = added.index('static struct mp_image *fit_software_upload(')
        depth = 0
        for i in range(added.index('{', start), len(added)):
            depth += (added[i] == '{') - (added[i] == '}')
            if depth == 0:
                function = added[start:i + 1]
                break
        stubs = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <math.h>
#define HAVE_IOS_GL 1
#define HAVE_VULKAN 1
#define PL_HAVE_VULKAN 1
#define MPMAX(a,b) ((a)>(b)?(a):(b))
#define MP_SWS_SWS 1
struct mp_rect {int x0,y0,x1,y1;};
struct mp_image {int w,h,imgfmt,bitdepth;int64_t pts;void *rpu;
  struct {struct mp_rect crop;int transfer,primaries;} params;};
struct mp_sws_context {void *log;int flags,force_scaler;};
struct mp_image_pool {int unused;};
struct ra {void *gpu;};
struct ra_ctx {void *ra;};
struct pl_video {struct ra *ra;struct ra_ctx *ra_ctx;void *log;
 struct mp_sws_context *sw_upload_scaler;struct mp_image_pool *sw_upload_pool;};
static int fail_alloc,fail_scale,allocations,scales;
static const int mp_sws_fast_flags=2;
static struct mp_image dst;
static struct mp_sws_context scaler;
static struct mp_image_pool pool;
static void *ra_pl_get(void *ra){return ra;}
static bool mp_sws_supported_format(int fmt){return fmt==10;}
static struct mp_image_pool *mp_image_pool_new(void *p){return &pool;}
static struct mp_sws_context *mp_sws_alloc(void *p){return &scaler;}
static struct mp_image *mp_image_pool_get(void *p,int fmt,int w,int h){
 allocations++;if(fail_alloc)return NULL;dst=(struct mp_image){.w=w,.h=h,.imgfmt=fmt};return &dst;}
static void mp_image_copy_attributes(struct mp_image *d,struct mp_image *s){
 d->params=s->params;d->bitdepth=s->bitdepth;d->pts=s->pts;d->rpu=s->rpu;}
static int mp_sws_scale(struct mp_sws_context *c,struct mp_image *d,struct mp_image *s){
 scales++;assert(c->force_scaler==MP_SWS_SWS && c->flags==mp_sws_fast_flags);
 assert(d->imgfmt==s->imgfmt && d->bitdepth==s->bitdepth);return fail_scale?-1:0;}
static void talloc_free(void *p){}
'''
        cases = r'''
int main(void){
 int gpu=0,rpu=7;struct ra ra={&gpu};struct ra_ctx ctx={&gpu};
 struct pl_video p={.ra=&ra,.ra_ctx=&ctx};
 struct mp_image src={.w=7680,.h=4320,.imgfmt=10,.bitdepth=10,.pts=12345,.rpu=&rpu,
   .params={.crop={0,0,7680,4320},.transfer=16,.primaries=9}};
 struct mp_image *out=fit_software_upload(&p,&src);
 assert(out && out->w==1920 && out->h==1080 && out->params.crop.x1==1920);
 assert(out->params.transfer==16 && out->params.primaries==9 && out->rpu==&rpu && out->pts==12345);
 src.w=8192;src.h=4096;src.params.crop=(struct mp_rect){0,0,8192,4096};
 out=fit_software_upload(&p,&src);assert(out && (int64_t)out->w*out->h<=1920*1080);
 assert(!(out->w&1) && !(out->h&1) && fabs((double)out->w/out->h-2)<0.01);
 int old=allocations;src.w=1920;src.h=1080;
 assert(!fit_software_upload(&p,&src) && allocations==old);
 src.w=7680;src.h=4320;src.imgfmt=99;
 assert(!fit_software_upload(&p,&src) && allocations==old);
 src.imgfmt=10;p.ra_ctx=NULL;assert(!fit_software_upload(&p,&src));p.ra_ctx=&ctx;
 fail_alloc=1;assert(!fit_software_upload(&p,&src));fail_alloc=0;
 fail_scale=1;assert(!fit_software_upload(&p,&src));
}
'''
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'upload.c'
            source.write_text(stubs + function + cases)
            binary = Path(temp) / 'upload'
            subprocess.run(['cc', '-std=c11', str(source), '-lm', '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
