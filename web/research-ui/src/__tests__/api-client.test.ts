import { afterEach, describe, expect, it } from 'vitest';
import { checkForUpdate, getChannelStatus, installUpdate, sendChatMessage } from '../api/client';

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

  it('can request lightweight channel status without qr probe on startup', async () => {
    const urls: string[] = [];
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      urls.push(String(input));
      return new Response(
        JSON.stringify({
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: 'disconnected',
          displayName: '微信 ClawBot',
          canSendText: false,
          canSendFile: false,
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      );
    }) as typeof fetch;

    await getChannelStatus({ probe: false });

    expect(urls).toEqual(['/api/ui/get-channel-status']);
  });

  it('posts remote update check requests to the production maintenance endpoint', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), init });
      return new Response(
        JSON.stringify({
          status: 'not_configured',
          currentVersion: '1.2.2',
          latestVersion: null,
          archive: null,
          userMessage: '远程更新源未配置。',
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      );
    }) as typeof fetch;

    await checkForUpdate({ requestId: 'check-1' });

    expect(requests[0]?.url).toBe('/api/ui/check-for-update');
    expect(requests[0]?.init?.method).toBe('POST');
    expect(JSON.parse(String(requests[0]?.init?.body))).toEqual({ requestId: 'check-1' });
  });

  it('posts signed update install requests to the production maintenance endpoint', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), init });
      return new Response(
        JSON.stringify({
          status: 'not_configured',
          version: null,
          userMessage: '远程更新源未配置。',
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      );
    }) as typeof fetch;

    await installUpdate({ requestId: 'install-1' });

    expect(requests[0]?.url).toBe('/api/ui/install-update');
    expect(requests[0]?.init?.method).toBe('POST');
    expect(JSON.parse(String(requests[0]?.init?.body))).toEqual({ requestId: 'install-1' });
  });
});
