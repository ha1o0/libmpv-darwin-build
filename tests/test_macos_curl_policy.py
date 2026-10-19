"""Compile and exercise the actual patched curl defaults and fallback policy.

Uses the pinned mpv sources passed with --mpv-source. The tiny C fixture replaces
configuration allocation, AV dictionaries and option recording; all decisions
under test are extracted from the patched production source. No mpv build or
network access is needed. Requires Python 3.8+, Git and a C11 compiler.
"""
import argparse
from pathlib import Path
import tempfile

from test_curl_network_policy import definition, patch_source, run


def initializer(source, typename):
    text = definition(source, rf"\.defaults = &\(const struct {typename}\)")
    return text[text.index("{"):].strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mpv-source", type=Path, required=True)
    parser.add_argument("--cc", default="cc")
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    if args.work_dir:
        args.work_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="macos-curl-policy-", dir=args.work_dir))
    source_dir = patch_source(args.mpv_source, scratch)
    curl = (source_dir / "stream/stream_curl.c").read_text(encoding="utf8")
    network = (source_dir / "stream/network.c").read_text(encoding="utf8")
    demux = (source_dir / "demux/demux_lavf.c").read_text(encoding="utf8")

    types = definition(curl, r"(?m)^struct curl_opts \{", True)
    curl_defaults = initializer(curl, "curl_opts")
    network_defaults = initializer(network, "mp_network_opts")
    guard = curl[curl.index("// Older lavf has a bug"):curl.index("#define OPT_BASE_STRUCT")]
    helper = definition(curl, r"(?m)^bool mp_curl_is_enabled\(")
    hls_start = demux.index("#if HAVE_LIBCURL\n    // When nested HTTP requests")
    hls_policy = demux[hls_start:demux.index("#endif", hls_start) + len("#endif")]
    tls_start = curl.index("    curl_easy_setopt(c, CURLOPT_SSL_OPTIONS,")
    tls_policy = curl[tls_start:curl.index("    if (p->net_opts->tls_ca_file)", tls_start)]

    code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#define AV_VERSION_INT(a, b, c) (((a) << 16) | ((b) << 8) | (c))
#define CURL_HTTP_VERSION_NONE 0
struct mp_network_opts { const char *useragent; double timeout; bool tls_verify; };
''' + types
    checks = []
    versions = [((61, 7, 102), False), ((61, 7, 103), True),
                ((61, 7, 104), True), ((61, 8, 0), False),
                ((62, 3, 102), False), ((62, 3, 103), True),
                ((62, 4, 0), False), ((62, 12, 102), True),
                ((62, 13, 0), False), ((62, 15, 100), False),
                ((62, 15, 101), True), ((63, 2, 0), True)]
    for index, (version, expected) in enumerate(versions):
        code += f"""
#undef CURL_BY_DEFAULT
#undef LIBAVFORMAT_VERSION_INT
#define LIBAVFORMAT_VERSION_INT AV_VERSION_INT{version}
{guard}
static bool curl_default_{index}(void) {{
    const struct curl_opts opts = {curl_defaults};
    return opts.enabled;
}}
"""
        checks.append(f"assert(curl_default_{index}() == {int(expected)});")
    for major in [61, 63]:
        for have_curl in [0, 1]:
            code += f"""
#undef HAVE_LIBCURL
#define HAVE_LIBCURL {have_curl}
#undef LIBAVFORMAT_VERSION_MAJOR
#define LIBAVFORMAT_VERSION_MAJOR {major}
static bool tls_default_{major}_{have_curl}(void) {{
    const struct mp_network_opts opts = {network_defaults};
    return opts.tls_verify;
}}
"""
            checks.append(f"assert(tls_default_{major}_{have_curl}() == {int(major >= 63)});")

    code += r'''
struct mpv_global { struct curl_opts opts; };
static const int curl_conf;
static int allocated, released;
static void *mp_get_config_group(void *parent, struct mpv_global *global, const void *conf)
{
    (void)parent;
    assert(conf == &curl_conf);
    struct curl_opts *copy = malloc(sizeof(*copy));
    assert(copy);
    *copy = global->opts;
    allocated++;
    return copy;
}
static void talloc_free(void *ptr) { free(ptr); released++; }
''' + helper + r'''
struct demuxer { bool access_references; struct mpv_global *global; };
typedef struct { int persistent, multiple; } AVDictionary;
static int av_dict_set(AVDictionary **dict, const char *name, const char *value, int flags)
{
    assert(flags == 0);
    if (!strcmp(name, "http_persistent")) (*dict)->persistent = atoi(value);
    else if (!strcmp(name, "http_multiple")) (*dict)->multiple = atoi(value);
    else assert(false);
    return 0;
}
'''
    for have_curl in [0, 1]:
        code += f"""
#undef HAVE_LIBCURL
#define HAVE_LIBCURL {have_curl}
static void apply_hls_{have_curl}(struct demuxer *demuxer, AVDictionary *dopts)
{{
    (void)demuxer;
    (void)dopts;
{hls_policy}
}}
"""
    code += r'''
enum { CURLOPT_SSL_OPTIONS, CURLOPT_SSL_VERIFYPEER, CURLOPT_SSL_VERIFYHOST };
#define CURLSSLOPT_NATIVE_CA 16
typedef int CURL;
static long ssl_options, peer, host;
static void record_setopt(CURL *curl, int option, long value)
{
    (void)curl;
    if (option == CURLOPT_SSL_OPTIONS) ssl_options = value;
    else if (option == CURLOPT_SSL_VERIFYPEER) peer = value;
    else if (option == CURLOPT_SSL_VERIFYHOST) host = value;
    else assert(false);
}
#define curl_easy_setopt record_setopt
struct priv { struct mp_network_opts *net_opts; };
static void apply_tls(struct priv *p, CURL *c)
{
''' + tls_policy + r'''
}
int main(void)
{
''' + "\n".join(checks) + r'''
    struct mpv_global global = {0};
    struct demuxer demuxer = { .access_references = true, .global = &global };
    assert(!mp_curl_is_enabled(&global));
    AVDictionary dict = {1, -1};
    apply_hls_1(&demuxer, &dict);
    assert(dict.persistent == 1 && dict.multiple == -1);

    // A later per-file enable must be observed; it must not be cached globally.
    global.opts.enabled = true;
    assert(mp_curl_is_enabled(&global));
    apply_hls_1(&demuxer, &dict);
    assert(dict.persistent == 0 && dict.multiple == 1);
    demuxer.access_references = false;
    dict = (AVDictionary){1, -1};
    apply_hls_1(&demuxer, &dict);
    assert(dict.persistent == 1 && dict.multiple == -1);
    demuxer.access_references = true;
    apply_hls_0(&demuxer, &dict);
    assert(dict.persistent == 1 && dict.multiple == -1);
    global.opts.enabled = false;
    dict = (AVDictionary){7, 9};
    apply_hls_1(&demuxer, &dict);
    assert(dict.persistent == 7 && dict.multiple == 9);
    assert(allocated == released);

    // Explicit TLS options must keep working independently of their default.
    struct mp_network_opts network_opts = {0};
    struct priv p = {.net_opts = &network_opts};
    apply_tls(&p, NULL);
    assert(peer == 0 && host == 0 && ssl_options == CURLSSLOPT_NATIVE_CA);
    network_opts.tls_verify = true;
    apply_tls(&p, NULL);
    assert(peer == 1 && host == 2 && ssl_options == CURLSSLOPT_NATIVE_CA);
    puts("PASS: curl version boundaries, runtime HLS fallback, TLS defaults and overrides");
    return 0;
}
'''
    probe = scratch / "policy.c"
    probe.write_text(code, encoding="utf8")
    binary = scratch / "policy"
    run([args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror", str(probe), "-o", str(binary)])
    print(run([str(binary)]).strip())
    print("Artifacts: " + str(scratch))


if __name__ == "__main__":
    main()
