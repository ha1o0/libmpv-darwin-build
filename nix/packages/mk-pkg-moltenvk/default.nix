{ pkgs, os, arch }:
let
  locks = import ../../../packages.lock.nix;
  lock = locks.moltenvk;
  fetch = pkgs.callPackage ../../utils/fetch-tarball/default.nix;
  src = fetch { name = "moltenvk-${lock.version}-apple"; inherit (lock) url sha256; };
  headers = fetch { name = "vulkan-headers-source-${locks.vulkan_headers.version}"; inherit (locks.vulkan_headers) url sha256; };
  slice = if os == "ios" then "ios-arm64" else "ios-arm64_x86_64-simulator";
in
assert os == "ios" || os == "iossimulator";
pkgs.runCommand "ios-vulkan-${os}-${arch}-${lock.version}" { } ''
  mkdir -p $out/lib/pkgconfig
  cp ${src}/MoltenVK/static/MoltenVK.xcframework/${slice}/libMoltenVK.a $out/lib/
  ln -s ${headers}/include $out/include
  cat > $out/lib/pkgconfig/vulkan.pc <<PC
Name: Vulkan
Description: Vulkan for iOS via MoltenVK
Version: ${locks.vulkan_headers.version}
Cflags: -I$out/include
Libs: -L$out/lib -lMoltenVK -lc++ -framework Metal -framework QuartzCore -framework IOSurface -framework CoreGraphics -framework UIKit -framework Foundation
PC
''
