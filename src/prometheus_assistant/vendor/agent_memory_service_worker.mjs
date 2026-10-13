// Prometheus approved full-service wrapper. Upstream payload stays intact.
// This is application-level containment, not an operating-system sandbox.
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { pathToFileURL } from 'node:url';
import { syncBuiltinESMExports } from 'node:module';

const root = process.env.PROMETHEUS_AGENTMEMORY_BUNDLE;
const stopFile = process.env.PROMETHEUS_AGENTMEMORY_STOP;
const secret = process.env.AGENTMEMORY_SECRET;
if (!root || !stopFile || !secret || secret.length < 32) throw Error('Missing qualification envelope');

// The integration has its own UI. Do not open upstream's browser viewer.
const listen = http.Server.prototype.listen;
http.Server.prototype.listen = function (...args) {
  const port = typeof args[0] === 'object' ? args[0]?.port : args[0];
  if (Number(port) === Number(process.env.III_VIEWER_PORT)) return this;
  throw Error('Unexpected Node HTTP listener refused');
};
syncBuiltinESMExports();

const fetch0 = globalThis.fetch;
const permittedPorts = new Set([process.env.III_REST_PORT, process.env.III_STREAM_PORT,
  process.env.III_VIEWER_PORT, process.env.III_ENGINE_PORT]);
globalThis.fetch = (...args) => {
  const url = new URL(typeof args[0] === 'string' || args[0] instanceof URL ? args[0] : args[0].url);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !permittedPorts.has(url.port)) {
    throw Error('External fetch is disabled in local qualification');
  }
  return fetch0(...args);
};

const transformers = await import(pathToFileURL(path.join(root,
  'node_modules/@huggingface/transformers/dist/transformers.node.mjs')));
transformers.env.allowRemoteModels = false;
transformers.env.allowLocalModels = true;
transformers.env.localModelPath = path.join(root, 'models') + path.sep;
transformers.env.useFSCache = false;
transformers.env.cacheDir = path.join(process.env.HOME, 'model-cache');
if (transformers.env.backends?.onnx?.wasm) transformers.env.backends.onnx.wasm.numThreads = 2;

await import(pathToFileURL(path.join(root, 'node_modules/@agentmemory/agentmemory/dist/index.mjs')));
let stopping = false;
setInterval(() => {
  if (!stopping && fs.existsSync(stopFile) && process.listenerCount('SIGTERM')) {
    stopping = true;
    process.emit('SIGTERM');
  }
}, 100).unref();
