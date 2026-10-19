"""Deterministically exercise the actual curl_open consumer-position race.

The dispatcher shim delivers a 243-byte response before CMD_ADD returns. The
production write_callback and on_done advance the producer while the complete,
unmodified curl_open must preserve the consumer's initial offset. Pending
transfers and explicit offsets are covered too. No network or libmpv build is
needed; requires Python 3.9+, Git and a C11 compiler.

--mpv-source applies the recipe patches to a temporary copy of pinned sources.
--patched-source instead reads an already prepared tree, for before/after
checks with an individual patch reversed. Neither mode modifies the input.
"""
import argparse
from pathlib import Path
import tempfile

from test_curl_network_policy import definition, patch_source, run


PREAMBLE = r'''
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <ctype.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MPMIN(a, b) ((a) < (b) ? (a) : (b))
#define mp_assert assert
#define MP_TRACE(...) ((void)0)
#define MP_WARN(...) ((void)0)
#define MP_ERR(...) ((void)0)
typedef int mp_thread;
typedef int mp_mutex;
typedef int mp_cond;
typedef int CURL;
typedef int CURLM;
typedef int CURLcode;
typedef struct AVClass AVClass;
enum { CURLE_OK, CURLE_ABORTED_BY_CALLBACK, CURLE_OPERATION_TIMEDOUT,
       CURLE_COULDNT_CONNECT, CURLE_COULDNT_RESOLVE_HOST,
       CURLE_COULDNT_RESOLVE_PROXY, CURLE_SSL_CONNECT_ERROR };
enum { CURLINFO_CONTENT_TYPE, CURLINFO_EFFECTIVE_URL };
#define CURL_WRITEFUNC_ERROR ((size_t)-1)
#define CURL_WRITEFUNC_PAUSE ((size_t)-2)
enum { STREAM_READ, STREAM_UNSUPPORTED, STREAM_ERROR, STREAM_NO_MATCH, STREAM_OK };
typedef struct { const char *start; size_t len; } bstr;
struct mp_network_opts { double timeout; };
struct mp_icy { int unused; };
struct mp_cancel { void (*callback)(void *); void *context; };
struct mpv_global { struct curl_ctx *curl; };
typedef struct stream {
    int mode;
    int64_t pos;
    void *priv;
    char *url, *mime_type;
    struct mp_log *log;
    struct mpv_global *global;
    struct mp_cancel *cancel;
    bool seekable, is_network, streaming, fast_skip;
    int (*fill_buffer)(struct stream *, void *, int);
    int (*seek)(struct stream *, int64_t);
    int64_t (*get_size)(struct stream *);
    int (*control)(struct stream *, int, void *);
    void (*close)(struct stream *);
} stream_t;
struct stream_open_args { const void *special_arg; };
'''


SHIMS = r'''
// A tiny arena gives every allocation a bounded lifetime, without reproducing
// talloc's ownership internals. The test does not exercise resource teardown.
static void *allocations[32];
static size_t allocation_count;
static void *allocate(void *parent, size_t size)
{
    (void)parent;
    assert(allocation_count < sizeof(allocations) / sizeof(allocations[0]));
    void *ptr = calloc(1, size);
    assert(ptr);
    allocations[allocation_count++] = ptr;
    return ptr;
}
#define talloc_zero(parent, type) ((type *)allocate(parent, sizeof(type)))
#define talloc_size allocate
static void *talloc_steal(void *parent, void *ptr) { (void)parent; return ptr; }
static char *talloc_strdup(void *parent, const char *text)
{
    char *copy = allocate(parent, strlen(text) + 1);
    strcpy(copy, text);
    return copy;
}
static void talloc_set_destructor(void *ptr, void (*destructor)(void *))
{
    assert(ptr && destructor);
}
static void priv_destructor(void *ptr) { (void)ptr; }
static const int curl_conf = 1, mp_network_conf = 2;
static void *mp_get_config_group(void *parent, struct mpv_global *global,
                                 const void *configuration)
{
    assert(global && global->curl);
    if (configuration == &curl_conf) {
        struct curl_opts *opts = talloc_zero(parent, struct curl_opts);
        opts->enabled = true;
        opts->buffer_size = 1024;
        return opts;
    }
    assert(configuration == &mp_network_conf);
    return talloc_zero(parent, struct mp_network_opts);
}
static bstr bstr0(const char *text)
{
    return (bstr){text, text ? strlen(text) : 0};
}
static bstr bstr_split(bstr text, const char *separator, bstr *rest)
{
    assert(strlen(separator) == 1 && !rest);
    const char *at = memchr(text.start, separator[0], text.len);
    return (bstr){text.start, at ? (size_t)(at - text.start) : text.len};
}
static bstr bstr_strip(bstr text)
{
    while (text.len && isspace((unsigned char)*text.start)) {
        text.start++;
        text.len--;
    }
    while (text.len && isspace((unsigned char)text.start[text.len - 1])) text.len--;
    return text;
}
static char *bstrto0(void *parent, bstr text)
{
    char *copy = allocate(parent, text.len + 1);
    memcpy(copy, text.start, text.len);
    return copy;
}
static const struct curl_scheme *curl_scheme_lookup(bstr url)
{
    static const struct curl_scheme scheme = {
        .scheme = {"http", 4}, .proto = MP_CURL_PROTO_HTTP,
    };
    assert(url.len >= 7 && !memcmp(url.start, "http://", 7));
    return &scheme;
}
static struct mp_icy *mp_icy_new(void *parent)
{
    return talloc_zero(parent, struct mp_icy);
}
static void mp_icy_process(struct mp_icy *icy, const char *data, size_t length,
                           void (*write)(void *, const char *, size_t), void *context)
{
    assert(icy);
    write(context, data, length);
}
static void mp_mutex_init(mp_mutex *mutex) { *mutex = 0; }
static void mp_mutex_lock(mp_mutex *mutex) { assert(*mutex == 0); *mutex = 1; }
static void mp_mutex_unlock(mp_mutex *mutex) { assert(*mutex == 1); *mutex = 0; }
static void mp_cond_init(mp_cond *condition) { *condition = 0; }
static void mp_cond_broadcast(mp_cond *condition) { (*condition)++; }
static void mp_cond_wait(mp_cond *condition, mp_mutex *mutex)
{
    (void)condition;
    (void)mutex;
    assert(!"the deterministic dispatcher must already have delivered headers");
}
static double mp_time_sec(void) { return 1.0; }
static CURL *curl_easy_init(void) { return talloc_zero(NULL, CURL); }
static int curl_easy_getinfo(CURL *curl, int info, void *output)
{
    assert(curl);
    const char *value;
    if (info == CURLINFO_CONTENT_TYPE) value = "application/vnd.apple.mpegurl; charset=utf-8";
    else {
        assert(info == CURLINFO_EFFECTIVE_URL);
        value = "http://fixture.invalid/playlist.m3u8";
    }
    memcpy(output, &value, sizeof(value));
    return CURLE_OK;
}
static void setup_curl(struct priv *p) { assert(p->curl && p->buffer && p->net_opts); }
static void on_cancel(void *context) { (void)context; }
static void mp_cancel_set_cb(struct mp_cancel *cancel, void (*callback)(void *),
                              void *context)
{
    assert(cancel);
    cancel->callback = callback;
    cancel->context = context;
}
static bool is_recoverable_error(CURLcode code) { (void)code; return false; }
static bool retry_request(struct priv *p)
{
    (void)p;
    assert(!"a successful single response must not retry");
    return false;
}
static void start_request(struct priv *p)
{
    (void)p;
    assert(!"a complete unchunked response must not start another request");
}
static void log_curl_error(struct priv *p, const char *label, CURLcode code)
{
    (void)p; (void)label; (void)code;
    assert(!"the dispatcher models only successful responses");
}
static void cmd_async(struct priv *p, enum cmd_kind kind)
{
    (void)p; (void)kind;
    assert(!"the fixture never fills the ring buffer");
}
static int curl_seek(struct stream *s, int64_t position)
{
    (void)s; (void)position;
    assert(!"this regression test must not seek to repair an incorrect initial position");
    return 0;
}
static int64_t curl_get_size(struct stream *s) { return ((struct priv *)s->priv)->content_size; }
static int curl_control(struct stream *s, int command, void *arg)
{
    (void)s; (void)command; (void)arg;
    return STREAM_UNSUPPORTED;
}
static void curl_close(struct stream *s) { (void)s; }
'''


DISPATCHER = r'''
enum { RESPONSE_SIZE = 243, PARTIAL_SIZE = 79 };
static char response[RESPONSE_SIZE];
static bool complete_before_open, response_seekable;
static uint64_t expected_offset;
static int dispatches;
static void cmd_sync(struct priv *p, enum cmd_kind kind, int64_t position, bool drop)
{
    assert(kind == CMD_ADD && position == 0 && !drop);
    assert(++dispatches == 1);
    assert(p->request_start == expected_offset);
    assert(p->s->cancel->callback == on_cancel && p->s->cancel->context == p);
    // Model headers followed by a body on the worker while the opener is
    // waiting for its synchronous dispatch. All producer byte accounting and
    // completion below come from the extracted production callbacks.
    p->seekable = response_seekable;
    p->content_size = expected_offset + RESPONSE_SIZE;
    p->probed = p->stream_ok = true;
    p->active = true;
    size_t length = complete_before_open ? RESPONSE_SIZE : PARTIAL_SIZE;
    assert(write_callback(response, 1, length, p) == length);
    assert(p->request_start == expected_offset && p->request_received == length);
    if (complete_before_open) {
        p->active = false;
        on_done(p, CURLE_OK);
        assert(p->request_start == expected_offset + RESPONSE_SIZE);
        assert(p->request_received == 0 && p->finished && p->stream_eof);
    }
}
'''


CHECKS = r'''
static bool exercise(bool special_arg, int64_t offset, bool complete, bool seekable)
{
    assert(!allocation_count);
    dispatches = 0;
    expected_offset = offset;
    complete_before_open = complete;
    response_seekable = seekable;
    struct curl_ctx context = {0};
    struct mpv_global global = {.curl = &context};
    struct mp_cancel cancel = {0};
    stream_t stream = {.global = &global, .cancel = &cancel, .mode = STREAM_READ,
                       .url = "http://fixture.invalid/playlist.m3u8", .pos = -123};
    struct curl_open_args options = {.offset = offset};
    struct stream_open_args args = {.special_arg = special_arg ? &options : NULL};
    assert(curl_open(&stream, &args) == STREAM_OK);
    struct priv *p = stream.priv;
    assert(p && dispatches == 1 && !p->stream_error);
    assert(p->seekable == seekable && stream.seekable == seekable);
    assert((stream.seek != NULL) == seekable);
    assert(stream.is_network && stream.streaming && stream.fast_skip);
    assert(!strcmp(stream.mime_type, "application/vnd.apple.mpegurl"));
    size_t length = complete ? RESPONSE_SIZE : PARTIAL_SIZE;
    assert(p->count == length && !memcmp(p->buffer, response, length));
    assert(p->request_start == (uint64_t)offset + (complete ? RESPONSE_SIZE : 0));
    assert(p->request_received == (complete ? 0 : PARTIAL_SIZE));
    assert(p->finished == complete && p->stream_eof == complete && p->active != complete);

    // This is the regression assertion: buffered bytes are unread, even when
    // the producer has already advanced to EOF before curl_open returned.
    bool passed = stream.pos == offset;
    printf("%s: %s offset=%" PRId64 " %s seekable=%d consumer=%" PRId64
           " producer=%" PRIu64 " buffered=%zu\n", passed ? "PASS" : "FAIL",
           special_arg ? "special_arg" : "default", offset,
           complete ? "completed" : "pending", seekable, stream.pos,
           p->request_start, p->count);
    fflush(stdout);
    char readback[RESPONSE_SIZE] = {0};
    assert(stream.fill_buffer(&stream, readback, (int)length) == (int)length);
    assert(!memcmp(readback, response, length) && p->count == 0);
    if (complete) assert(stream.fill_buffer(&stream, readback, 1) == 0);
    while (allocation_count) free(allocations[--allocation_count]);
    return passed;
}
int main(void)
{
    // Use recognizable bytes while retaining the observed small-body size.
    for (size_t i = 0; i < sizeof(response); i++) response[i] = (char)(i + 17);
    int failures = 0;
    for (int complete = 0; complete <= 1; complete++) {
        for (int seekable = 0; seekable <= 1; seekable++) {
            failures += !exercise(false, 0, complete, seekable);
            failures += !exercise(true, 0, complete, seekable);
            failures += !exercise(true, INT64_C(4294967333), complete, seekable);
        }
    }
    printf("curl_open consumer-position cases: %d/12 passed\n", 12 - failures);
    return failures != 0;
}
'''


def probe_source(source):
    types = ""
    for name, kind in [("curl_proto", "enum"), ("curl_scheme", "struct"),
                       ("curl_reconnect_opts", "struct"), ("curl_open_args", "struct"),
                       ("curl_opts", "struct"), ("curl_ctx", "struct"),
                       ("priv", "struct"), ("cmd_kind", "enum")]:
        if name == "curl_reconnect_opts" and "struct curl_reconnect_opts {" not in source:
            continue  # Also accept the pre-reconnect source for before/after proof.
        types += definition(source, rf"(?m)^{kind} {name} \{{", True)

    def function(name):
        return definition(source, rf"(?m)^static [\w *]+\b{name}\([^;]*?\)\n\{{")

    return (PREAMBLE + types + SHIMS + function("ring_write") +
            function("write_callback") + function("on_done") + DISPATCHER +
            function("curl_fill_buffer") + function("curl_open") + CHECKS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--mpv-source", type=Path,
                        help="unpatched pinned source; apply the recipe to a temporary copy")
    source.add_argument("--patched-source", type=Path,
                        help="already prepared source tree; read it without applying patches")
    parser.add_argument("--cc", default="cc")
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    if args.work_dir:
        args.work_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="macos-curl-open-", dir=args.work_dir))
    print("Artifacts: " + str(scratch), flush=True)
    source_dir = args.patched_source or patch_source(args.mpv_source, scratch)
    source_text = (source_dir / "stream/stream_curl.c").read_text(encoding="utf8")
    probe = scratch / "open.c"
    probe.write_text(probe_source(source_text), encoding="utf8")
    binary = scratch / "open"
    # on_done compares its nonnegative signed content_size with request_start;
    # keep the production function intact rather than rewriting that expression.
    run([args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-sign-compare",
         str(probe), "-o", str(binary)])
    print(run([str(binary)]).strip())


if __name__ == "__main__":
    main()
