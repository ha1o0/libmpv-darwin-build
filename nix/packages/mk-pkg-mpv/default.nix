{
  pkgs ? import ../../utils/default/pkgs.nix,
  os ? import ../../utils/default/os.nix,
  arch ? pkgs.callPackage ../../utils/default/arch.nix { },
  variant ? import ../../utils/default/variant.nix,
}:

let
  name = "mpv";
  variants = import ../../utils/constants/variants.nix;
  oses = import ../../utils/constants/oses.nix;
  macosNativeVideo = os == oses.macos && variant == variants.video;
  packageLocks = import ../../../packages.lock.nix;
  packageLock =
    if macosNativeVideo then
      packageLocks.mpvMacosNativeVideo
    else
      packageLocks.mpv;
  inherit (packageLock) version;
  swiftTargetArch = if arch == "amd64" then "x86_64" else "arm64";
  macosTargetSystem = if arch == "amd64" then "x86_64-darwin" else "aarch64-darwin";
  macosTargetPkgs =
    if pkgs.stdenv.hostPlatform.system == macosTargetSystem then
      pkgs
    else if arch == "amd64" then
      pkgs.pkgsCross.x86_64-darwin
    else
      pkgs.pkgsCross.aarch64-darwin;
  callPackage = pkgs.lib.callPackageWith {
    inherit
      pkgs
      os
      arch
      variant
      ;
  };
  nativeFile = callPackage ../../utils/native-file/default.nix { };
  crossFile = callPackage ../../utils/cross-file/default.nix { };
  mpvCrossFile =
    if macosNativeVideo then
      pkgs.runCommand "mk-mpv-cross-file-${os}-${arch}.ini" { } ''
        cp ${crossFile} cross-file.ini
        sed -i 's/-mmacosx-version-min=10.9/-mmacosx-version-min=11.0/g' cross-file.ini
        cp cross-file.ini $out
      ''
    else
      crossFile;
  xcrun =
    if macosNativeVideo then
      pkgs.writeShellScriptBin "xcrun" ''
        set -eu
        xcode=${pkgs.darwin.xcode}
        sdk="$xcode/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
        swiftc="$xcode/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc"
        xcodebuild="$xcode/Contents/Developer/usr/bin/xcodebuild"

        if [ "$#" -eq 2 ] && [ "$1" = "-find" ] && [ "$2" = "swiftc" ]; then
          printf '%s\n' "$swiftc"
          exit 0
        fi
        if [ "$#" -eq 3 ] && [ "$1" = "--sdk" ] && [ "$2" = "macosx" ] && [ "$3" = "--show-sdk-path" ]; then
          printf '%s\n' "$sdk"
          exit 0
        fi
        if [ "$#" -eq 3 ] && [ "$1" = "--sdk" ] && [ "$2" = "macosx" ] && [ "$3" = "--show-sdk-version" ]; then
          exec "$xcodebuild" -sdk macosx -version ProductVersion
        fi
        echo "unsupported xcrun invocation: $*" >&2
        exit 1
      ''
    else
      null;
  xctoolchainLipo = callPackage ../../utils/xctoolchain/lipo.nix { };
  xctoolchainOtool = callPackage ../../utils/xctoolchain/otool.nix { };
  ffmpeg = callPackage ../mk-pkg-ffmpeg/default.nix { };
  uchardet = callPackage ../mk-pkg-uchardet/default.nix { };
  libass = callPackage ../mk-pkg-libass/default.nix { };
  libplacebo = callPackage ../mk-pkg-libplacebo/default.nix { };
  vulkanHeaders = callPackage ../mk-pkg-vulkan-headers/default.nix { };

  nativeBuildInputs = [
    pkgs.meson
    pkgs.ninja
    pkgs.pkg-config
    pkgs.python3
    xctoolchainLipo
  ]
  ++ pkgs.lib.optionals macosNativeVideo [
    xcrun
    xctoolchainOtool
  ];

  pname = import ../../utils/name/package.nix name;
  src = callPackage ../../utils/fetch-tarball/default.nix {
    name = "${pname}-source-${version}";
    inherit (packageLock) url sha256;
  };
  patchedSource = pkgs.runCommand "${pname}-patched-source-${variant}-${version}" { } ''
    cp -r ${src} src
    export src=$PWD/src
    chmod -R 777 $src

    cd $src
    patch -p1 <${../../../patches/mpv-fix-libmpv-gpu-next-color-management.patch}
    ${pkgs.lib.optionalString macosNativeVideo ''
      patch -p1 <${../../../patches/mpv-macos-embedded-macvk.patch}
    ''}

    # 仅在编译 iOS 平台时，应用 CoreAudio 兼容降级补丁
    ${if os == "ios" || os == "iossimulator" then ''
      # 1. 忽略 iOS 上编译 ao_avfoundation.m 产生的 availability 警告
      sed -i '1s/^/#pragma clang diagnostic ignored "-Wunguarded-availability-new"\n/' audio/out/ao_avfoundation.m

      # 2. 在 iOS 上屏蔽不被支持 of setAudioOutputDeviceUniqueID: 属性调用
      sed -i 's|\[p->renderer setAudioOutputDeviceUniqueID:(NSString\*)cfstr_from_cstr(ao->device)\];|#if TARGET_OS_OSX\n        [p->renderer setAudioOutputDeviceUniqueID:(NSString*)cfstr_from_cstr(ao->device)];\n#endif|g' audio/out/ao_avfoundation.m

      # 3. 将 ao_coreaudio_utils.h 中所有 macOS CoreAudio HAL 的独占声明在 iOS 平台排除
      sed -i 's|#if HAVE_COREAUDIO \|\| HAVE_AVFOUNDATION|#if HAVE_COREAUDIO|g' audio/out/ao_coreaudio_utils.h

      # 4. 在 ao_coreaudio_chmap.h 中将 macOS 独占的 AudioDeviceID 函数声明拆分并用 HAVE_COREAUDIO 保护，保留 iOS 所需的 layout 函数
      sed -i 's|void ca_log_layout(struct ao \*ao, int l, AudioChannelLayout \*layout);|void ca_log_layout(struct ao *ao, int l, AudioChannelLayout *layout);\n#endif\n#if HAVE_COREAUDIO|g' audio/out/ao_coreaudio_chmap.h

      # 5. 将 ao_coreaudio_properties.c/.h 的全部内容用 HAVE_COREAUDIO 保护，在 iOS 下编译为空文件避免缺少类型报错
      sed -i '1s/^/#if HAVE_COREAUDIO\n/' audio/out/ao_coreaudio_properties.c
      echo "#endif" >> audio/out/ao_coreaudio_properties.c
      sed -i '1s/^/#if HAVE_COREAUDIO\n/' audio/out/ao_coreaudio_properties.h
      echo "#endif" >> audio/out/ao_coreaudio_properties.h

      # 6. 将 ao_coreaudio_chmap.c 中需要 AudioDeviceID (macOS HAL) 的后半段实现部分在 iOS 下用 HAVE_COREAUDIO 保护起来
      sed -i 's|static AudioChannelLayout\* ca_query_layout(|#if HAVE_COREAUDIO\nstatic AudioChannelLayout* ca_query_layout(|g' audio/out/ao_coreaudio_chmap.c
      echo "#endif" >> audio/out/ao_coreaudio_chmap.c

      # 7. 忽略 iOS 上编译 ao_coreaudio_utils.c 时缺失的 <CoreAudio/HostTime.h> 头文件
      sed -i 's|#include <CoreAudio/HostTime.h>|#if HAVE_COREAUDIO\n#include <CoreAudio/HostTime.h>\n#endif|g' audio/out/ao_coreaudio_utils.c

      # 8. 将 ao_coreaudio_utils.c 前段 macOS 独享的硬件查询逻辑用 HAVE_COREAUDIO 保护起来
      sed -i 's|static bool ca_is_output_device(|#if HAVE_COREAUDIO\nstatic bool ca_is_output_device(|g' audio/out/ao_coreaudio_utils.c
      sed -i 's|bool check_ca_st(|#endif\n\nbool check_ca_st(|g' audio/out/ao_coreaudio_utils.c

      # 9. 将 ao_coreaudio_utils.c 后段 macOS 独享的硬件流锁定与混合设置在 iOS 下屏蔽
      sed -i 's|bool ca_stream_supports_compressed(|#if HAVE_COREAUDIO\nbool ca_stream_supports_compressed(|g' audio/out/ao_coreaudio_utils.c
      echo "#endif" >> audio/out/ao_coreaudio_utils.c

      # 10. 在 iOS 下将 ca_get_latency 延迟计算强制降级到 mach_absolute_time 兼容分支，避开缺失的 HostTime API
      sed -i 's|HAVE_COREAUDIO \|\| HAVE_AVFOUNDATION|HAVE_COREAUDIO|g' audio/out/ao_coreaudio_utils.c
    '' else ""}

    # libmpv is embedded in an existing Flutter NSApplication. Do not create
    # mpv's internal Cocoa client (and its menu/AppHub) in any macOS variant.
    ${if os == oses.macos then ''
      sed -i '/#if HAVE_COCOA/ { N; N; s|#if HAVE_COCOA\n    mpv_handle \*ctx = mp_new_client(mpctx->clients, "mac");\n    cocoa_set_mpv_handle(ctx);|#if HAVE_COCOA \&\& HAVE_SWIFT \&\& HAVE_CPLAYER\n    mpv_handle *ctx = mp_new_client(mpctx->clients, "mac");\n    cocoa_set_mpv_handle(ctx);| }' player/main.c
    '' else ""}

    # macOS audio keeps Cocoa symbols as stubs because Swift is disabled.
    ${if os == oses.macos && variant != variants.video then ''
      sed -i '/void cocoa_init_cocoa_cb(void)/,/^#endif/ { s/^#endif/#else\nvoid cocoa_init_media_keys(void) {}\nvoid cocoa_uninit_media_keys(void) {}\nvoid cocoa_set_input_context(struct input_ctx *input_context) { (void)input_context; }\nvoid cocoa_set_mpv_handle(struct mpv_handle *ctx) { (void)ctx; }\nvoid cocoa_init_cocoa_cb(void) {}\nint cocoa_main(int argc, char *argv[]) { (void)argc; (void)argv; return 0; }\n#endif/ }' osdep/mac/app_bridge.m
    '' else ""}

    cat << 'EOF' > player/clipboard/clipboard-mac.m
#include "config.h"
#include "clipboard.h"

#if HAVE_SWIFT
#include "osdep/mac/swift.h"

struct clipboard_mac_priv {
    Clipboard *clipboard;
};

static int init(struct clipboard_ctx *cl, struct clipboard_init_params *params)
{
    struct clipboard_mac_priv *p = cl->priv = talloc_zero(cl, struct clipboard_mac_priv);
    p->clipboard = [[Clipboard alloc] init];
    return CLIPBOARD_SUCCESS;
}

static bool data_changed(struct clipboard_ctx *cl)
{
    struct clipboard_mac_priv *p = cl->priv;
    return [p->clipboard changed];
}

static int get_data(struct clipboard_ctx *cl, struct clipboard_access_params *params,
                    struct clipboard_data *out, void *talloc_ctx)
{
    struct clipboard_mac_priv *p = cl->priv;
    return [p->clipboard getWithParams:params out:out tallocCtx:talloc_ctx];
}

static int set_data(struct clipboard_ctx *cl, struct clipboard_access_params *params,
                    struct clipboard_data *data)
{
    struct clipboard_mac_priv *p = cl->priv;
    return [p->clipboard setWithParams:params data:data];
}

const struct clipboard_backend clipboard_backend_mac = {
    .name = "mac",
    .desc = "macOS clipboard",
    .init = init,
    .data_changed = data_changed,
    .get_data = get_data,
    .set_data = set_data,
};
#else
static int stub_init(struct clipboard_ctx *cl, struct clipboard_init_params *params)
{
    (void)cl;
    (void)params;
    return CLIPBOARD_UNAVAILABLE;
}
const struct clipboard_backend clipboard_backend_mac = {
    .name = "mac",
    .desc = "macOS clipboard (stub)",
    .init = stub_init,
};
#endif
EOF
    if [ "${variant}" == "${variants.audio}" ]; then
      patch -p1 <${../../../patches/mpv-remove-libass.patch}
      find sub -type f \( -name "*.c" -o -name "*.h" \) -exec sed -i 's/<ass\/ass.h>/"sub\/ass.h"/g' {} +
      find sub -type f \( -name "*.c" -o -name "*.h" \) -exec sed -i 's/<ass\/ass_types.h>/"sub\/ass_types.h"/g' {} +
    fi

    # 动态配置 libplacebo 在 meson.build 里的开关与链接关系
    ${if macosNativeVideo then ''
      # macOS 视频变体：保持启用 libplacebo
    '' else ''
      # Keep the compatibility shims used by the existing iOS and macOS audio
      # builds, which compile against the older libplacebo from nixpkgs.
      sed -i 's/>=7.360.1/>=7.349.0/g' meson.build
      sed -i 's|#include "csputils.h"|#include "csputils.h"\n#ifndef PL_COLOR_SYSTEM_YCGCO_RE\n#define PL_COLOR_SYSTEM_YCGCO_RE PL_COLOR_SYSTEM_YCGCO\n#endif\n#ifndef PL_COLOR_SYSTEM_YCGCO_RO\n#define PL_COLOR_SYSTEM_YCGCO_RO PL_COLOR_SYSTEM_YCGCO\n#endif|g' video/csputils.c
      sed -i 's|#include "mp_image.h"|#include "mp_image.h"\n#ifndef PL_COLOR_SYSTEM_YCGCO_RE\n#define PL_COLOR_SYSTEM_YCGCO_RE PL_COLOR_SYSTEM_YCGCO\n#endif\n#ifndef PL_COLOR_SYSTEM_YCGCO_RO\n#define PL_COLOR_SYSTEM_YCGCO_RO PL_COLOR_SYSTEM_YCGCO\n#endif|g' video/mp_image.c
      sed -i 's|#include <libplacebo/renderer.h>|#include <libplacebo/renderer.h>\n#ifndef PL_CLEAR_BLUR\n#define PL_CLEAR_BLUR PL_CLEAR_COLOR\n#define NO_BACKGROUND_BLUR 1\n#endif|g' video/out/vo_gpu_next.c
      sed -i 's|pars->params.blur_radius = p->next_opts->background_blur_radius;|#ifndef NO_BACKGROUND_BLUR\n    pars->params.blur_radius = p->next_opts->background_blur_radius;\n#endif|g' video/out/vo_gpu_next.c

      # 其他变体：禁用 libplacebo 特征，剔除链接依赖，仅保留头文件编译
      sed -i "s/libplacebo = dependency('libplacebo'/libplacebo = dependency('libplacebo', required: false/g" meson.build
      sed -i "s/'libplacebo': true/'libplacebo': false/g" meson.build
      sed -i "s/libplacebo,//g" meson.build

      # 复制头文件到本地，防止非 macOS video 变体下编译报错
      cp -r ${libplacebo}/include/libplacebo ./
    ''}
    cd -

    cp -r $src $out
  '';
  fixedSource = callPackage ../../utils/patch-shebangs/default.nix {
    name = "${pname}-fixed-source-${variant}-${version}";
    src = patchedSource;
    inherit nativeBuildInputs;
  };
in

pkgs.stdenvNoCC.mkDerivation {
  name = "${pname}-${os}-${arch}-${variant}-${version}";
  pname = pname;
  inherit version;
  src = fixedSource;
  dontUnpack = true;
  enableParallelBuilding = true;
  inherit nativeBuildInputs;
  buildInputs =
    [ ffmpeg ]
    ++ pkgs.lib.optionals macosNativeVideo [
      libplacebo
      macosTargetPkgs.shaderc.lib
      vulkanHeaders
      macosTargetPkgs.vulkan-loader
    ]
    ++ pkgs.lib.optionals (variant == "video") [
      uchardet
      libass
    ];
  preConfigure = pkgs.lib.optionalString macosNativeVideo ''
    export PATH=${xcrun}/bin:$PATH
    export DEVELOPER_DIR=${pkgs.darwin.xcode}/Contents/Developer
  '';
  configurePhase = ''
    export PKG_CONFIG_LIBDIR=""
    DISABLE_ALL_OPTIONS=(
      `# booleans`
      -Dgpl=false `# GPL (version 2 or later) build`
      -Dcplayer=false `# mpv CLI player`
      -Dlibmpv=false `# libmpv library`
      -Dbuild-date=false `# whether to include binary compile time`
      -Dtests=false `# unit tests (development only)`

      `# misc features`
      -Dcdda=disabled `# cdda support (libcdio)`
      -Dcplugins=disabled `# C plugins`
      -Ddvbin=disabled `# DVB input module`
      -Ddvdnav=disabled `# dvdnav support`
      -Diconv=disabled `# iconv`
      -Djavascript=disabled `# Javascript (MuJS backend)`
      -Dlcms2=disabled `# LCMS2 support`
      -Dlibarchive=disabled `# libarchive wrapper for reading zip files and more`
      -Dlibavdevice=disabled `# libavdevice`
      -Dlibbluray=disabled `# Bluray support`
      -Dlibcurl=disabled `# libcurl-based stream backend`
      -Dlua=disabled `# Lua`
      -Dpthread-debug=disabled `# pthread runtime debugging wrappers`
      -Drubberband=disabled `# librubberband support`
      -Dsdl2-gamepad=disabled `# SDL2 gamepad input`
      -Dsubrandr=disabled `# subrandr support`
      -Duchardet=disabled `# uchardet support`
      -Duwp=disabled `# Universal Windows Platform`
      -Dvapoursynth=disabled `# VapourSynth filter bridge`
      -Dvector=disabled `# GCC vector instructions`
      -Dx11-clipboard=disabled `# X11 clipboard backend`
      -Dzimg=disabled `# libzimg support (high quality software scaler)`
      -Dzlib=disabled `# zlib`

      `# audio output features`
      -Daaudio=disabled `# Android AAudio audio output`
      -Dalsa=disabled `# ALSA audio output`
      -Daudiotrack=disabled `# Android AudioTrack audio output`
      -Daudiounit=disabled `# AudioUnit output for iOS`
      -Davfoundation=disabled `# AVFoundation audio output`
      -Dcoreaudio=disabled `# CoreAudio audio output`
      -Djack=disabled `# JACK audio output`
      -Dopenal=disabled `# OpenAL audio output`
      -Dopensles=disabled `# OpenSL ES audio output`
      -Doss-audio=disabled `# OSSv4 audio output`
      -Dpipewire=disabled `# PipeWire audio output`
      -Dpulse=disabled `# PulseAudio audio output`
      -Dsdl2-audio=disabled `# SDL2 audio output`
      -Dsndio=disabled `# sndio audio output`
      -Dwasapi=disabled `# WASAPI audio output`

      `# video output features`
      -Damf=disabled `# AMD AMF`
      -Dcaca=disabled `# CACA`
      -Dcocoa=disabled `# Cocoa`
      -Dd3d11=disabled `# Direct3D 11 video output`
      -Ddirect3d=disabled `# Direct3D support`
      -Ddmabuf-wayland=disabled `# dmabuf-wayland video output`
      -Ddrm=disabled `# DRM`
      -Degl=disabled `# EGL 1.4`
      -Degl-android=disabled `# Android EGL support`
      -Degl-angle=disabled `# OpenGL ANGLE headers`
      -Degl-angle-lib=disabled `# OpenGL Win32 ANGLE library`
      -Degl-angle-win32=disabled `# OpenGL Win32 ANGLE Backend`
      -Degl-drm=disabled `# OpenGL DRM EGL Backend`
      -Degl-wayland=disabled `# OpenGL Wayland Backend`
      -Degl-x11=disabled `# OpenGL X11 EGL Backend`
      -Dgbm=disabled `# GBM`
      -Dgl=disabled `# OpenGL context support`
      -Dgl-cocoa=disabled `# gl-cocoa`
      -Dgl-dxinterop=disabled `# OpenGL/DirectX Interop Backend`
      -Dgl-win32=disabled `# OpenGL Win32 Backend`
      -Dgl-x11=disabled `# OpenGL X11/GLX (deprecated/legacy)`
      -Djpeg=disabled `# JPEG support`
      -Dsdl2-video=disabled `# SDL2 video output`
      -Dshaderc=disabled `# libshaderc SPIR-V compiler`
      -Dsixel=disabled `# Sixel`
      -Dspirv-cross=disabled `# SPIRV-Cross SPIR-V shader converter`
      -Dplain-gl=disabled `# OpenGL without platform-specific code (e.g. for libmpv)`
      -Dvdpau=disabled `# VDPAU acceleration`
      -Dvdpau-gl-x11=disabled `# VDPAU with OpenGl/X11`
      -Dvaapi=disabled `# VAAPI acceleration`
      -Dvaapi-drm=disabled `# VAAPI (DRM/EGL support)`
      -Dvaapi-wayland=disabled `# VAAPI (Wayland support)`
      -Dvaapi-win32=disabled `# VAAPI (Windows support)`
      -Dvaapi-x11=disabled `# VAAPI (X11 support)`
      -Dvulkan=disabled `# Vulkan context support`
      -Dwayland=disabled `# Wayland`
      -Dx11=disabled `# X11`
      -Dxv=disabled `# Xv video output`

      `# hwaccel features`
      -Dandroid-media-ndk=disabled `# Android Media APIs`
      -Dcuda-hwaccel=disabled `# CUDA acceleration`
      -Dcuda-interop=disabled `# CUDA with graphics interop`
      -Dd3d-hwaccel=disabled `# D3D11VA hwaccel`
      -Dd3d9-hwaccel=disabled `# DXVA2 hwaccel`
      -Dgl-dxinterop-d3d9=disabled `# OpenGL/DirectX Interop Backend DXVA2 interop`
      -Dios-gl=disabled `# iOS OpenGL ES hardware decoding interop support`
      -Dvideotoolbox-gl=disabled `# Videotoolbox with OpenGL`
      -Dvideotoolbox-pl=disabled `# Videotoolbox with libplacebo`

      `# macOS features`
      -Dmacos-10-15-4-features=disabled `# macOS 10.15.4 SDK Features`
      -Dmacos-11-features=disabled `# macOS 11 SDK Features`
      -Dmacos-11-3-features=disabled `# macOS 11.3 SDK Features`
      -Dmacos-12-features=disabled `# macOS 12 SDK Features`
      -Dmacos-cocoa-cb=disabled `# macOS libmpv backend`
      -Dmacos-media-player=disabled `# macOS Media Player support`
      -Dmacos-touchbar=disabled `# macOS Touch Bar support`
      -Dswift-build=disabled `# macOS Swift build tools`
      -Dswift-flags= `# Optional Swift compiler flags`

      `# manpages`
      -Dhtml-build=disabled `# html manual generation`
      -Dmanpage-build=disabled `# manpage generation`
      -Dpdf-build=disabled `# pdf manual generation`
    )

    COMMON_OPTIONS=(
      `# booleans`
      -Dlibmpv=true `# libmpv library`
      -Dbuild-date=true `# whether to include binary compile time`

      `# misc features`
      -Diconv=enabled `# iconv`
    )

    COMMON_VIDEO_OPTIONS=(
      `# misc features`
      -Duchardet=enabled `# uchardet support`
      -Dzlib=enabled `# zlib`

      `# video output features`
      -Dgl=enabled `# OpenGL context support`
      -Dplain-gl=enabled `# OpenGL without platform-specific code (e.g. for libmpv)`
    )

    MACOS_OPTIONS=(
      `# audio output features`
      -Dcoreaudio=enabled `# CoreAudio audio output`

      `# video output features`
      -Dcocoa=enabled `# Cocoa` `# BUG: required in audio mode since v0.36.0`
    )

    MACOS_VIDEO_OPTIONS=(
      `# video output features`
      -Dgl-cocoa=enabled `# gl-cocoa`
      -Dvulkan=enabled `# Vulkan context support (macvk via MoltenVK)`

      `# hwaccel features`
      -Dvideotoolbox-gl=enabled `# Videotoolbox with OpenGL`
      -Dvideotoolbox-pl=enabled `# Videotoolbox with libplacebo`

      `# macOS native Vulkan/Metal output`
      -Dmacos-10-15-4-features=enabled
      -Dmacos-11-features=enabled
      -Dmacos-11-3-features=enabled
      -Dmacos-12-features=enabled
      -Dswift-build=enabled
      "-Dswift-flags=-target ${swiftTargetArch}-apple-macos11.0"
    )

    IOS_OPTIONS=(
      `# audio output features`
      -Daudiounit=enabled `# AudioUnit output for iOS`
    )

    IOS_VIDEO_OPTIONS=(
      `# hwaccel features`
      -Dios-gl=enabled `# iOS OpenGL ES hardware decoding interop support`
    )

    OPTIONS=("''${DISABLE_ALL_OPTIONS[@]}")

    OPTIONS+=("''${COMMON_OPTIONS[@]}")
    if [ "${variant}" == "${variants.video}" ]; then
      OPTIONS+=("''${COMMON_VIDEO_OPTIONS[@]}")
    fi

    if [ "${os}" == "${oses.macos}" ]; then
      OPTIONS+=("''${MACOS_OPTIONS[@]}")
      if [ "${variant}" == "${variants.video}" ]; then
        OPTIONS+=("''${MACOS_VIDEO_OPTIONS[@]}")
      fi
    elif [ "${os}" == "${oses.ios}" ]; then
      OPTIONS+=("''${IOS_OPTIONS[@]}")
      if [ "${variant}" == "${variants.video}" ]; then
        OPTIONS+=("''${IOS_VIDEO_OPTIONS[@]}")
      fi
    fi

    meson setup build $src \
      --native-file ${nativeFile} \
      --cross-file ${mpvCrossFile} \
      --prefix=$out \
      "''${OPTIONS[@]}" |
      tee configure.log
  '';
  buildPhase = ''
    meson compile -vC build
  '';
  installPhase = ''
    meson install -C build

    # copy configure.log
    mkdir -p $out/share/mpv
    cp configure.log $out/share/mpv/

    ${pkgs.lib.optionalString macosNativeVideo ''
      enabled_features=$(sed -n 's/^Message: List of enabled features: //p' configure.log | tail -n 1)
      if [ -z "$enabled_features" ]; then
        echo "Unable to find mpv's enabled feature list in configure.log" >&2
        exit 1
      fi

      for feature in cocoa gl-cocoa vulkan videotoolbox-pl swift; do
        if ! printf '%s\n' "$enabled_features" | grep -Eq "(^|[[:space:]])$feature([[:space:]]|$)"; then
          echo "Required macOS native video feature was not enabled: $feature" >&2
          exit 1
        fi
      done

      if ! grep -Fq 'MPV_RENDER_PARAM_BACKEND = 21' $out/include/mpv/render.h; then
        echo "The custom gpu-next libmpv render backend ABI is missing" >&2
        exit 1
      fi

      mpv_dylib=$(find $out/lib -type f -name 'libmpv*.dylib' | head -n 1)
      if [ -z "$mpv_dylib" ] || ! grep -aFq 'mac/Vulkan (via Metal)' "$mpv_dylib"; then
        echo "The built libmpv binary does not contain the macvk context" >&2
        exit 1
      fi
      if ! grep -aFq 'Embedding macvk output in external NSView' "$mpv_dylib"; then
        echo "The built libmpv binary does not support an embedded macvk NSView" >&2
        exit 1
      fi

      expected_arch=${if arch == "amd64" then "x86_64" else "arm64"}
      if ! lipo -verify_arch "$expected_arch" "$mpv_dylib"; then
        echo "The built libmpv binary does not contain architecture: $expected_arch" >&2
        exit 1
      fi

      libplacebo_dylib=$(otool -L "$mpv_dylib" | awk '/libplacebo/ { print $1; exit }')
      if [ -z "$libplacebo_dylib" ]; then
        echo "The built libmpv binary is not linked to libplacebo" >&2
        exit 1
      fi
      if ! otool -L "$libplacebo_dylib" | grep -Fq 'libvulkan'; then
        echo "The linked libplacebo binary is not linked to the Vulkan loader" >&2
        exit 1
      fi
    ''}
  '';
}
