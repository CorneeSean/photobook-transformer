//! GPU photo viewer (wgpu) — the "pixels in Rust" leg of the spike.
//!
//! The JS shell hands us its `<canvas>`; we create a WebGPU surface directly on it
//! (no per-frame pixel copy back to JS), upload the selected image as an sRGB
//! texture, and render a contain-fit quad. Exposure is a live fragment-shader gain
//! driven by the shell's slider. Renders are on-demand (set_image / set_exposure /
//! resize each draw one frame) — a still viewer needs no rAF loop.

use wasm_bindgen::prelude::*;
use web_sys::HtmlCanvasElement;
use wgpu::util::DeviceExt;

#[repr(C)]
#[derive(Copy, Clone, bytemuck::Pod, bytemuck::Zeroable)]
struct Uniforms {
    exposure: f32,
    scale_x: f32,
    scale_y: f32,
    _pad: f32,
}

#[wasm_bindgen]
pub struct PhotoViewer {
    surface: wgpu::Surface<'static>,
    device: wgpu::Device,
    queue: wgpu::Queue,
    config: wgpu::SurfaceConfiguration,
    pipeline: wgpu::RenderPipeline,
    tex_layout: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
    uniform_buffer: wgpu::Buffer,
    ubo_bind_group: wgpu::BindGroup,
    /// (texture bind group, width, height) — None until an image is set.
    image: Option<(wgpu::BindGroup, u32, u32)>,
    uniforms: Uniforms,
    /// Letterbox / clear colour (linear RGB), set by JS to the theme's card colour
    /// so the canvas background matches the page (and tracks light/dark).
    clear: wgpu::Color,
    /// Which GPU backend actually bound: "webgpu" or "webgl2".
    backend: String,
}

/// Create a viewer bound to `canvas`. Async: requests the GPU adapter + device.
#[wasm_bindgen]
pub async fn create_viewer(canvas: HtmlCanvasElement) -> Result<PhotoViewer, JsValue> {
    console_error_panic_hook::set_once();

    let width = canvas.width().max(1);
    let height = canvas.height().max(1);

    // WebGPU first, WebGL2 as fallback — same code + WGSL (naga transpiles to GLSL).
    let instance = wgpu::Instance::new(wgpu::InstanceDescriptor {
        backends: wgpu::Backends::BROWSER_WEBGPU | wgpu::Backends::GL,
        ..Default::default()
    });
    let surface = instance
        .create_surface(wgpu::SurfaceTarget::Canvas(canvas))
        .map_err(|e| JsValue::from_str(&format!("create_surface: {e}")))?;

    let adapter = instance
        .request_adapter(&wgpu::RequestAdapterOptions {
            power_preference: wgpu::PowerPreference::HighPerformance,
            force_fallback_adapter: false,
            compatible_surface: Some(&surface),
        })
        .await
        .ok_or_else(|| JsValue::from_str("no GPU adapter (WebGPU or WebGL2)"))?;

    let backend_name = match adapter.get_info().backend {
        wgpu::Backend::BrowserWebGpu => "webgpu",
        wgpu::Backend::Gl => "webgl2",
        _ => "wgpu",
    }
    .to_string();

    let (device, queue) = adapter
        .request_device(
            &wgpu::DeviceDescriptor {
                label: None,
                required_features: wgpu::Features::empty(),
                // WebGL2-safe limits so the GL fallback works; raised to the
                // adapter's actual limits on WebGPU.
                required_limits: wgpu::Limits::downlevel_webgl2_defaults()
                    .using_resolution(adapter.limits()),
                memory_hints: wgpu::MemoryHints::default(),
            },
            None,
        )
        .await
        .map_err(|e| JsValue::from_str(&format!("request_device: {e}")))?;

    let caps = surface.get_capabilities(&adapter);
    let format = caps
        .formats
        .iter()
        .copied()
        .find(|f| f.is_srgb())
        .unwrap_or(caps.formats[0]);
    let config = wgpu::SurfaceConfiguration {
        usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
        format,
        width,
        height,
        present_mode: wgpu::PresentMode::Fifo,
        alpha_mode: caps.alpha_modes[0],
        view_formats: vec![],
        desired_maximum_frame_latency: 2,
    };
    surface.configure(&device, &config);

    let uniforms = Uniforms {
        exposure: 1.0,
        scale_x: 1.0,
        scale_y: 1.0,
        _pad: 0.0,
    };
    let uniform_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
        label: Some("uniforms"),
        contents: bytemuck::bytes_of(&uniforms),
        usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
    });

    let ubo_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
        label: Some("ubo"),
        entries: &[wgpu::BindGroupLayoutEntry {
            binding: 0,
            visibility: wgpu::ShaderStages::VERTEX_FRAGMENT,
            ty: wgpu::BindingType::Buffer {
                ty: wgpu::BufferBindingType::Uniform,
                has_dynamic_offset: false,
                min_binding_size: None,
            },
            count: None,
        }],
    });
    let tex_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
        label: Some("tex"),
        entries: &[
            wgpu::BindGroupLayoutEntry {
                binding: 0,
                visibility: wgpu::ShaderStages::FRAGMENT,
                ty: wgpu::BindingType::Texture {
                    sample_type: wgpu::TextureSampleType::Float { filterable: true },
                    view_dimension: wgpu::TextureViewDimension::D2,
                    multisampled: false,
                },
                count: None,
            },
            wgpu::BindGroupLayoutEntry {
                binding: 1,
                visibility: wgpu::ShaderStages::FRAGMENT,
                ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                count: None,
            },
        ],
    });

    let ubo_bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: Some("ubo"),
        layout: &ubo_layout,
        entries: &[wgpu::BindGroupEntry {
            binding: 0,
            resource: uniform_buffer.as_entire_binding(),
        }],
    });

    let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
        mag_filter: wgpu::FilterMode::Linear,
        min_filter: wgpu::FilterMode::Linear,
        ..Default::default()
    });

    let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some("viewer"),
        source: wgpu::ShaderSource::Wgsl(include_str!("shader.wgsl").into()),
    });
    let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: None,
        bind_group_layouts: &[&ubo_layout, &tex_layout],
        push_constant_ranges: &[],
    });
    let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("viewer"),
        layout: Some(&pipeline_layout),
        vertex: wgpu::VertexState {
            module: &shader,
            entry_point: Some("vs"),
            buffers: &[],
            compilation_options: Default::default(),
        },
        fragment: Some(wgpu::FragmentState {
            module: &shader,
            entry_point: Some("fs"),
            targets: &[Some(wgpu::ColorTargetState {
                format,
                blend: Some(wgpu::BlendState::REPLACE),
                write_mask: wgpu::ColorWrites::ALL,
            })],
            compilation_options: Default::default(),
        }),
        primitive: wgpu::PrimitiveState::default(),
        depth_stencil: None,
        multisample: wgpu::MultisampleState::default(),
        multiview: None,
        cache: None,
    });

    let viewer = PhotoViewer {
        surface,
        device,
        queue,
        config,
        pipeline,
        tex_layout,
        sampler,
        uniform_buffer,
        ubo_bind_group,
        image: None,
        uniforms,
        clear: wgpu::Color {
            r: 1.0,
            g: 1.0,
            b: 1.0,
            a: 1.0,
        },
        backend: backend_name,
    };
    viewer.render();
    Ok(viewer)
}

#[wasm_bindgen]
impl PhotoViewer {
    /// Upload an RGBA8 image (row-major, top row first) and display it.
    pub fn set_image(&mut self, rgba: &[u8], width: u32, height: u32) {
        let size = wgpu::Extent3d {
            width,
            height,
            depth_or_array_layers: 1,
        };
        let texture = self.device.create_texture(&wgpu::TextureDescriptor {
            label: Some("photo"),
            size,
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba8UnormSrgb,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        self.queue.write_texture(
            wgpu::ImageCopyTexture {
                texture: &texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            rgba,
            wgpu::ImageDataLayout {
                offset: 0,
                bytes_per_row: Some(4 * width),
                rows_per_image: Some(height),
            },
            size,
        );
        let view = texture.create_view(&Default::default());
        let bind_group = self.device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("photo"),
            layout: &self.tex_layout,
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: wgpu::BindingResource::TextureView(&view),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: wgpu::BindingResource::Sampler(&self.sampler),
                },
            ],
        });
        self.image = Some((bind_group, width, height));
        self.recompute_scale();
        self.render();
    }

    /// Which GPU backend bound: "webgpu" or "webgl2".
    pub fn backend(&self) -> String {
        self.backend.clone()
    }

    /// Set the letterbox/clear colour (linear RGB, 0..1) — the shell passes the
    /// theme's card colour so the canvas matches the page background.
    pub fn set_background(&mut self, r: f32, g: f32, b: f32) {
        self.clear = wgpu::Color {
            r: r as f64,
            g: g as f64,
            b: b as f64,
            a: 1.0,
        };
        self.render();
    }

    /// Exposure in stops; linear gain = 2**stops. Cheap — updates a uniform + redraws.
    pub fn set_exposure(&mut self, stops: f32) {
        self.uniforms.exposure = stops.exp2();
        self.write_uniforms();
        self.render();
    }

    /// Reconfigure the surface to a new backing size (physical pixels) and redraw.
    pub fn resize(&mut self, width: u32, height: u32) {
        if width == 0 || height == 0 {
            return;
        }
        self.config.width = width;
        self.config.height = height;
        self.surface.configure(&self.device, &self.config);
        self.recompute_scale();
        self.render();
    }

    fn recompute_scale(&mut self) {
        if let Some((_, iw, ih)) = self.image {
            let (cw, ch) = (self.config.width as f32, self.config.height as f32);
            let (iw, ih) = (iw as f32, ih as f32);
            let scale = (cw / iw).min(ch / ih);
            self.uniforms.scale_x = iw * scale / cw;
            self.uniforms.scale_y = ih * scale / ch;
        } else {
            self.uniforms.scale_x = 1.0;
            self.uniforms.scale_y = 1.0;
        }
        self.write_uniforms();
    }

    fn write_uniforms(&self) {
        self.queue
            .write_buffer(&self.uniform_buffer, 0, bytemuck::bytes_of(&self.uniforms));
    }

    fn render(&self) {
        let frame = match self.surface.get_current_texture() {
            Ok(f) => f,
            Err(_) => return, // surface not ready (e.g. zero-size) — skip this frame
        };
        let view = frame.texture.create_view(&Default::default());
        let mut encoder = self
            .device
            .create_command_encoder(&wgpu::CommandEncoderDescriptor { label: None });
        {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("viewer"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        // Letterbox filled with the theme's card colour (set by JS),
                        // so the viewer matches the page background like Canvas2D did.
                        load: wgpu::LoadOp::Clear(self.clear),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
            });
            if let Some((bind_group, _, _)) = &self.image {
                pass.set_pipeline(&self.pipeline);
                pass.set_bind_group(0, &self.ubo_bind_group, &[]);
                pass.set_bind_group(1, bind_group, &[]);
                pass.draw(0..6, 0..1);
            }
        }
        self.queue.submit(Some(encoder.finish()));
        frame.present();
    }
}
