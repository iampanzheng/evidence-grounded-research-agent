import '@testing-library/jest-dom/vitest';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import App from '../src/App';

// Public synthetic UI smoke uses a fake network boundary; the real HTTP path
// is separately covered by the original project's private test suite.
vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
  new Response(JSON.stringify({ status: 'ready' }), { status: 200 }),
));
afterEach(() => cleanup());
it('advertises fictional Orion instead of the private corpus', async () => {
  render(<App/>);
  expect(await screen.findByText('本地服务就绪')).toBeInTheDocument();
  expect(screen.getByText('What checks gate a fictional Orion release?')).toBeInTheDocument();
  expect(screen.getByText(/当前公开演示使用原创的虚构 Orion 文档/)).toBeInTheDocument();
  expect(screen.queryByText(/基于已冻结的真实文档/)).not.toBeInTheDocument();
});
