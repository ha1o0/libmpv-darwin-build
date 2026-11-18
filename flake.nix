{
  description = "libmpv-darwin-build";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs?ref=nixpkgs-unstable";
    flakelight = {
      url = "github:nix-community/flakelight";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { flakelight, ... }:
    flakelight ./. {
      flakelight.builtinFormatters = false;
      withOverlays = import ./nix/utils/default/overlays.nix;
      nixpkgs.config = {
        allowUnfree = true;
      };
      systems = [
        "aarch64-darwin"
        "x86_64-darwin"
      ];
      devShell = pkgs: {
        stdenv = pkgs.stdenvNoCC;
        packages = with pkgs; [
          tree
          gnumake
        ];
      };
      perSystem =
        { pkgs, ... }:
        {
          packages = let
            all = import ./nix/utils/flake/create-cross-packages.nix {
              inherit pkgs;
              path = ./nix/packages;
            };
          in pkgs.lib.filterAttrs (name: _: pkgs.lib.hasInfix "-macos-" name || name == "mk-out-media-kit") all
             // { default = all.mk-out-media-kit; };
        };
    };
}
