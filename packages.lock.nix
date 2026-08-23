{
  dav1d = {
    version = "1.2.1";
    url = "https://code.videolan.org/videolan/dav1d/-/archive/1.2.1/dav1d-1.2.1.tar.bz2";
    sha256 = "a4003623cdc0109dec3aac8435520aa3fb12c4d69454fa227f2658cdb6dab5fa";
  };
  ffmpeg = {
    version = "6.1.5";
    url = "https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n6.1.5.tar.gz";
    sha256 = "4bdedf0bfc705c99f515391054cd4df8723494c66016c33e0009419da5231e63";
  };
  fftools-ffi = {
    version = "9b0d4da0";
    url = "https://github.com/moffatman/fftools-ffi/archive/9b0d4da026d9c830702ec043c1f1f98d407025af.tar.gz";
    sha256 = "mgf3ddt3yjmYBd2D0WeEnhgxKNjrEbjYnDx2t4YCfU8=";
  };
  freetype = {
    version = "2.13.2";
    url = "https://downloads.sourceforge.net/project/freetype/freetype2/2.13.2/freetype-2.13.2.tar.xz";
    sha256 = "12991c4e55c506dd7f9b765933e62fd2be2e06d421505d7950a132e4f1bb484d";
  };
  fribidi = {
    version = "1.0.13";
    url = "https://github.com/fribidi/fribidi/releases/download/v1.0.13/fribidi-1.0.13.tar.xz";
    sha256 = "7fa16c80c81bd622f7b198d31356da139cc318a63fc7761217af4130903f54a2";
  };
  harfbuzz = {
    version = "8.1.1";
    url = "https://github.com/harfbuzz/harfbuzz/archive/8.1.1.tar.gz";
    sha256 = "b16e6bc0fc7e6a218583f40c7d201771f2e3072f85ef6e9217b36c1dc6b2aa25";
  };
  libass = {
    version = "0.17.1";
    url = "https://github.com/libass/libass/releases/download/0.17.1/libass-0.17.1.tar.xz";
    sha256 = "f0da0bbfba476c16ae3e1cfd862256d30915911f7abaa1b16ce62ee653192784";
  };
  libplacebo = {
    version = "7.360.1";
    url = "https://code.videolan.org/videolan/libplacebo/-/archive/v7.360.1/libplacebo-v7.360.1.tar.gz";
    sha256 = "14c0a99f4b01557ec9826ce6b1d52f6de21be274ba03fd5aab7307f18766dc39";
  };
  libogg = {
    version = "1.3.5";
    url = "https://github.com/xiph/ogg/releases/download/v1.3.5/libogg-1.3.5.tar.gz";
    sha256 = "0eb4b4b9420a0f51db142ba3f9c64b333f826532dc0f48c6410ae51f4799b664";
  };
  libpng = {
    version = "1.6.40";
    url = "https://github.com/pnggroup/libpng/archive/v1.6.40.tar.gz";
    sha256 = "62d25af25e636454b005c93cae51ddcd5383c40fa14aa3dae8f6576feb5692c2";
  };
  libpngPatch = {
    version = "1.6.40-1";
    url = "https://wrapdb.mesonbuild.com/v2/libpng_1.6.40-1/get_patch";
    sha256 = "bad558070e0a82faa5c0ae553bcd12d49021fc4b628f232a8e58c3fbd281aae1";
  };
  libvorbis = {
    version = "1.3.7";
    url = "https://github.com/xiph/vorbis/releases/download/v1.3.7/libvorbis-1.3.7.tar.gz";
    sha256 = "0e982409a9c3fc82ee06e08205b1355e5c6aa4c36bca58146ef399621b0ce5ab";
  };
  libvpx = {
    version = "1.13.0+1";
    url = "https://gitlab.freedesktop.org/gstreamer/meson-ports/libvpx/-/archive/90d26fac0d895969a82cd873ad36e39737104c44/libvpx-v1.13.0.tar.gz";
    sha256 = "4f872ad2709d17b848b3588231495e432c42b9263731b9121fa210a3c5a893ff";
  };
  libx264 = {
    version = "a8b68ebf";
    url = "https://code.videolan.org/videolan/x264/-/archive/a8b68ebfaa68621b5ac8907610d3335971839d52/libx264-a8b68ebfaa68621b5ac8907610d3335971839d52.tar.gz";
    sha256 = "164688b63f11a6e4f6d945057fc5c57d5eefb97973d0029fb0303744e10839ff";
  };
  libxml2 = {
    version = "2.11.5";
    url = "https://download.gnome.org/sources/libxml2/2.11/libxml2-2.11.5.tar.xz";
    sha256 = "3727b078c360ec69fa869de14bd6f75d7ee8d36987b071e6928d4720a28df3a6";
  };
  mbedtls = {
    version = "3.4.1";
    url = "https://github.com/Mbed-TLS/mbedtls/archive/refs/tags/v3.4.1.tar.gz";
    sha256 = "a420fcf7103e54e775c383e3751729b8fb2dcd087f6165befd13f28315f754f5";
  };
  mpv = {
    version = "0.36.0";
    url = "https://github.com/ha1o0/mpv/archive/refs/heads/my-gpu-next.tar.gz";
    sha256 = "8ff7f966c928a017936588691c5f7e3ec9f44f13d9e0ad6e4b8c9f8a647e7d19";
  };
  mpvMacosNativeVideo = {
    version = "0.41.0-git-8c8ec836";
    url = "https://github.com/ha1o0/mpv/archive/8c8ec8365844886f847bec2b1d983c5dcac4b3fe.tar.gz";
    sha256 = "86c45c14818d96f3adf23c265b2525a24614b65b94fb303ce85548d7d61e03fe";
  };
  vulkanHeaders = {
    version = "1.4.357.0";
    url = "https://github.com/KhronosGroup/Vulkan-Headers/archive/refs/tags/vulkan-sdk-1.4.357.0.tar.gz";
    sha256 = "e87dce08116151f6b6d7de6b6faf41498e87e6cf848ff16fa3bd5402190ad4a3";
  };
  uchardet = {
    version = "0.0.8";
    url = "https://www.freedesktop.org/software/uchardet/releases/uchardet-0.0.8.tar.xz";
    sha256 = "e97a60cfc00a1c147a674b097bb1422abd9fa78a2d9ce3f3fdcc2e78a34ac5f0";
  };
}
