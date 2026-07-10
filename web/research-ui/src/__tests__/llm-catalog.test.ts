import { describe, expect, it } from 'vitest';
import { estimateLlmUsageCostCny, pricingForModel } from '../components/llmCatalog';

describe('llmCatalog pricing', () => {
  it('calculates DeepSeek usage cost from cache hit, cache miss, and output tokens', () => {
    const pricing = pricingForModel('deepseek', 'deepseek-chat');

    expect(pricing).not.toBeNull();
    expect(
      estimateLlmUsageCostCny(pricing!, {
        cacheHitInputTokens: 1_000_000,
        cacheMissInputTokens: 1_000_000,
        outputTokens: 1_000_000,
      }),
    ).toBeCloseTo(3.02);
  });

  it('uses DeepSeek V4 Pro RMB pricing when that model is selected', () => {
    const pricing = pricingForModel('deepseek', 'deepseek-v4-pro');

    expect(pricing).not.toBeNull();
    expect(
      estimateLlmUsageCostCny(pricing!, {
        cacheHitInputTokens: 1_000_000,
        cacheMissInputTokens: 1_000_000,
        outputTokens: 1_000_000,
      }),
    ).toBeCloseTo(9.025);
  });
});
