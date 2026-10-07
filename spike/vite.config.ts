import { cpSync, existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import basicSsl from "@vitejs/plugin-basic-ssl";
import { defineConfig, type Plugin } from "vite";

// Cross-origin isolation, so ONNX Runtime's WASM backend can use threads.
const isolation = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "require-corp",
};

// ONNX Runtime's WASM files, served from this origin rather than the CDN that
// Transformers.js uses by default. In development they go through a
// middleware ahead of Vite's own, because Vite rewrites the runtime's dynamic
// import of a /public module and returns an HTML error page instead.
const ORT_DIR = "node_modules/onnxruntime-web/dist";
const MODELS_DIR = resolve(__dirname, "../models/files");
const ORT_FILES = [
  "ort-wasm-simd-threaded.asyncify.mjs",
  "ort-wasm-simd-threaded.asyncify.wasm",
  "ort-wasm-simd-threaded.mjs",
  "ort-wasm-simd-threaded.wasm",
];

function selfHostOrt(): Plugin {
  return {
    name: "self-host-ort",
    configureServer(server) {
      server.middlewares.use("/ort", (req, res, next) => {
        const name = (req.url ?? "").split("?")[0].replace(/^\//, "");
        if (!ORT_FILES.includes(name)) return next();
        res.setHeader("Content-Type", name.endsWith(".wasm") ? "application/wasm" : "text/javascript");
        for (const [k, v] of Object.entries(isolation)) res.setHeader(k, v);
        res.end(readFileSync(`${ORT_DIR}/${name}`));
      });
      // Model files live in models/files at the repository root, fetched by
      // models/fetch.sh and shared with the app.
      server.middlewares.use("/models", (req, res, next) => {
        const path = decodeURIComponent((req.url ?? "").split("?")[0]);
        const file = resolve(MODELS_DIR, `.${path}`);
        if (!file.startsWith(`${MODELS_DIR}/`) || !existsSync(file)) return next();
        res.setHeader("Content-Type", file.endsWith(".json") ? "application/json" : "application/octet-stream");
        for (const [k, v] of Object.entries(isolation)) res.setHeader(k, v);
        res.end(readFileSync(file));
      });
    },
    // A build copies only the fixtures from public/, not the 1.3 GB of model
    // files beside them. The deployed site serves models and the runtime from
    // their own versioned paths; see scripts/deploy.sh.
    writeBundle(options) {
      cpSync("public/fixtures", `${options.dir ?? "dist"}/fixtures`, { recursive: true });
    },
  };
}

// `npm run dev:lan` serves over HTTPS with a self-signed certificate, so a
// phone on the same network gets a secure context: WebGPU and cross-origin
// isolation are both unavailable over plain HTTP anywhere but localhost.
export default defineConfig(({ mode }) => ({
  // SPIKE_BASE=/spike/ when building for the deployed site.
  base: process.env.SPIKE_BASE ?? "/",
  build: { copyPublicDir: false },
  plugins: [selfHostOrt(), ...(mode === "lan" ? [basicSsl()] : [])],
  server: { headers: isolation },
  preview: { headers: isolation },
  worker: { format: "es" },
  optimizeDeps: { exclude: ["@huggingface/transformers"] },
}));
