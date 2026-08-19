{
  pkgs ? import ../../utils/default/pkgs.nix,
}:

let
  packageLock = (import ../../../packages.lock.nix).vulkanHeaders;
  src = pkgs.callPackage ../../utils/fetch-tarball/default.nix {
    name = "vulkan-headers-source-${packageLock.version}";
    inherit (packageLock) url sha256;
  };
in

pkgs.runCommand "vulkan-headers-${packageLock.version}" { } ''
  mkdir -p $out/share/vulkan
  cp -R ${src}/include $out/include
  cp -R ${src}/registry $out/share/vulkan/registry
''
