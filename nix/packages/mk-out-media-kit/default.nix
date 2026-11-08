{
  pkgs ? import ../../utils/default/pkgs.nix,
}:

let
  version = import ../../utils/version/default.nix { inherit pkgs; };
  # The consumer's Makefiles and artifact updater use these four full-flavor
  # XCFramework bundles. Keep their per-framework ZIPs for SwiftPM as well.
  archives = builtins.concatMap (
    os:
    builtins.map (
      variant:
      pkgs.callPackage ../mk-out-archive/default.nix {
        inherit os variant;
        format = "xcframeworks";
        arch = "universal";
        flavor = "full";
      }
    ) [ "audio" "video" ]
  ) [ "ios" "macos" ];
in

pkgs.runCommand "mk-out-media-kit-${version}" { } ''
  mkdir -p $out
  for archive in ${pkgs.lib.concatStringsSep " " archives}; do
    cp --no-preserve=mode "$archive"/*.tar.gz "$archive"/*.zip $out/
  done
''
