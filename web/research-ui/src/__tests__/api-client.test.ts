import { afterEach, describe, expect, it } from 'vitest';
import { sendChatMessage } from '../api/client';

describe('api client error translation', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('translates html error response into readable chinese message', async () => {
    globalThis.fetch = (async () => {
      return new Response('<html><body>internal gateway error</body></html>', {
        status: 502,
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
      });
    }) as typeof fetch;

    await expect(
      sendChatMessage({
        requestId: 'req-1',
        contextId: 'normal-chat',
        text: '你好',
      }),
    ).rejects.toThrow('服务暂时不可用，请稍后重试。');
  });

  it('handles non-json success payload without exposing parse error', async () => {
    globalThis.fetch = (async () => {
      return new Response('<html>ok</html>', {
        status: 200,
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
      });
    }) as typeof fetch;

    await expect(
      sendChatMessage({
        requestId: 'req-2',
        contextId: 'normal-chat',
        text: '测试',
      }),
    ).rejects.toThrow('服务返回格式异常，请稍后重试。');
  });

  it('handles malformed json payload without exposing Unexpected token', async () => {
    globalThis.fetch = (async () => {
      return new Response('<!doctype html><html>bad gateway</html>', {
        status: 502,
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
      });
    }) as typeof fetch;

    await expect(
      sendChatMessage({
        requestId: 'req-3',
        contextId: 'normal-chat',
        text: '测试',
      }),
    ).rejects.toThrow('服务暂时不可用，请稍后重试。');
  });
});
