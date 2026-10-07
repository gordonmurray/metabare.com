// Which models the app runs, at which precision, and where their files are.
// Chosen in the browser inference spike: CLIP at q4f16 and MiniLM at q8 agree
// across WebGPU and WASM and fit the download budget (spike/README.md).

export const MODELS = {
    clip: { id: "Xenova/clip-vit-base-patch32", dtype: "q4f16", dims: 512 },
    text: { id: "Xenova/all-MiniLM-L6-v2", dtype: "q8", dims: 384 },
} as const;

/**
 * Identifies the vector space every stored vector belongs to. Vectors made
 * with different models or precisions cannot be compared, so a library is
 * only valid for the space it was built in.
 */
export const VECTOR_SPACE = `${MODELS.clip.id}@${MODELS.clip.dtype}+${MODELS.text.id}@${MODELS.text.dtype}`;

// Set at build time for the deployed site, where models and the runtime live
// under versioned paths that can be cached forever.
export const MODEL_BASE: string = import.meta.env.VITE_MODEL_BASE ?? "/models/";
export const ORT_BASE: string = import.meta.env.VITE_ORT_BASE ?? "/ort/";

export const LIMITS = {
    /** Largest file accepted, in bytes. */
    maxFileBytes: 50 * 1024 * 1024,
    /** Largest decoded image, in pixels. Bigger images are refused, not resized. */
    maxImagePixels: 50_000_000,
    /** Longest side of a stored thumbnail, in pixels. */
    thumbnailSize: 320,
    /** Characters of a note kept for the result excerpt. */
    excerptChars: 280,
};

export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];
export const NOTE_EXTENSIONS = [".txt", ".md", ".markdown"];
