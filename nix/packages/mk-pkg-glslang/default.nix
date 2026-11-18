{ pkgs, os, arch }:
let
  lock = (import ../../../packages.lock.nix).glslang;
  simulator = os == "iossimulator";
  cpu = if arch == "amd64" then "x86_64" else "arm64";
  platform = if simulator then "iPhoneSimulator" else "iPhoneOS";
  developer = "${pkgs.darwin.xcode}/Contents/Developer";
  tools = "${developer}/Toolchains/XcodeDefault.xctoolchain/usr/bin";
in
pkgs.stdenvNoCC.mkDerivation {
  pname = "ios-glslang-${os}-${arch}";
  inherit (lock) version;
  src = pkgs.callPackage ../../utils/fetch-tarball/default.nix {
    name = "glslang-source-${lock.version}";
    inherit (lock) url sha256;
  };
  nativeBuildInputs = [ pkgs.cmake pkgs.ninja ];
  cmakeFlags = [
    "-DCMAKE_SYSTEM_NAME=iOS" "-DCMAKE_OSX_DEPLOYMENT_TARGET=15.0"
    "-DCMAKE_OSX_SYSROOT=${developer}/Platforms/${platform}.platform/Developer/SDKs/${platform}.sdk"
    "-DCMAKE_OSX_ARCHITECTURES=${cpu}" "-DCMAKE_C_COMPILER=${tools}/clang"
    "-DCMAKE_CXX_COMPILER=${tools}/clang++" "-DCMAKE_AR=${tools}/ar" "-DCMAKE_RANLIB=${tools}/ranlib"
    "-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY" "-DBUILD_SHARED_LIBS=OFF"
    "-DENABLE_OPT=OFF" "-DENABLE_GLSLANG_BINARIES=OFF" "-DGLSLANG_TESTS=OFF"
    "-DENABLE_HLSL=OFF" "-DENABLE_PCH=OFF"
  ];
}
