/** Verify the built Vite bundle can be served over loopback, without backend/Groq.
 * Only local static assets are fetched. No credentials or model requests.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const root = fileURLToPath(new URL('..', import.meta.url));
const port = await new Promise((resolve, reject) => {
  const server = createServer();
  server.once('error', reject);
  server.listen(0, '127.0.0.1', () => {
    const address = server.address();
    const assigned = typeof address === 'object' && address !== null ? address.port : 0;
    server.close(() => resolve(assigned));
  });
});
const child = spawn(process.execPath, [join(root, 'node_modules/vite/bin/vite.js'), 'preview',
  '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
  cwd: root, stdio: ['ignore', 'pipe', 'pipe'],
});
let serverOutput = '';
child.stdout.on('data', chunk => { serverOutput += chunk.toString().slice(0, 1000); });
child.stderr.on('data', chunk => { serverOutput += chunk.toString().slice(0, 1000); });
try {
  let homepage;
  for (let i = 0; i < 60; i++) {
    if (child.exitCode !== null) throw new Error(`VITE_PREVIEW_EXITED: ${serverOutput}`);
    try {
      const response = await fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(1000) });
      if (response.ok) { homepage = await response.text(); break; }
    } catch { /* boot in progress */ }
    await delay(100);
  }
  assert.ok(homepage, `VITE_PREVIEW_UNAVAILABLE: ${serverOutput}`);
  assert.match(homepage, /<html lang="zh-CN">/);
  const js = homepage.match(/src="(\/assets\/[^"]+\.js)"/);
  const css = homepage.match(/href="(\/assets\/[^"]+\.css)"/);
  assert.ok(js && css, 'missing built hashed JS or CSS assets');
  for (const [url, contentType] of [[js[1], 'javascript'], [css[1], 'css']]) {
    const response = await fetch(`http://127.0.0.1:${port}${url}`, {
      signal: AbortSignal.timeout(2000),
    });
    assert.equal(response.status, 200);
    assert.ok(response.headers.get('content-type')?.includes(contentType));
    const content = await response.text();
    assert.ok(content.length > 100);
    assert.ok(!content.includes('P2_LLM_API_KEY'), 'a backend-only secret identifier was bundled');
  }
  console.log('PASS: production HTML + hashed JS/CSS served on loopback; no API/Groq requests.');
} finally {
  child.kill('SIGTERM');
  await Promise.race([new Promise(resolve => child.once('exit', resolve)), delay(2000)]);
  if (child.exitCode === null) child.kill('SIGKILL');
}
