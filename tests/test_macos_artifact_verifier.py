"""Run with Python >= 3.8; parser tests do not require Xcode or built artifacts."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/verify-macos-curl-artifacts.py"
SPEC = importlib.util.spec_from_file_location("macos_artifacts", SCRIPT)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def commands(dependency="/System/Library/Frameworks/Security.framework/Versions/A/Security",
             rpath="@loader_path/../../../", minimum="11.0", platform="1"):
    return """example (architecture arm64):
Load command 0
          cmd LC_ID_DYLIB
      cmdsize 72
         name @rpath/Mpv.framework/Versions/A/Mpv (offset 24)
Load command 1
          cmd LC_LOAD_DYLIB
      cmdsize 96
         name {} (offset 24)
Load command 2
          cmd LC_RPATH
      cmdsize 48
         path {} (offset 12)
Load command 3
          cmd LC_BUILD_VERSION
      cmdsize 32
     platform {}
         minos {}
           sdk 15.1
        ntools 1
""".format(dependency, rpath, platform, minimum)


def make_mpv_package(package):
    binary = package / "Mpv.xcframework/macos/Mpv.framework/Versions/A/Mpv"
    binary.parent.mkdir(parents=True)
    binary.touch()
    licenses = binary.parent / "Resources/Licenses/curl-transport"
    licenses.mkdir(parents=True)
    for name in verifier.NETWORK_LICENSES:
        (licenses / name).write_text("Licence text for " + name + "\n")
    return binary


class ArtifactVerifierTests(unittest.TestCase):
    def test_system_security_and_relocatable_rpath_are_allowed(self):
        verifier.check_load_commands(commands(), "fixture", is_mpv=True)

    def test_nix_rpath_is_rejected_even_with_clean_dylib_dependencies(self):
        with self.assertRaisesRegex(ValueError, "Nix store"):
            verifier.check_load_commands(commands(rpath="/nix/store/build/lib"), "fixture")

    def test_nix_dylib_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Nix store"):
            verifier.check_load_commands(commands(dependency="/nix/store/build/lib/libfoo.dylib"), "fixture")

    def test_static_network_dependencies_cannot_become_dylibs_or_frameworks(self):
        for dependency in (
                "/usr/lib/libcurl.4.dylib", "@rpath/libnghttp2.14.dylib",
                "@rpath/libssl.3.dylib", "@rpath/libcrypto.3.dylib",
                "@rpath/Curl.framework/Versions/A/Curl",
                "@rpath/Nghttp2.framework/Versions/A/Nghttp2",
                "@rpath/OpenSSL.framework/Versions/A/OpenSSL"):
            with self.subTest(dependency=dependency), self.assertRaisesRegex(ValueError, "statically"):
                verifier.check_load_commands(commands(dependency=dependency), "fixture")

    def test_existing_ffmpeg_mbedtls_dependency_is_allowed(self):
        verifier.check_load_commands(commands(dependency="@rpath/Mbedtls.framework/Versions/A/Mbedtls"), "fixture")

    def test_mpv_minimum_os_must_remain_11(self):
        for version in ("10.15", "12.0", "15.1"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "minimum macOS"):
                verifier.check_load_commands(commands(minimum=version), "fixture", is_mpv=True)

    def test_ios_build_metadata_is_not_accepted_as_macos(self):
        with self.assertRaisesRegex(ValueError, "minimum macOS"):
            verifier.check_load_commands(commands(platform="2"), "fixture", is_mpv=True)

    def test_security_framework_is_required_for_mpv(self):
        with self.assertRaisesRegex(ValueError, "Security.framework"):
            verifier.check_load_commands(commands(dependency="/usr/lib/libSystem.B.dylib"), "fixture", is_mpv=True)

    def test_empty_tool_output_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "no dylib load commands"):
            verifier.check_load_commands("", "fixture")

    def test_parser_preserves_spaces_in_rpaths(self):
        _, rpaths, minimum = verifier.load_commands(commands(rpath="/a path/Frameworks"))
        self.assertEqual(rpaths, ["/a path/Frameworks"])
        self.assertEqual(minimum, "11.0")

    def test_legacy_macos_load_command_is_supported(self):
        text = commands().replace("cmd LC_BUILD_VERSION", "cmd LC_VERSION_MIN_MACOSX")
        text = text.replace("platform 1\n", "").replace("minos 11.0", "version 11.0")
        verifier.check_load_commands(text, "fixture", is_mpv=True)

    def test_each_required_network_component_is_checked(self):
        parts = [b"mpv_curl_avio\0", b"HTTP2\0", b"AppleSecTrust\0", b"OpenSSL\0"]
        verifier.check_curl_slice(b"".join(parts), "fixture")
        for missing in range(len(parts)):
            with self.subTest(missing=parts[missing]), self.assertRaisesRegex(ValueError, "missing"):
                verifier.check_curl_slice(b"".join(part for index, part in enumerate(parts) if index != missing), "fixture")

    def test_suffix_merged_feature_literal_is_allowed(self):
        verifier.check_curl_slice(
            b"mpv_curl_avio\0HTTP2\0MultiSSL\0AppleSecTrust\0OpenSSL\0", "fixture")

    def test_all_three_transport_licenses_are_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = make_mpv_package(Path(temp))
            verifier.check_curl_licenses(binary.parent)

    def test_each_transport_license_is_required_in_the_package(self):
        for name in ("curl.txt", "nghttp2.txt", "openssl.txt"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                package = Path(temp)
                binary = make_mpv_package(package)
                (binary.parent / "Resources/Licenses/curl-transport" / name).unlink()
                with patch.object(verifier, "run") as run:
                    with self.assertRaisesRegex(ValueError, name):
                        verifier.verify_package(package)
                    run.assert_not_called()

    def test_empty_or_whitespace_transport_licenses_are_rejected(self):
        for name in ("curl.txt", "nghttp2.txt", "openssl.txt"):
            for content in (b"", b" \n\t"):
                with self.subTest(name=name, content=content), tempfile.TemporaryDirectory() as temp:
                    binary = make_mpv_package(Path(temp))
                    (binary.parent / "Resources/Licenses/curl-transport" / name).write_bytes(content)
                    with self.assertRaisesRegex(ValueError, name):
                        verifier.check_curl_licenses(binary.parent)

    def test_a_missing_cpu_slice_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)
            make_mpv_package(package)
            with patch.object(verifier, "run", return_value="arm64\n"):
                with self.assertRaisesRegex(ValueError, "expected arm64 and x86_64"):
                    verifier.verify_package(package)

    def test_features_on_only_one_cpu_do_not_satisfy_universal_package(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)
            make_mpv_package(package)

            def fake_run(*args):
                if args[:2] == ("lipo", "-archs"):
                    return "x86_64 arm64\n"
                if args[0] == "otool":
                    return commands()
                if args[0] == "lipo" and args[2] == "-thin":
                    data = b"mpv_curl_avio\0SSL\0AppleSecTrust\0OpenSSL\0"
                    if args[3] == "arm64":
                        data += b"HTTP2\0"
                    Path(args[5]).write_bytes(data)
                    return ""
                self.fail("Unexpected command: {}".format(args))

            with patch.object(verifier, "run", side_effect=fake_run):
                with self.assertRaisesRegex(ValueError, r"x86_64.*HTTP/2"):
                    verifier.verify_package(package)


if __name__ == "__main__":
    unittest.main(verbosity=2)
