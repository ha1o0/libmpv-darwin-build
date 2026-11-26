"""Run CPU reshaping equations; full GPU comparison runs in the iOS release smoke."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class DoviPipMathTest(unittest.TestCase):
    def test_polynomial_mmr_pq_and_packing(self):
        patch = (ROOT / 'patches/mpv-ios-dovi-pip-subtitles.patch').read_text()
        section = patch.split('+++ b/video/out/ios_dovi_pip.h\n', 1)[1].split('diff --git', 1)[0]
        header = '\n'.join(line[1:] for line in section.splitlines() if line.startswith('+'))
        header = header.replace('#include <libplacebo/colorspace.h>', '')
        # Declarations only; pixel conversion below is extracted production code.
        declarations = r"""
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef struct {float m[3][3];} pl_matrix3x3;
typedef struct {pl_matrix3x3 mat; float c[3];} pl_transform3x3;
struct pl_dovi_metadata {
 float nonlinear_offset[3]; pl_matrix3x3 nonlinear, linear;
 struct pl_reshape_data {uint8_t num_pivots;float pivots[9];uint8_t method[8];
 float poly_coeffs[8][3];uint8_t mmr_order[8];float mmr_constant[8];float mmr_coeffs[8][3][7];} comp[3];
};
#define PL_COLOR_SYSTEM_DOLBYVISION 1
struct pl_color_repr {int sys;const struct pl_dovi_metadata *dovi;};
float pl_color_repr_normalize(struct pl_color_repr *r);
pl_transform3x3 pl_color_repr_decode(struct pl_color_repr *r, const void *a);
void pl_matrix3x3_mul(pl_matrix3x3 *a, const pl_matrix3x3 *b);
"""
        cases = r"""
int main(void) {
 struct pl_dovi_metadata metadata={0};
 struct dv_pip_transform t={.metadata=&metadata,
  .nonlinear={.mat={{{1,0,0},{0,1,0},{0,0,1}}}},
  .linear={{{1,0,0},{0,1,0},{0,0,1}}}};
 for (int c=0;c<3;c++) {
  metadata.comp[c].num_pivots=2;metadata.comp[c].pivots[1]=1;
  metadata.comp[c].poly_coeffs[0][1]=1;
 }
 for (int n=0;n<=1000;n++) {
  float input[3]={n/1000.0f,n/1000.0f,n/1000.0f},out[3];
  dv_pip_color(&t,input,out);
  for(int c=0;c<3;c++) assert(fabsf(out[c]-input[c])<0.00015f);
 }
 float input[3]={.2f,.4f,.6f},out[3];
 metadata.comp[0].poly_coeffs[0][1]=0;metadata.comp[0].poly_coeffs[0][2]=1;
 dv_pip_color(&t,input,out);assert(fabsf(out[0]-.04f)<.0001f);
 // MMR order 3 includes powers of each cross term (not mixed-order products).
 metadata.comp[1].method[0]=1;metadata.comp[1].mmr_order[0]=3;
 metadata.comp[1].mmr_coeffs[0][0][3]=1;
 metadata.comp[1].mmr_coeffs[0][1][4]=1;
 metadata.comp[1].mmr_coeffs[0][2][6]=1;
 dv_pip_color(&t,input,out);
 float expected=.2f*.4f+powf(.2f*.6f,2)+powf(.2f*.4f*.6f,3);
 assert(fabsf(out[1]-expected)<.0001f);
 float color[3]={1,.5f,0};uint32_t packed=dv_pip_pack(color);
 assert((packed&1023)==0 && ((packed>>10)&1023)==512 && ((packed>>20)&1023)==1023);
 assert(fabsf(dv_pq_encode(.0203f)-.58069f)<.0001f);
 puts("DOVI_PIP_MATH_OK");
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'check.c'
            source.write_text(declarations + header + cases)
            binary = Path(tmp) / 'check'
            subprocess.run(['cc', '-std=c11', str(source), '-lm', '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
