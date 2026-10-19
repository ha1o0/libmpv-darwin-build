#!/usr/bin/env bash
set -euo pipefail

: "${CURL_SOURCE:?}" "${NGHTTP2_SOURCE:?}" "${OPENSSL_SOURCE:?}"
: "${INSTALL_PREFIX:?}" "${TARGET_ARCH:?}" "${MACOS_SDK:?}" "${XCODE_TOOLCHAIN:?}"
case "$TARGET_ARCH" in
    x86_64) openssl_target=darwin64-x86_64-cc ;;
    arm64) openssl_target=darwin64-arm64-cc ;;
    *) echo "Unsupported macOS architecture: $TARGET_ARCH" >&2; exit 1 ;;
esac
jobs=${NIX_BUILD_CORES:-4}
mkdir -p build/openssl "$INSTALL_PREFIX/lib/pkgconfig"

# curl's static .pc file requires zlib even when CMake uses the SDK directly.
# Describe the target system library without adding a bundled zlib dependency.
zlib_version=$(sed -n 's/^#define ZLIB_VERSION "\([^"]*\)".*/\1/p' "$MACOS_SDK/usr/include/zlib.h")
test -n "$zlib_version"
cat > "$INSTALL_PREFIX/lib/pkgconfig/zlib.pc" <<EOF
Name: zlib
Description: macOS SDK system zlib
Version: $zlib_version
Libs: -lz
EOF

# Do not discover Homebrew, system curl, or Nix's host networking libraries.
export PKG_CONFIG_PATH="$INSTALL_PREFIX/lib/pkgconfig"
export PKG_CONFIG_LIBDIR="$PKG_CONFIG_PATH"
common=(
    -G Ninja
    -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_SYSTEM_NAME=Darwin
    "-DCMAKE_C_COMPILER=$XCODE_TOOLCHAIN/clang"
    "-DCMAKE_CXX_COMPILER=$XCODE_TOOLCHAIN/clang++"
    "-DCMAKE_AR=$XCODE_TOOLCHAIN/ar"
    "-DCMAKE_RANLIB=$XCODE_TOOLCHAIN/ranlib"
    "-DCMAKE_OSX_ARCHITECTURES=$TARGET_ARCH"
    "-DCMAKE_OSX_SYSROOT=$MACOS_SDK"
    -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0
    "-DCMAKE_INSTALL_PREFIX=$INSTALL_PREFIX"
    -DCMAKE_INSTALL_LIBDIR=lib
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
)

(
    cd build/openssl
    CC="$XCODE_TOOLCHAIN/clang" AR="$XCODE_TOOLCHAIN/ar" \
    RANLIB="$XCODE_TOOLCHAIN/ranlib" \
    CFLAGS="-O3 -fPIC -arch $TARGET_ARCH -isysroot $MACOS_SDK -mmacosx-version-min=11.0" \
    LDFLAGS="-arch $TARGET_ARCH -isysroot $MACOS_SDK -mmacosx-version-min=11.0" \
    perl "$OPENSSL_SOURCE/Configure" "$openssl_target" \
        "--prefix=$INSTALL_PREFIX" "--openssldir=$INSTALL_PREFIX/etc/ssl" \
        --libdir=lib no-shared no-module no-tests no-apps no-docs \
        no-autoload-config
    make -j"$jobs" build_sw
    make install_sw
)

cmake -S "$NGHTTP2_SOURCE" -B build/nghttp2 "${common[@]}" \
    -DENABLE_LIB_ONLY=ON -DBUILD_STATIC_LIBS=ON -DBUILD_SHARED_LIBS=OFF \
    -DENABLE_DOC=OFF -DENABLE_FAILMALLOC=OFF -DBUILD_TESTING=OFF
cmake --build build/nghttp2 --parallel "$jobs"
cmake --install build/nghttp2

cmake -S "$CURL_SOURCE" -B build/curl "${common[@]}" \
    -DBUILD_SHARED_LIBS=OFF -DBUILD_STATIC_LIBS=ON -DBUILD_CURL_EXE=OFF \
    -DBUILD_TESTING=OFF -DBUILD_EXAMPLES=OFF -DBUILD_LIBCURL_DOCS=OFF \
    -DBUILD_MISC_DOCS=OFF -DENABLE_CURL_MANUAL=OFF \
    -DCURL_USE_PKGCONFIG=OFF -DCURL_USE_CMAKECONFIG=OFF \
    -DCURL_USE_OPENSSL=ON -DOPENSSL_USE_STATIC_LIBS=ON \
    "-DOPENSSL_INCLUDE_DIR=$INSTALL_PREFIX/include" \
    "-DOPENSSL_SSL_LIBRARY=$INSTALL_PREFIX/lib/libssl.a" \
    "-DOPENSSL_CRYPTO_LIBRARY=$INSTALL_PREFIX/lib/libcrypto.a" \
    -DUSE_APPLE_SECTRUST=ON -DCURL_CA_NATIVE=ON \
    -DCURL_CA_BUNDLE=none -DCURL_CA_PATH=none \
    -DUSE_NGHTTP2=ON -DNGHTTP2_USE_STATIC_LIBS=ON \
    -DCMAKE_REQUIRE_FIND_PACKAGE_NGHTTP2=ON \
    "-DNGHTTP2_INCLUDE_DIR=$INSTALL_PREFIX/include" \
    "-DNGHTTP2_LIBRARY=$INSTALL_PREFIX/lib/libnghttp2.a" \
    -DENABLE_ARES=OFF -DENABLE_THREADED_RESOLVER=ON \
    -DUSE_NGTCP2=OFF -DUSE_QUICHE=OFF -DUSE_ECH=OFF \
    -DCURL_USE_LIBPSL=OFF -DCURL_USE_LIBSSH=OFF -DCURL_USE_LIBSSH2=OFF \
    -DCURL_USE_GSSAPI=OFF -DCURL_DISABLE_LDAP=ON \
    -DUSE_LIBIDN2=OFF -DUSE_APPLE_IDN=ON \
    -DCURL_BROTLI=OFF -DCURL_ZSTD=OFF -DCURL_ZLIB=ON \
    "-DZLIB_INCLUDE_DIR=$MACOS_SDK/usr/include" \
    "-DZLIB_LIBRARY=$MACOS_SDK/usr/lib/libz.tbd"
cmake --build build/curl --parallel "$jobs"
cmake --install build/curl

features=$(sh "$INSTALL_PREFIX/bin/curl-config" --features)
for feature in HTTP2 SSL AppleSecTrust AsynchDNS; do
    if ! printf '%s\n' "$features" | grep -Fxq "$feature"; then
        echo "Required curl feature is missing: $feature" >&2
        exit 1
    fi
done
sh "$INSTALL_PREFIX/bin/curl-config" --ssl-backends | grep -Fq OpenSSL
for feature in USE_OPENSSL USE_NGHTTP2 USE_APPLE_SECTRUST; do
    grep -Eq "^#define $feature 1$" build/curl/lib/curl_config.h
done
for library in curl nghttp2 ssl crypto; do
    "$XCODE_TOOLCHAIN/lipo" "$INSTALL_PREFIX/lib/lib$library.a" -verify_arch "$TARGET_ARCH"
done
if find "$INSTALL_PREFIX/lib" -name '*.dylib' | grep -q .; then
    echo "The curl transport must contain only static libraries" >&2
    exit 1
fi
pkg-config --static --exists libcurl

# Exercise the same complete static dependency graph that mpv's Meson uses.
# Linking works for both target architectures without executing target code.
cat > build/curl-link-check.c <<'EOF'
#include <curl/curl.h>
const curl_version_info_data *curl_link_check(void)
{
    CURL *easy = curl_easy_init();
    CURLM *multi = curl_multi_init();
    curl_easy_cleanup(easy);
    curl_multi_cleanup(multi);
    return curl_version_info(CURLVERSION_NOW);
}
EOF
read -r -a curl_cflags <<< "$(pkg-config --static --cflags libcurl)"
read -r -a curl_libs <<< "$(pkg-config --static --libs libcurl)"
"$XCODE_TOOLCHAIN/clang" -arch "$TARGET_ARCH" -isysroot "$MACOS_SDK" \
    -mmacosx-version-min=11.0 -dynamiclib \
    -Wl,-install_name,@rpath/curl-link-check.dylib \
    "${curl_cflags[@]}" build/curl-link-check.c "${curl_libs[@]}" \
    -o build/curl-link-check.dylib
"$XCODE_TOOLCHAIN/otool" -L build/curl-link-check.dylib > build/curl-link-check.imports
if grep -E '/nix/store/|lib(curl|nghttp2|ssl|crypto)[^/]*\.dylib' build/curl-link-check.imports; then
    echo "The curl transport unexpectedly links to a non-system dynamic library" >&2
    exit 1
fi
mkdir -p "$INSTALL_PREFIX/share/curl"
printf '%s\n' "$features" > "$INSTALL_PREFIX/share/curl/features.txt"
pkg-config --static --libs libcurl > "$INSTALL_PREFIX/share/curl/static-libs.txt"
