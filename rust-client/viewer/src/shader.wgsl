// Fullscreen textured quad with contain-fit scaling and a linear exposure gain.
//
// sRGB is handled explicitly here rather than via sRGB texture/surface formats,
// because Chrome's WebGPU canvas typically offers no sRGB *surface* format (only a
// view format) — so relying on the surface to re-encode left the image too dark.
// The texture is plain unorm (sampled values are the stored sRGB-encoded bytes); we
// decode to linear, apply exposure in linear space, then re-encode to sRGB for the
// (also non-sRGB) surface. Round-trips to the original at exposure = 1.

struct Uniforms {
  exposure: f32,   // linear gain = 2**stops
  scale_x: f32,    // quad half-extent to contain-fit the image in the canvas
  scale_y: f32,
  _pad: f32,
};

@group(0) @binding(0) var<uniform> u: Uniforms;
@group(1) @binding(0) var tex: texture_2d<f32>;
@group(1) @binding(1) var samp: sampler;

fn srgb_to_linear(c: vec3<f32>) -> vec3<f32> {
  let low = c / 12.92;
  let high = pow((c + 0.055) / 1.055, vec3<f32>(2.4));
  return select(high, low, c <= vec3<f32>(0.04045));
}

fn linear_to_srgb(c: vec3<f32>) -> vec3<f32> {
  let low = c * 12.92;
  let high = 1.055 * pow(c, vec3<f32>(1.0 / 2.4)) - 0.055;
  return select(high, low, c <= vec3<f32>(0.0031308));
}

struct VsOut {
  @builtin(position) pos: vec4<f32>,
  @location(0) uv: vec2<f32>,
};

@vertex
fn vs(@builtin(vertex_index) vi: u32) -> VsOut {
  // Two triangles covering the (scaled) quad. UVs map (0,0) to top-left of the
  // image so the picture is upright (NDC y up, texture v down).
  var pos = array<vec2<f32>, 6>(
    vec2<f32>(-1.0, -1.0), vec2<f32>( 1.0, -1.0), vec2<f32>(-1.0,  1.0),
    vec2<f32>(-1.0,  1.0), vec2<f32>( 1.0, -1.0), vec2<f32>( 1.0,  1.0),
  );
  var uv = array<vec2<f32>, 6>(
    vec2<f32>(0.0, 1.0), vec2<f32>(1.0, 1.0), vec2<f32>(0.0, 0.0),
    vec2<f32>(0.0, 0.0), vec2<f32>(1.0, 1.0), vec2<f32>(1.0, 0.0),
  );
  var out: VsOut;
  out.pos = vec4<f32>(pos[vi] * vec2<f32>(u.scale_x, u.scale_y), 0.0, 1.0);
  out.uv = uv[vi];
  return out;
}

@fragment
fn fs(in: VsOut) -> @location(0) vec4<f32> {
  let s = textureSample(tex, samp, in.uv);        // stored sRGB-encoded bytes
  let lin = srgb_to_linear(s.rgb) * u.exposure;   // exposure in linear space
  return vec4<f32>(linear_to_srgb(lin), s.a);     // re-encode for the non-sRGB surface
}
