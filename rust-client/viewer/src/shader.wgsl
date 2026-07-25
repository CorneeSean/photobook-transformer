// Fullscreen textured quad with contain-fit scaling and a linear exposure gain.
// The texture is sRGB, so textureSample returns linear values; we scale by the
// exposure multiplier in linear space (physically correct) and let the sRGB
// surface re-encode on write.

struct Uniforms {
  exposure: f32,   // linear gain = 2**stops
  scale_x: f32,    // quad half-extent to contain-fit the image in the canvas
  scale_y: f32,
  _pad: f32,
};

@group(0) @binding(0) var<uniform> u: Uniforms;
@group(1) @binding(0) var tex: texture_2d<f32>;
@group(1) @binding(1) var samp: sampler;

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
  let c = textureSample(tex, samp, in.uv);
  return vec4<f32>(c.rgb * u.exposure, c.a);
}
