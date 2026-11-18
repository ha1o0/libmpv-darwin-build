{
  pkgs ? import ../../utils/default/pkgs.nix,
  os ? import ../../utils/default/os.nix,
  arch ? pkgs.callPackage ../../utils/default/arch.nix { },
  variant ? import ../../utils/default/variant.nix,
}:
let
  ios = (os == "ios" || os == "iossimulator") && variant == "video";
  glslang = callPackage ../mk-pkg-glslang/default.nix { };
  moltenvk = callPackage ../mk-pkg-moltenvk/default.nix { };
  lock = import ../../../packages.lock.nix;
  callPackage = pkgs.lib.callPackageWith { inherit pkgs os arch; };
  xctoolchainLipo = callPackage ../../utils/xctoolchain/lipo.nix { };
  nativeFile = callPackage ../../utils/native-file/default.nix { };
  crossFile = callPackage ../../utils/cross-file/default.nix { };
  source = callPackage ../../utils/fetch-tarball/default.nix {
    name = "libplacebo-source-${lock.libplacebo.version}";
    inherit (lock.libplacebo) url sha256;
  };
  fastFloat = callPackage ../../utils/fetch-tarball/default.nix {
    name = "fast-float-source-${lock.fast_float.version}";
    inherit (lock.fast_float) url sha256;
  };
  python = pkgs.python3.withPackages (p: [ p.glad2 p.jinja2 ]);
  vulkanHeaders = callPackage ../../utils/fetch-tarball/default.nix {
    name = "vulkan-headers-source-${lock.vulkan_headers.version}";
    inherit (lock.vulkan_headers) url sha256;
  };
in
pkgs.stdenvNoCC.mkDerivation {
  pname = "mk-libplacebo-${os}-${arch}";
  inherit (lock.libplacebo) version;
  src = source;
  nativeBuildInputs = [ pkgs.meson pkgs.ninja pkgs.pkg-config python ]
    ++ pkgs.lib.optionals ios [ xctoolchainLipo ];
  buildInputs = pkgs.lib.optionals ios [ moltenvk glslang ];
  dontUseMesonConfigure = true;
  postUnpack = ''
    chmod -R u+w "$sourceRoot"
    cp -R ${fastFloat}/include "$sourceRoot/3rdparty/fast_float/"
    cp -R ${vulkanHeaders}/include "$sourceRoot/3rdparty/Vulkan-Headers/"
    ${pkgs.lib.optionalString ios ''
      cp -R ${vulkanHeaders}/registry "$sourceRoot/3rdparty/Vulkan-Headers/"
    ''}
    substituteInPlace "$sourceRoot/meson.build" \
      --replace-fail "import('python').find_installation()" "import('python').find_installation('${python}/bin/python3')"
    ${pkgs.lib.optionalString ios ''
      # Meson cross-file options take precedence over environment CFLAGS.
      # Resolve the target headers and archives explicitly, not a host glslang.
      substituteInPlace "$sourceRoot/src/glsl/meson.build" \
        --replace-fail "'glslang-default-resource-limits', required: false" "'glslang-default-resource-limits', required: true, static: true, dirs: ['${glslang}/lib']" \
        --replace-fail "'glslang', required: required, static: static)" "'glslang', required: true, static: static, dirs: vulkan_lib_dirs)" \
        --replace-fail "cc.has_header('glslang/build_info.h')" "cc.has_header('glslang/build_info.h', args: ['-I${glslang}/include'])" \
        --replace-fail "declare_dependency(dependencies: glslang_deps)" "declare_dependency(dependencies: glslang_deps, include_directories: include_directories('${glslang}/include'))"
    ''}
  '';
  configurePhase = ''
    meson setup build . --native-file ${nativeFile} --cross-file ${crossFile} \
      --prefix=$out --libdir=lib --default-library=static \
      -Dbuildtype=release -Db_staticpic=true -Dwrap_mode=nodownload \
      -Dauto_features=disabled -Dopengl=enabled -Dgl-proc-addr=enabled \
      ${pkgs.lib.optionalString ios "-Dvulkan=enabled -Dvk-proc-addr=enabled -Dglslang=enabled -Dprefer_static=true -Dvulkan-sdk=${glslang}"} \
      -Ddovi=enabled -Ddemos=false -Dtests=false -Dbench=false -Dfuzz=false
  '';
  buildPhase = "meson compile -C build";
  installPhase = ''
    meson install -C build
    # mpv links this C API from a C target; the static archive also contains C++.
    sed -i '/^Libs:/s/$/ -lc++/' $out/lib/pkgconfig/libplacebo.pc
    ${pkgs.lib.optionalString ios ''
      grep -qx 'pl_has_glslang=1' $out/lib/pkgconfig/libplacebo.pc
    ''}
  '';
}
