final: prev: {
  darwin = prev.darwin.overrideScope (
    final: prev: {
      xcode_16_2 = prev.xcode.overrideAttrs (prev: {
        outputHash = "sha256-XWTzMAonhhbQYkjg2XRQ5hcMMKZTkKN9f8dvDMraz9A=";
      });
      xcode = final.xcode_16_2;
    }
  );
}
