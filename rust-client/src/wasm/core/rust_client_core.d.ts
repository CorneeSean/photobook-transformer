/* tslint:disable */
/* eslint-disable */

export class VecStore {
    free(): void;
    [Symbol.dispose](): void;
    /**
     * Store one embedding under an integer id. Defensively pads/truncates to `dim`.
     */
    insert(id: number, emb: Float32Array): void;
    is_empty(): boolean;
    /**
     * Ids of the `k` nearest stored vectors to `query`, nearest first.
     */
    knn(query: Float32Array, k: number): Uint32Array;
    len(): number;
    constructor(dim: number);
}

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly __wbg_vecstore_free: (a: number, b: number) => void;
    readonly vecstore_new: (a: number) => number;
    readonly vecstore_insert: (a: number, b: number, c: number, d: number) => void;
    readonly vecstore_knn: (a: number, b: number, c: number, d: number) => [number, number];
    readonly vecstore_len: (a: number) => number;
    readonly vecstore_is_empty: (a: number) => number;
    readonly __wbindgen_externrefs: WebAssembly.Table;
    readonly __wbindgen_malloc: (a: number, b: number) => number;
    readonly __wbindgen_free: (a: number, b: number, c: number) => void;
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
