import type { LlmConfigDraft } from '../api/contracts';

type LlmProvider = LlmConfigDraft['provider'];

export type LlmModelOption = {
  value: string;
  label: string;
  pricing?: LlmModelPricing;
};

export type LlmProviderPreset = {
  value: LlmProvider;
  label: string;
  endpointUrl: string;
  defaultModel: string;
  models: LlmModelOption[];
};

export type LlmModelPricing = {
  currency: 'CNY';
  perMillionCacheHitInput: number;
  perMillionCacheMissInput: number;
  perMillionOutput: number;
  sourceLabel: string;
  sourceCheckedAt: string;
};

export type LlmUsageTokens = {
  cacheHitInputTokens: number;
  cacheMissInputTokens: number;
  outputTokens: number;
};

const DEEPSEEK_V4_FLASH_PRICING: LlmModelPricing = {
  currency: 'CNY',
  perMillionCacheHitInput: 0.02,
  perMillionCacheMissInput: 1,
  perMillionOutput: 2,
  sourceLabel: 'DeepSeek 中文官方价格页',
  sourceCheckedAt: '2026-07-10',
};

const DEEPSEEK_V4_PRO_PRICING: LlmModelPricing = {
  currency: 'CNY',
  perMillionCacheHitInput: 0.025,
  perMillionCacheMissInput: 3,
  perMillionOutput: 6,
  sourceLabel: 'DeepSeek 中文官方价格页',
  sourceCheckedAt: '2026-07-10',
};

const PRESETS: LlmProviderPreset[] = [
  {
    value: 'deepseek',
    label: 'DeepSeek',
    endpointUrl: 'https://api.deepseek.com',
    defaultModel: 'deepseek/deepseek-chat',
    models: [
      { value: 'deepseek/deepseek-v4-flash', label: 'DeepSeek V4 Flash', pricing: DEEPSEEK_V4_FLASH_PRICING },
      { value: 'deepseek/deepseek-v4-pro', label: 'DeepSeek V4 Pro', pricing: DEEPSEEK_V4_PRO_PRICING },
      { value: 'deepseek/deepseek-chat', label: 'DeepSeek Chat', pricing: DEEPSEEK_V4_FLASH_PRICING },
      { value: 'deepseek/deepseek-reasoner', label: 'DeepSeek Reasoner', pricing: DEEPSEEK_V4_FLASH_PRICING },
    ],
  },
  {
    value: 'qwen',
    label: '通义千问',
    endpointUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    defaultModel: 'qwen/qwen-plus',
    models: [
      { value: 'qwen/qwen-plus', label: 'Qwen Plus' },
      { value: 'qwen/qwen3.6-plus', label: 'Qwen 3.6 Plus' },
      { value: 'qwen/qwen3.7-max', label: 'Qwen 3.7 Max' },
    ],
  },
  {
    value: 'glm',
    label: '智谱 GLM',
    endpointUrl: 'https://open.bigmodel.cn/api/paas/v4',
    defaultModel: 'glm/glm-4.5',
    models: [
      { value: 'glm/glm-4.5', label: 'GLM-4.5' },
      { value: 'glm/glm-4.5-air', label: 'GLM-4.5 Air' },
    ],
  },
  {
    value: 'kimi',
    label: 'Kimi',
    endpointUrl: 'https://api.moonshot.cn/v1',
    defaultModel: 'kimi/kimi-k2.5',
    models: [
      { value: 'kimi/kimi-k2.5', label: 'Kimi K2.5' },
      { value: 'kimi/kimi-k2-turbo-preview', label: 'Kimi K2 Turbo Preview' },
    ],
  },
  {
    value: 'minimax',
    label: 'MiniMax',
    endpointUrl: 'https://api.minimax.io/v1',
    defaultModel: 'minimax/MiniMax-M2',
    models: [
      { value: 'minimax/MiniMax-M2', label: 'MiniMax M2' },
      { value: 'minimax/MiniMax-M2.5', label: 'MiniMax M2.5' },
    ],
  },
  {
    value: 'doubao',
    label: '豆包',
    endpointUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    defaultModel: 'doubao/doubao-seed-2-0-pro-260215',
    models: [
      { value: 'doubao/doubao-seed-2-0-pro-260215', label: 'Doubao Seed 2.0 Pro' },
      { value: 'doubao/doubao-seed-2-0-lite-260215', label: 'Doubao Seed 2.0 Lite' },
    ],
  },
  {
    value: 'ernie',
    label: '文心 ERNIE',
    endpointUrl: 'https://qianfan.baidubce.com/v2',
    defaultModel: 'ernie/ernie-4.5-turbo',
    models: [
      { value: 'ernie/ernie-4.5-turbo', label: 'ERNIE 4.5 Turbo' },
      { value: 'ernie/ernie-x1-turbo', label: 'ERNIE X1 Turbo' },
    ],
  },
  {
    value: 'hunyuan',
    label: '腾讯混元',
    endpointUrl: 'https://api.hunyuan.cloud.tencent.com/v1',
    defaultModel: 'hunyuan/hunyuan-turbos-latest',
    models: [
      { value: 'hunyuan/hunyuan-turbos-latest', label: 'Hunyuan TurboS latest' },
      { value: 'hunyuan/hunyuan-large-latest', label: 'Hunyuan Large latest' },
    ],
  },
  {
    value: 'openai_compatible',
    label: '兼容接口',
    endpointUrl: 'https://api.example.com/v1',
    defaultModel: 'openai_compatible/custom-model',
    models: [{ value: 'openai_compatible/custom-model', label: '自定义兼容模型' }],
  },
];

export const LLM_PROVIDER_PRESETS = PRESETS;

export function getLlmProviderPreset(provider: LlmProvider): LlmProviderPreset {
  return PRESETS.find((item) => item.value === provider) ?? PRESETS[0];
}

export function withLlmProviderDefaults(draft: LlmConfigDraft): LlmConfigDraft {
  const preset = getLlmProviderPreset(draft.provider);
  return {
    ...draft,
    endpointUrl: draft.endpointUrl?.trim() ? draft.endpointUrl : preset.endpointUrl,
    defaultModel: draft.defaultModel.trim() ? normalizeLlmModelValue(draft.provider, draft.defaultModel) : preset.defaultModel,
  };
}

export function presetPatchForProvider(provider: LlmProvider): Partial<LlmConfigDraft> {
  const preset = getLlmProviderPreset(provider);
  return {
    provider,
    endpointUrl: preset.endpointUrl,
    defaultModel: preset.defaultModel,
    apiKeyReplacement: '',
    apiKeyMasked: null,
  };
}

export function modelOptionsForProvider(provider: LlmProvider, selectedModel: string): LlmModelOption[] {
  const preset = getLlmProviderPreset(provider);
  const normalized = selectedModel.trim() ? normalizeLlmModelValue(provider, selectedModel) : preset.defaultModel;
  if (preset.models.some((item) => item.value === normalized)) {
    return preset.models;
  }
  return [{ value: normalized, label: normalized }, ...preset.models];
}

export function pricingForModel(provider: LlmProvider, model: string): LlmModelPricing | null {
  const normalized = normalizeLlmModelValue(provider, model);
  return getLlmProviderPreset(provider).models.find((item) => item.value === normalized)?.pricing ?? null;
}

export function estimateLlmUsageCostCny(pricing: LlmModelPricing, usage: LlmUsageTokens) {
  return (
    (Math.max(0, usage.cacheHitInputTokens) * pricing.perMillionCacheHitInput +
      Math.max(0, usage.cacheMissInputTokens) * pricing.perMillionCacheMissInput +
      Math.max(0, usage.outputTokens) * pricing.perMillionOutput) /
    1_000_000
  );
}

export function normalizeLlmModelValue(provider: LlmProvider, model: string) {
  const trimmed = model.trim();
  if (!trimmed) {
    return getLlmProviderPreset(provider).defaultModel;
  }
  return trimmed.includes('/') ? trimmed : `${provider}/${trimmed}`;
}
