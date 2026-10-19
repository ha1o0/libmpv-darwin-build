{
  pkgs ? import ../../utils/default/pkgs.nix,
  os ? import ../../utils/default/os.nix,
  arch ? pkgs.callPackage ../../utils/default/arch.nix { },
}:

assert os == "macos" && builtins.elem arch [ "amd64" "arm64" ];

let
  locks = import ../../../packages.lock.nix;
  source = name: pkgs.callPackage ../../utils/fetch-tarball/default.nix {
    name = "mk-curl-${name}-source-${locks.${name}.version}";
    inherit (locks.${name}) url sha256;
  };
  xcode = pkgs.darwin.xcode;
in

# Keep the entire static transport in one prefix, including its private
# pkg-config dependencies. No new network dylibs/frameworks are distributed.
pkgs.stdenvNoCC.mkDerivation {
  pname = "mk-pkg-curl-${os}-${arch}";
  version = locks.curl.version;
  dontUnpack = true;
  dontConfigure = true;
  dontFixup = true;
  enableParallelBuilding = true;
  nativeBuildInputs = [ pkgs.cmake pkgs.ninja pkgs.pkg-config pkgs.perl pkgs.gnumake ];
  CURL_SOURCE = source "curl";
  NGHTTP2_SOURCE = source "nghttp2";
  OPENSSL_SOURCE = source "openssl";
  TARGET_ARCH = if arch == "amd64" then "x86_64" else "arm64";
  MACOS_SDK = "${xcode}/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk";
  XCODE_TOOLCHAIN = "${xcode}/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin";
  buildPhase = ''
    runHook preBuild
    export INSTALL_PREFIX="$out"
    bash ${./build-macos.sh}
    runHook postBuild
  '';
  installPhase = ''
    mkdir -p $out/share/licenses/curl-transport
    cp ${source "curl"}/COPYING $out/share/licenses/curl-transport/curl.txt
    cp ${source "nghttp2"}/COPYING $out/share/licenses/curl-transport/nghttp2.txt
    cp ${source "openssl"}/LICENSE.txt $out/share/licenses/curl-transport/openssl.txt
  '';
}
