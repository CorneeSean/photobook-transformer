/* tslint:disable */
/* eslint-disable */

/**
 * A developed image handed back to JS for canvas display (visual proof of decode).
 */
export class Developed {
    private constructor();
    free(): void;
    [Symbol.dispose](): void;
    readonly rgba: Uint8Array;
    height: number;
    width: number;
}

/**
 * Parses the input PDF file (as a base64 encoded string), outputs the parsed
 * PDF (and any warnings) as a JSON object
 *
 * ```js,no_run,ignore
 * let input = JSON.encode({ pdf_base64: atob(my_pdf) });
 * let doc = JSON.parse(Pdf_BytesToPdfDocument(input));
 * console.log(doc.pdf);
 * console.log(doc.warnings);
 * // {
 * //   status: 0,
 * //   data: {
 * //     metadata: ...,
 * //     resources: ...,
 * //     bookmarks: ...,
 * //     pages: [{ media_box, trim_box, crop_box, ops }]
 * //    }
 * // }
 * ```
 */
export function Pdf_BytesToDocumentSync(input: string): string;

/**
 * Takes a `PdfDocument` JS object and returns the base64 PDF bytes
 */
export function Pdf_DocumentToBytesSync(input: string): string;

/**
 * Parses the input HTML, converts it to PDF pages and outputs the generated
 * PDF as a JSON object
 *
 * ```js,no_run,ignore
 * let html = "<!doctype html><html><body><h1>Hello!</h1></body></html>";
 * let input = JSON.encode({ html: html, title "My PDF!" });
 * let document = JSON.parse(Pdf_HtmlToPdfDocument(input));
 * console.log(document);
 * // {
 * //   status: 0,
 * //   data: {
 * //     metadata: ...,
 * //     resources: ...,
 * //     bookmarks: ...,
 * //     pages: [{ media_box, trim_box, crop_box, ops }]
 * //    }
 * // }
 * ```
 */
export function Pdf_HtmlToDocumentSync(input: string): string;

/**
 * Takes a `PdfPage` JS object and outputs the SVG string for that page
 */
export function Pdf_PageToSvgSync(input: string): string;

/**
 * Helper function that takes a PDF page and outputs a list of all
 * images IDs / fonts IDs that have to be gathered from the documents
 * resources in order to render this page.
 */
export function Pdf_ResourcesForPageSync(input: string): string;

/**
 * Decode a RAW and develop it to RGBA8 (for drawing to a `<canvas>`).
 */
export function develop(bytes: Uint8Array): Developed;

/**
 * Decode a RAW from memory and return a short description (proves rawloader in WASM).
 */
export function raw_info(bytes: Uint8Array): string;

/**
 * Full slice: decode a RAW, develop it crudely to RGB8, and return a one-page PDF.
 */
export function raw_to_pdf(bytes: Uint8Array): Uint8Array;

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly raw_info: (a: number, b: number) => [number, number, number, number];
    readonly __wbg_developed_free: (a: number, b: number) => void;
    readonly __wbg_get_developed_width: (a: number) => number;
    readonly __wbg_set_developed_width: (a: number, b: number) => void;
    readonly __wbg_get_developed_height: (a: number) => number;
    readonly __wbg_set_developed_height: (a: number, b: number) => void;
    readonly developed_rgba: (a: number) => [number, number];
    readonly develop: (a: number, b: number) => [number, number, number];
    readonly raw_to_pdf: (a: number, b: number) => [number, number, number, number];
    readonly Pdf_HtmlToDocumentSync: (a: number, b: number) => [number, number];
    readonly Pdf_BytesToDocumentSync: (a: number, b: number) => [number, number];
    readonly Pdf_ResourcesForPageSync: (a: number, b: number) => [number, number];
    readonly Pdf_PageToSvgSync: (a: number, b: number) => [number, number];
    readonly Pdf_DocumentToBytesSync: (a: number, b: number) => [number, number];
    readonly __wbindgen_free: (a: number, b: number, c: number) => void;
    readonly __wbindgen_malloc: (a: number, b: number) => number;
    readonly __wbindgen_realloc: (a: number, b: number, c: number, d: number) => number;
    readonly __wbindgen_externrefs: WebAssembly.Table;
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
