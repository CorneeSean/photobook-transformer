/* tslint:disable */
/* eslint-disable */

export class PhotoViewer {
    private constructor();
    free(): void;
    [Symbol.dispose](): void;
    /**
     * Which GPU backend bound: "webgpu" or "webgl2".
     */
    backend(): string;
    /**
     * Reconfigure the surface to a new backing size (physical pixels) and redraw.
     */
    resize(width: number, height: number): void;
    /**
     * Set the letterbox/clear colour (sRGB, 0..1) — the shell passes the theme's
     * card colour so the canvas matches the page background.
     */
    set_background(r: number, g: number, b: number): void;
    /**
     * Exposure in stops; linear gain = 2**stops. Cheap — updates a uniform + redraws.
     */
    set_exposure(stops: number): void;
    /**
     * Upload an RGBA8 image (row-major, top row first) and display it.
     */
    set_image(rgba: Uint8Array, width: number, height: number): void;
}

/**
 * Create a viewer bound to `canvas`. Async: requests the GPU adapter + device.
 */
export function create_viewer(canvas: HTMLCanvasElement): Promise<PhotoViewer>;

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly __wbg_photoviewer_free: (a: number, b: number) => void;
    readonly create_viewer: (a: any) => any;
    readonly photoviewer_set_image: (a: number, b: number, c: number, d: number, e: number) => void;
    readonly photoviewer_backend: (a: number) => [number, number];
    readonly photoviewer_set_background: (a: number, b: number, c: number, d: number) => void;
    readonly photoviewer_set_exposure: (a: number, b: number) => void;
    readonly photoviewer_resize: (a: number, b: number, c: number) => void;
    readonly wasm_bindgen__convert__closures_____invoke__h042a4ab1f1630212: (a: number, b: number, c: any) => [number, number];
    readonly wasm_bindgen__convert__closures_____invoke__h1d4b9406f012d333: (a: number, b: number, c: any, d: any) => void;
    readonly wasm_bindgen__convert__closures_____invoke__he4086b7f26f1a23e: (a: number, b: number, c: any) => void;
    readonly wasm_bindgen__convert__closures_____invoke__he4086b7f26f1a23e_2: (a: number, b: number, c: any) => void;
    readonly __wbindgen_malloc: (a: number, b: number) => number;
    readonly __wbindgen_realloc: (a: number, b: number, c: number, d: number) => number;
    readonly __wbindgen_exn_store: (a: number) => void;
    readonly __externref_table_alloc: () => number;
    readonly __wbindgen_externrefs: WebAssembly.Table;
    readonly __wbindgen_free: (a: number, b: number, c: number) => void;
    readonly __wbindgen_destroy_closure: (a: number, b: number) => void;
    readonly __externref_table_dealloc: (a: number) => void;
    readonly __wbindgen_start: () => void;
}

export type SyncInitInput = BufferSource | WebAssembly.Module;

/**
 * Instantiates the given `module`, which can either be bytes or
 * a precompiled `WebAssembly.Module`.
 *
 * @param {{ module: SyncInitInput }} module - Passing `SyncInitInput` directly is deprecated.
 *
 * @returns {InitOutput}
 */
export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

/**
 * If `module_or_path` is {RequestInfo} or {URL}, makes a request and
 * for everything else, calls `WebAssembly.instantiate` directly.
 *
 * @param {{ module_or_path: InitInput | Promise<InitInput> }} module_or_path - Passing `InitInput` directly is deprecated.
 *
 * @returns {Promise<InitOutput>}
 */
export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;
