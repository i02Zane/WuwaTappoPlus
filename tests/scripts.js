const fs = require('node:fs');
const path = require('node:path');

// Use the same version source as Python without requiring Python for Node tests.
const source = fs.readFileSync(path.join(__dirname, '../src/wuwa_tappo_plus/__init__.py'), 'utf8');
const version = source.match(/^__version__ = "([^"]+)"/m)[1];
function loadScript(name) {
  return fs.readFileSync(path.join(__dirname, '../src/wuwa_tappo_plus/scripts', name), 'utf8')
    .replace('__VERSION__', JSON.stringify(version));
}

module.exports = { version, loadScript };
