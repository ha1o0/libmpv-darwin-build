#!/usr/bin/env python3
"""Validate the actual tvOS simulator libmpv with decoding, seek and SW frames."""
from pathlib import Path
import argparse, json, shutil, subprocess, tempfile

SOURCE = r'''
#include <mpv/client.h>
#include <mpv/render.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int main(int argc, char **argv) {
 void *lib=dlopen("@rpath/libmpv.2.dylib",RTLD_NOW|RTLD_LOCAL);
 if(!lib) {fprintf(stderr,"%s\n",dlerror());return 1;}
 mpv_handle *(*create)(void)=dlsym(lib,"mpv_create");
 int (*option)(mpv_handle*,const char*,const char*)=dlsym(lib,"mpv_set_option_string");
 int (*init)(mpv_handle*)=dlsym(lib,"mpv_initialize");
 int (*command)(mpv_handle*,const char**)=dlsym(lib,"mpv_command");
 int (*get)(mpv_handle*,const char*,mpv_format,void*)=dlsym(lib,"mpv_get_property");
 int (*set)(mpv_handle*,const char*,const char*)=dlsym(lib,"mpv_set_property_string");
 void (*destroy)(mpv_handle*)=dlsym(lib,"mpv_terminate_destroy");
 int (*rc_create)(mpv_render_context**,mpv_handle*,mpv_render_param*)=dlsym(lib,"mpv_render_context_create");
 int (*render)(mpv_render_context*,mpv_render_param*)=dlsym(lib,"mpv_render_context_render");
 void (*rc_free)(mpv_render_context*)=dlsym(lib,"mpv_render_context_free");
 if(!create||!option||!init||!command||!get||!destroy||!rc_create||!render||!rc_free)return 2;
 mpv_handle *h=create();
 if(!h||option(h,"config","no")<0||option(h,"vo","libmpv")<0||option(h,"ao","null")<0||option(h,"hwdec","no")<0||option(h,"pause","yes")<0||option(h,"secondary-sub-style","independent")<0||init(h)<0)return 3;
 mpv_render_param params[]={{MPV_RENDER_PARAM_API_TYPE,MPV_RENDER_API_TYPE_SW},{0,NULL}};
 mpv_render_context *ctx=NULL;int result=rc_create(&ctx,h,params);if(result<0){fprintf(stderr,"render create %d\n",result);return 4;}
 char name[]="/tmp/bota-tvos-frame-XXXXXX";FILE *file=fdopen(mkstemp(name),"wb");
 fprintf(file,"YUV4MPEG2 W64 H64 F30:1 Ip A1:1 C420jpeg\n");
 for(int n=0;n<120;n++){fwrite("FRAME\n",1,6,file);for(int i=0;i<4096;i++)fputc(130,file);for(int i=0;i<2048;i++)fputc(128,file);}fclose(file);
 const char *load[]={"loadfile",name,NULL};if(command(h,load)<0)return 5;
 int64_t width=0;for(int n=0;n<100;n++){usleep(50000);if(get(h,"video-params/w",MPV_FORMAT_INT64,&width)>=0&&width==64)break;}
 if(width!=64){fprintf(stderr,"tvOS video decode did not produce a frame\n");return 6;}
 int size[2]={64,64};size_t stride=64*4;unsigned char pixels[64*64*4]={0};
 mpv_render_param draw[]={{MPV_RENDER_PARAM_SW_SIZE,size},{MPV_RENDER_PARAM_SW_FORMAT,"rgb0"},{MPV_RENDER_PARAM_SW_STRIDE,&stride},{MPV_RENDER_PARAM_SW_POINTER,pixels},{0,NULL}};
 if(render(ctx,draw)<0)return 7;
 unsigned char *pixel=pixels+(32*64+32)*4;if(pixel[0]<50||pixel[0]>220||pixel[1]<50||pixel[2]<50)return 8;
 printf("LIBMPV_TVOS_VIDEO_RENDER_OK pixel=%d,%d,%d\n",pixel[0],pixel[1],pixel[2]);
 const char *seek[]={"seek","1.5","absolute+exact",NULL};if(command(h,seek)<0)return 9;
 double position=0;for(int n=0;n<100;n++){usleep(50000);render(ctx,draw);if(get(h,"time-pos",MPV_FORMAT_DOUBLE,&position)>=0&&position>=1.5)break;}
 if(position<1.5)return 10;
 printf("LIBMPV_TVOS_SEEK_OK position=%.3f\n",position);
 if(argc>1) {
  if(set(h,"demuxer-lavf-o","allowed_extensions=ALL")<0 || command(h,(const char*[]){"loadfile",argv[1],NULL})<0)return 11;
  for(int n=0;n<300;n++){usleep(50000);render(ctx,draw);if(get(h,"video-params/w",MPV_FORMAT_INT64,&width)>=0&&width>64)break;}
  if(width<=64)return 12;
  printf("LIBMPV_TVOS_WRAPPED_HLS_OK decoded width=%lld\n",(long long)width);
 }
 char hdr[]="/tmp/bota-tvos-hdr-XXXXXX";file=fdopen(mkstemp(hdr),"wb");
 fprintf(file,"YUV4MPEG2 W32 H32 F25:1 Ip A1:1 C420p10\n");
 for(int n=0;n<4;n++){fwrite("FRAME\n",1,6,file);for(int i=0;i<1024;i++){fputc(509&255,file);fputc(509>>8,file);}for(int i=0;i<512;i++){fputc(0,file);fputc(2,file);}}fclose(file);
 if(!set || set(h,"vf","format=colormatrix=bt.2020-ncl:primaries=bt.2020:gamma=pq:max-luma=1000")<0 || command(h,(const char*[]){"loadfile",hdr,NULL})<0)return 13;
 for(int n=0;n<100;n++){usleep(50000);render(ctx,draw);if(get(h,"video-params/w",MPV_FORMAT_INT64,&width)>=0&&width==32)break;}
 if(width!=32)return 14;
 for(int n=0;n<4;n++){usleep(50000);render(ctx,draw);}int baseline=pixel[0];
 if(set(h,"target-peak","100")<0 || set(h,"tone-mapping","clip")<0)return 15;
 for(int n=0;n<4;n++){usleep(50000);render(ctx,draw);}
 printf("LIBMPV_TVOS_HDR_AUDIT PQ100nits baseline=%d after_target100_clip=%d\n",baseline,pixel[0]);
 unlink(hdr);
 rc_free(ctx);destroy(h);unlink(name);return 0;
}
'''
def run(args, **kwargs):
 try: return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT,**kwargs).strip()
 except subprocess.CalledProcessError as error:
  print(error.output,flush=True)
  raise

def dependencies(file):
 return [line.strip().split(' (')[0] for line in run(['otool','-L',str(file)]).splitlines()[1:]]

def native_candidates(mpv):
 roots=run(['nix-store','--query','--requisites',mpv.parent.parent]).splitlines()
 candidates=[f for p in roots if '-tvossimulator-arm64' in p for f in Path(p).glob('lib/*.dylib')]
 queue=[run(['nix-store','--query','--deriver',mpv.parent.parent])];visited=set()
 while queue:
  drv=queue.pop()
  if drv in visited or not drv.endswith('.drv'):continue
  visited.add(drv)
  for p in run(['nix-store','--query','--outputs',drv]).splitlines():candidates.extend(Path(p).glob('lib/*.dylib'))
  queue.extend(p for p in run(['nix-store','--query','--references',drv]).splitlines() if p.endswith('.drv') and '-tvossimulator-arm64' in p)
 return candidates

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mpv',type=Path);parser.add_argument('--runtime');parser.add_argument('--device');parser.add_argument('--wrapped-hls',type=Path);parser.add_argument('--font-fixtures',type=Path);parser.add_argument('--font-video',type=Path);args=parser.parse_args()
 if not args.device and not args.runtime:parser.error('--runtime or --device is required')
 if bool(args.font_fixtures) != bool(args.font_video):parser.error('--font-fixtures and --font-video must be used together')
 mpv=args.mpv.resolve(); candidates=native_candidates(mpv)
 with tempfile.TemporaryDirectory(prefix='bota-tvos-native-') as temporary:
  root=Path(temporary);libs=root/'libs';libs.mkdir();seen={};queue=[mpv]
  while queue:
   file=queue.pop();name=file.name
   if name in seen:continue
   seen[name]=file;shutil.copyfile(file,libs/name);(libs/name).chmod(0o755)
   for dependency in dependencies(file)[1:]:
    if dependency.startswith('/nix/store/'):
     queue.append(Path(dependency))
    elif dependency.startswith('@rpath/'):
     base=Path(dependency).name;matches=[x for x in candidates if x.name==base]
     if not matches and base=='libuchardet.dylib':matches=[x for x in candidates if x.name=='liblibuchardet.dylib']
     matches={p.resolve() for p in matches}
     if len(matches)!=1:raise RuntimeError(f'Missing or ambiguous {base}')
     queue.append(matches.pop())
  # The uchardet CMake import records a public name unlike its build output.
  for name in list(seen):
   if name=='liblibuchardet.dylib':shutil.copyfile(libs/name,libs/'libuchardet.dylib');seen['libuchardet.dylib']=seen[name]
  for name,source in seen.items():
   file=libs/name;subprocess.run(['install_name_tool','-id','@rpath/'+name,str(file)],check=True)
   for dependency in dependencies(file)[1:]:
    if dependency.startswith('/nix/store/'):subprocess.run(['install_name_tool','-change',dependency,'@rpath/'+Path(dependency).name,str(file)],check=True)
   subprocess.run(['codesign','--force','--sign','-',str(file)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  (root/'smoke.c').write_text(SOURCE)
  executable=root/'smoke'
  subprocess.run(['xcrun','--sdk','appletvsimulator','clang','-target','arm64-apple-tvos15.0-simulator','-isysroot',run(['xcrun','--sdk','appletvsimulator','--show-sdk-path']),'-I',str(mpv.parent.parent/'include'),str(root/'smoke.c'),'-Wl,-rpath,@executable_path/libs','-o',str(executable)],check=True)
  subprocess.run(['codesign','--force','--sign','-',str(executable)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  font_executable=None
  if args.font_fixtures:
   font_executable=root/'font-smoke'
   subprocess.run(['xcrun','--sdk','appletvsimulator','clang','-target','arm64-apple-tvos15.0-simulator','-isysroot',run(['xcrun','--sdk','appletvsimulator','--show-sdk-path']),'-I',str(mpv.parent.parent/'include'),str(Path(__file__).with_name('ass_font_resources_smoke.c')),'-Wl,-rpath,@executable_path/libs','-o',str(font_executable)],check=True)
   subprocess.run(['codesign','--force','--sign','-',str(font_executable)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   font_output=root/'font-output';font_output.mkdir();shutil.copyfile(args.font_video,font_output/'font-pixels.mp4')
  device=args.device or run(['xcrun','simctl','create','Bota tvOS libmpv validation','com.apple.CoreSimulator.SimDeviceType.Apple-TV-4K-4th-generation-4K',args.runtime])
  try:
   if not args.device:
    subprocess.run(['xcrun','simctl','boot',device],check=True)
    subprocess.run(['xcrun','simctl','bootstatus',device,'-b'],check=True,timeout=300,stdout=subprocess.DEVNULL)
   output=run(['xcrun','simctl','spawn',device,str(executable),*([str(args.wrapped_hls.resolve())] if args.wrapped_hls else [])],timeout=90);print(output)
   if 'LIBMPV_TVOS_SEEK_OK' not in output:raise RuntimeError('Native playback verification failed')
   if args.wrapped_hls and 'LIBMPV_TVOS_WRAPPED_HLS_OK' not in output:raise RuntimeError('Wrapped HLS verification failed')
   if font_executable:
    import re
    for secondary in (False,True):
     font_report=run(['xcrun','simctl','spawn',device,str(font_executable),str(args.font_fixtures.resolve()),str(font_output),*(['secondary'] if secondary else [])],timeout=90);print(font_report)
     hashes=re.findall(r'LIBMPV_ASS_PIXELS slot=\w+ family=.*? hash=(\d+)',font_report)
     if len(hashes)!=3 or len(set(hashes))!=3:raise RuntimeError('Subtitle font pixels did not distinguish all families')
    print('LIBMPV_TVOS_SUBTITLE_FONT_RESOURCES_OK primary and independent secondary families rendered distinctly')
  finally:
   if not args.device:
    subprocess.run(['xcrun','simctl','shutdown',device],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['xcrun','simctl','delete',device],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
if __name__=='__main__':main()
