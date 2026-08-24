{
  pkgs ? import ../../utils/default/pkgs.nix,
  os ? import ../../utils/default/os.nix,
  arch ? pkgs.callPackage ../../utils/default/arch.nix { },
}:

let
  archs = import ../../utils/constants/archs.nix;
  oses = import ../../utils/constants/oses.nix;
  # The pinned nixpkgs supplies MoltenVK 1.2.11, whose HDR swapchain teardown
  # can double-release CAEDRMetadata. Use the official 1.3.0 release containing
  # the upstream memory-management fix without upgrading the full package set.
  packageLock = (import ../../../packages.lock.nix).moltenvk;
  src = pkgs.callPackage ../../utils/fetch-tarball/default.nix {
    name = "moltenvk-source-${packageLock.version}";
    inherit (packageLock) url sha256;
  };
  xctoolchainLipo = pkgs.callPackage ../../utils/xctoolchain/lipo.nix { };
  targetArch =
    if arch == archs.arm64 then
      "arm64"
    else if arch == archs.amd64 then
      "x86_64"
    else
      abort "MoltenVK must be prepared per architecture before creating a universal output";
in

assert os == oses.macos;
pkgs.runCommand "moltenvk-${packageLock.version}-${arch}" {
  nativeBuildInputs = [ xctoolchainLipo ];
} ''
  mkdir -p $out/lib $out/share/vulkan/icd.d

  lipo \
    ${src}/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib \
    -thin ${targetArch} \
    -output $out/lib/libMoltenVK.dylib

  cp \
    ${src}/MoltenVK/dynamic/dylib/macOS/MoltenVK_icd.json \
    $out/share/vulkan/icd.d/MoltenVK_icd.json
''
