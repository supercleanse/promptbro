"use strict";
// Loads ../../Model.js into a fresh vm context so its top-level `function`/
// `var` declarations (QML .pragma library style, no module.exports) become
// readable properties, without needing an actual QML engine. Model.js has no
// QML types in it -- just plain JS helpers -- so this is the only translation
// needed: strip the QML-only `.pragma library` directive Node can't parse.

const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

function loadModelJs() {
  const file = path.join(__dirname, "..", "..", "Model.js");
  const src = fs.readFileSync(file, "utf8").replace(/^\.pragma\s+library\s*$/m, "");
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: file });
  return sandbox;
}

module.exports = { loadModelJs };
