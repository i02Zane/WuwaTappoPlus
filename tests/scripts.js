const fs = require('node:fs');
const path = require('node:path');

// Use the same version source as Python without requiring Python for Node tests.
const source = fs.readFileSync(path.join(__dirname, '../src/wuwa_tappo_plus/__init__.py'), 'utf8');
const version = source.match(/^__version__ = "([^"]+)"/m)[1];
function loadScript(name) {
  const directory = path.join(__dirname, '../src/wuwa_tappo_plus/scripts');
  const manifest = JSON.parse(fs.readFileSync(path.join(directory, 'manifest.json'), 'utf8'));
  let source = fs.readFileSync(path.join(directory, name), 'utf8');
  if (manifest[name]) {
    assertMarker(source, name);
    source = source.replace('/* @modules */', () => manifest[name].map(part =>
      fs.readFileSync(path.join(directory, part), 'utf8')).join('\n'));
  }
  return source.replace('__VERSION__', JSON.stringify(version));
}

function assertMarker(source, name) {
  if (source.split('/* @modules */').length !== 2) {
    throw new Error('script must have one module marker: ' + name);
  }
}

module.exports = { version, loadScript };
