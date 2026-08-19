{
  pkgs ? import ../../utils/default/pkgs.nix,
  os ? import ../../utils/default/os.nix,
  arch ? pkgs.callPackage ../../utils/default/arch.nix { },
  variant ? import ../../utils/default/variant.nix,
}:

let
  packageLock = (import ../../../packages.lock.nix).libplacebo;
  # Build amd64 dependencies with the native x86_64 package set under Rosetta.
  targetPkgs =
    if arch == "amd64" then
      import pkgs.path {
        system = "x86_64-darwin";
        config.allowUnfree = true;
      }
    else
      pkgs;
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
