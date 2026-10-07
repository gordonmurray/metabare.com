import { cpSync, mkdirSync, readFileSync } from "node:fs";
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
    },
    writeBundle(options) {
      const out = `${options.dir ?? "dist"}/ort`;
      mkdirSync(out, { recursive: true });
      for (const name of ORT_FILES) cpSync(`${ORT_DIR}/${name}`, `${out}/${name}`);
    },
  };
}

// `npm run dev:lan` serves over HTTPS with a self-signed certificate, so a
// phone on the same network gets a secure context: WebGPU and cross-origin
// isolation are both unavailable over plain HTTP anywhere but localhost.
export default defineConfig(({ mode }) => ({
  plugins: [selfHostOrt(), ...(mode === "lan" ? [basicSsl()] : [])],
  server: { headers: isolation },
  preview: { headers: isolation },
  worker: { format: "es" },
  optimizeDeps: { exclude: ["@huggingface/transformers"] },
}));
