import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";

// Cross-origin isolation, so ONNX Runtime's WASM backend can use threads.
// The deployed site sets the same headers in CloudFront.
const isolation = {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
};

const ORT_DIR = resolve(import.meta.dirname, "node_modules/onnxruntime-web/dist");
const MODELS_DIR = resolve(import.meta.dirname, "../models/files");

const TYPES: Record<string, string> = {
    ".json": "application/json",
    ".mjs": "text/javascript",
    ".wasm": "application/wasm",
};

// In development, /models/ and /ort/ are served from models/files (fetched by
// models/fetch.sh) and from the installed ONNX Runtime. A production build
// points at the versioned paths scripts/deploy.sh uploads instead.
function serve(prefix: string, dir: string): Plugin["configureServer"] {
    return (server) => {
        server.middlewares.use(prefix, (req, res, next) => {
            const path = decodeURIComponent((req.url ?? "").split("?")[0] ?? "");
            const file = resolve(dir, `.${path}`);
            if (!file.startsWith(`${dir}/`) || !existsSync(file)) return next();
            const ext = file.slice(file.lastIndexOf("."));
            res.setHeader("Content-Type", TYPES[ext] ?? "application/octet-stream");
            for (const [k, v] of Object.entries(isolation)) res.setHeader(k, v);
            res.end(readFileSync(file));
        });
    };
}

export default defineConfig({
    plugins: [
        { name: "serve-models", configureServer: serve("/models", MODELS_DIR) },
        { name: "serve-ort", configureServer: serve("/ort", ORT_DIR) },
    ],
    server: { headers: isolation },
    preview: { headers: isolation },
    worker: { format: "es" },
    optimizeDeps: { exclude: ["@huggingface/transformers"] },
});
