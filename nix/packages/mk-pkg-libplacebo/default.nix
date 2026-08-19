{
  pkgs ? import ../../utils/default/pkgs.nix,
  os ? import ../../utils/default/os.nix,
  arch ? pkgs.callPackage ../../utils/default/arch.nix { },
  variant ? import ../../utils/default/variant.nix,
}:

let
  packageLock = (import ../../../packages.lock.nix).libplacebo;
  targetSystem = if arch == "amd64" then "x86_64-darwin" else "aarch64-darwin";
  targetPkgs =
    if pkgs.stdenv.hostPlatform.system == targetSystem then
      pkgs
    else if arch == "amd64" then
      pkgs.pkgsCross.x86_64-darwin
    else
      pkgs.pkgsCross.aarch64-darwin;
  src = pkgs.callPackage ../../utils/fetch-tarball/default.nix {
    name = "libplacebo-source-${packageLock.version}";
    inherit (packageLock) url sha256;
  };
  vulkanHeaders = pkgs.callPackage ../mk-pkg-vulkan-headers/default.nix { };
in

if os == "macos" && variant == "video" then
  targetPkgs.libplacebo.overrideAttrs (old: {
    inherit src;
    inherit (packageLock) version;
    postPatch = (old.postPatch or "") + ''
      mkdir -p 3rdparty/Vulkan-Headers
      cp -R ${vulkanHeaders}/include 3rdparty/Vulkan-Headers/
      cp -R ${vulkanHeaders}/share/vulkan/registry 3rdparty/Vulkan-Headers/
    '';
  })
else
  targetPkgs.libplacebo
