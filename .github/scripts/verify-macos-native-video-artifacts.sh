#!/usr/bin/env bash

set -euo pipefail

ARTIFACT_DIR=${1:-result}
WORK_DIR=$(mktemp -d)
trap 'rm -rf "${WORK_DIR}"' EXIT

shopt -s nullglob
archives=("${ARTIFACT_DIR}"/libmpv-xcframeworks_*_macos-universal-video-*.tar.gz)
if [ "${#archives[@]}" -eq 0 ]; then
    echo "No macOS universal video XCFramework archive was produced" >&2
    exit 1
fi

for archive in "${archives[@]}"; do
    archive_name=$(basename "${archive}" .tar.gz)
    extract_dir="${WORK_DIR}/${archive_name}"
    mkdir -p "${extract_dir}"
    tar -xzf "${archive}" -C "${extract_dir}"

    package_dir=$(find "${extract_dir}" -mindepth 1 -maxdepth 1 -type d | head -n 1)
    if [ -z "${package_dir}" ]; then
        echo "Archive is empty: ${archive}" >&2
        exit 1
    fi

    for framework in Mpv Placebo Vulkan MoltenVK Glslang; do
        if [ ! -d "${package_dir}/${framework}.xcframework" ]; then
            echo "Missing ${framework}.xcframework in ${archive}" >&2
            exit 1
        fi
    done

    mpv_binary=$(find "${package_dir}/Mpv.xcframework" -type f -path '*/Mpv.framework/Versions/A/Mpv' | head -n 1)
    placebo_binary=$(find "${package_dir}/Placebo.xcframework" -type f -path '*/Placebo.framework/Versions/A/Placebo' | head -n 1)
    if [ -z "${mpv_binary}" ] || [ -z "${placebo_binary}" ]; then
        echo "Unable to locate Mpv or Placebo framework binary in ${archive}" >&2
        exit 1
    fi

    lipo "${mpv_binary}" -verify_arch arm64 x86_64
    grep -aFq 'mac/Vulkan (via Metal)' "${mpv_binary}"
    grep -aFq 'Embedding macvk output in external NSView' "${mpv_binary}"
    if grep -aFq 'Initializing embedded macvk AppKit state asynchronously' "${mpv_binary}"; then
        echo "Mpv.framework contains the broken asynchronous macvk AppKit initialization" >&2
        exit 1
    fi

    render_header=$(find "${package_dir}/Mpv.xcframework" -type f -path '*/Mpv.framework/Versions/A/Headers/render.h' | head -n 1)
    if [ -z "${render_header}" ] || ! grep -Fq 'MPV_RENDER_PARAM_BACKEND = 21' "${render_header}"; then
        echo "Custom gpu-next Render API ABI is missing from ${archive}" >&2
        exit 1
    fi

    if ! otool -L "${mpv_binary}" | grep -Fq '@rpath/Placebo.framework/Versions/A/Placebo'; then
        echo "Mpv.framework is not linked to Placebo.framework in ${archive}" >&2
        exit 1
    fi
    if ! otool -L "${placebo_binary}" | grep -Fq '@rpath/Vulkan.framework/Versions/A/Vulkan'; then
        echo "Placebo.framework is not linked to Vulkan.framework in ${archive}" >&2
        exit 1
    fi

    while IFS= read -r binary; do
        lipo "${binary}" -verify_arch arm64 x86_64
        if otool -L "${binary}" | grep -Fq '/nix/store/'; then
            echo "Framework dependency still refers to /nix/store: ${binary}" >&2
            exit 1
        fi

        while IFS= read -r dependency; do
            dependency_framework=$(printf '%s\n' "${dependency}" | sed -n -E 's|^@rpath/([^/]+)\.framework/.*|\1|p')
            if [ -n "${dependency_framework}" ] && [ ! -d "${package_dir}/${dependency_framework}.xcframework" ]; then
                echo "Missing XCFramework for ${dependency} required by ${binary}" >&2
                exit 1
            fi
        done < <(otool -L "${binary}" | awk 'NR > 1 { print $1 }')
    done < <(find "${package_dir}" -type f -path '*.framework/Versions/A/*' ! -path '*/Resources/*' ! -path '*/Headers/*' ! -path '*/Modules/*')

    icd_manifest=$(find "${package_dir}/MoltenVK.xcframework" -type f -path '*/MoltenVK.framework/Versions/A/Resources/vulkan/icd.d/MoltenVK_icd.json' | head -n 1)
    if [ -z "${icd_manifest}" ] || grep -Fq '/nix/store/' "${icd_manifest}"; then
        echo "MoltenVK ICD manifest is missing or not relocatable in ${archive}" >&2
        exit 1
    fi
    icd_library=$(sed -n -E 's|.*"library_path"[[:space:]]*:[[:space:]]*"([^"]+)".*|\1|p' "${icd_manifest}")
    if [ -z "${icd_library}" ] || [ ! -f "$(dirname "${icd_manifest}")/${icd_library}" ]; then
        echo "MoltenVK ICD library_path does not resolve inside ${archive}" >&2
        exit 1
    fi
    icd_api_version=$(sed -n -E 's|.*"api_version"[[:space:]]*:[[:space:]]*"([^"]+)".*|\1|p' "${icd_manifest}")
    if [ "${icd_api_version}" != "1.3.0" ]; then
        echo "Unexpected MoltenVK ICD API version in ${archive}: ${icd_api_version}" >&2
        exit 1
    fi

    moltenvk_binary=$(find "${package_dir}/MoltenVK.xcframework" -type f -path '*/MoltenVK.framework/Versions/A/MoltenVK' | head -n 1)
    if [ -z "${moltenvk_binary}" ]; then
        echo "Unable to locate MoltenVK framework binary in ${archive}" >&2
        exit 1
    fi
    lipo "${moltenvk_binary}" -verify_arch arm64 x86_64
    if otool -L "${moltenvk_binary}" | grep -Eq '@rpath/(Glslang|SPIRV)\.framework'; then
        echo "MoltenVK.framework still uses the pre-1.3 shader runtime dependencies" >&2
        exit 1
    fi

    for architecture in arm64 x86_64; do
        min_os=$(xcrun vtool -arch "${architecture}" -show-build "${mpv_binary}" | awk '/minos/ { print $2; exit }')
        if [ "${min_os}" != "11.0" ]; then
            echo "Unexpected Mpv.framework minimum macOS version for ${architecture}: ${min_os}" >&2
            exit 1
        fi
        moltenvk_min_os=$(xcrun vtool -arch "${architecture}" -show-build "${moltenvk_binary}" | awk '/minos/ { print $2; exit }')
        if [ "${moltenvk_min_os}" != "10.15" ]; then
            echo "Unexpected MoltenVK.framework minimum macOS version for ${architecture}: ${moltenvk_min_os}" >&2
            exit 1
        fi
    done
done
