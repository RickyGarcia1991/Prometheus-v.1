'use strict';
// Node's permission model bounds files/processes; this also denies networking.
const deny = () => { throw new Error('Regression isolation: real networking is disabled'); };
for (const name of ['node:http', 'node:https']) {
  const module = require(name);
  module.request = deny;
  module.get = deny;
  module.createServer = deny;
}
for (const name of ['node:net', 'node:tls', 'node:dgram']) {
  const module = require(name);
  for (const method of ['connect', 'createConnection', 'createServer', 'createSocket']) {
    if (method in module) module[method] = deny;
  }
}
require('node:net').Socket.prototype.connect = deny;
globalThis.fetch = deny;
globalThis.WebSocket = class { constructor() { deny(); } };
