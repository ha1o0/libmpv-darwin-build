#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "dovi_source_spec.h"

struct fixture {
    AVDOVIMetadata metadata;
    AVDOVIRpuDataHeader header;
    AVDOVIDataMapping mapping;
};

static struct fixture mel(unsigned denominator)
{
    struct fixture f = {0};
    f.metadata.header_offset = offsetof(struct fixture, header);
    f.metadata.mapping_offset = offsetof(struct fixture, mapping);
    f.header.coef_log2_denom = denominator;
    f.header.el_spatial_resampling_filter_flag = 1;
    f.mapping.nlq_method_idc = AV_DOVI_NLQ_LINEAR_DZ;
    for (int i = 0; i < 3; i++)
        f.mapping.nlq[i].vdr_in_max = UINT64_C(1) << denominator;
    return f;
}

static const char *inspect(const struct fixture *f)
{
    struct fixture before = *f;
    const char *type = mp_dovi_enhancement_layer((const uint8_t *)f, sizeof(*f));
    assert(memcmp(&before, f, sizeof(*f)) == 0);
    return type;
}

int main(void)
{
    // FFmpeg normalizes NLQ coefficients to fixed point, including float RPUs.
    for (unsigned denominator = 0; denominator <= 32; denominator++) {
        struct fixture f = mel(denominator);
        assert(strcmp(inspect(&f), "MEL") == 0);
        for (int component = 0; component < 3; component++) {
            struct fixture changed = f;
            changed.mapping.nlq[component].nlq_offset = 1;
            assert(strcmp(inspect(&changed), "FEL") == 0);
            changed = f;
            changed.mapping.nlq[component].vdr_in_max++;
            assert(strcmp(inspect(&changed), "FEL") == 0);
            changed = f;
            changed.mapping.nlq[component].linear_deadzone_slope = 1;
            assert(strcmp(inspect(&changed), "FEL") == 0);
            changed = f;
            changed.mapping.nlq[component].linear_deadzone_threshold = 1;
            assert(strcmp(inspect(&changed), "FEL") == 0);
        }
    }
    struct fixture f = mel(23);
    assert(mp_dovi_enhancement_layer(NULL, sizeof(f)) == NULL);
    for (size_t size = 0; size < sizeof(f); size++)
        assert(mp_dovi_enhancement_layer((const uint8_t *)&f, size) == NULL);
    f.metadata.header_offset = SIZE_MAX;
    assert(inspect(&f) == NULL);
    f = mel(23);
    f.metadata.mapping_offset = 0;
    assert(inspect(&f) == NULL);
    f = mel(23);
    f.header.disable_residual_flag = 1;
    assert(inspect(&f) == NULL);
    f = mel(23);
    f.header.coef_log2_denom = 33;
    assert(inspect(&f) == NULL);
    f = mel(23);
    f.mapping.nlq_method_idc = AV_DOVI_NLQ_NONE;
    assert(inspect(&f) == NULL);
    puts("DV source metadata classification and bounds checks passed");
    return 0;
}
