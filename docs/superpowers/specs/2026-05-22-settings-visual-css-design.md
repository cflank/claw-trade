# 设置模块视觉与 CSS 级设计

日期：2026-05-22

适用目标：

- `web/research-ui/src/styles.css`
- 设置启动引导
- 设置页
- 报告任务确认卡片

本文是 CSS 文件级设计，不是方向描述。实现时应尽量复用现有 `ct-*` 命名体系。

## 1. 视觉目标

设置模块要像专业投研工具，不像营销页，也不像复杂后台。

整体观感：

- 浅色背景
- 白色工作面板
- 细边框
- 小状态标签
- 低饱和按钮
- 无大渐变
- 无 hero
- 无装饰图形
- 无嵌套卡片

## 2. Design Tokens

在现有 `:root` 上扩展，不替换全部变量。

```css
:root {
  --ct-bg: #f5f7fb;
  --ct-surface: #ffffff;
  --ct-surface-muted: #f8fafc;
  --ct-surface-subtle: #fbfcfe;
  --ct-border: #d8e0eb;
  --ct-border-strong: #b9c5d4;

  --ct-text: #1f2d3d;
  --ct-text-muted: #627286;
  --ct-text-soft: #8793a3;

  --ct-primary: #2f5f73;
  --ct-primary-hover: #264f60;
  --ct-primary-soft: #e9f3f5;

  --ct-success: #23734c;
  --ct-success-bg: #eef8f2;
  --ct-success-border: #b8dcc7;

  --ct-warning: #9a6519;
  --ct-warning-bg: #fff7e8;
  --ct-warning-border: #efd49d;

  --ct-danger: #b13b3b;
  --ct-danger-bg: #fff1f1;
  --ct-danger-border: #efb5b5;

  --ct-radius-sm: 6px;
  --ct-radius-md: 8px;
  --ct-radius-pill: 999px;

  --ct-space-1: 4px;
  --ct-space-2: 8px;
  --ct-space-3: 12px;
  --ct-space-4: 16px;
  --ct-space-5: 20px;
  --ct-space-6: 24px;

  --ct-control-h: 40px;
  --ct-control-h-sm: 32px;
  --ct-qr-size: 180px;

  --ct-shadow-panel: 0 1px 2px rgba(17, 24, 39, 0.04);
  --ct-focus-ring: 0 0 0 3px rgba(47, 95, 115, 0.16);
}
```

## 3. Page Shell

保留顶部栏，但设置相关页面不要使用三栏工作台布局。

```css
.ct-settings-wrap,
.ct-onboarding-wrap {
  max-width: 1120px;
  margin: 0 auto;
  padding: 24px 20px 40px;
}

.ct-page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.ct-page-title {
  margin: 0;
  font-size: 22px;
  line-height: 30px;
  font-weight: 700;
  color: var(--ct-text);
}

.ct-page-subtitle {
  margin: 4px 0 0;
  max-width: 680px;
  font-size: 13px;
  line-height: 20px;
  color: var(--ct-text-muted);
}
```

## 4. 启动引导布局

启动引导是两列：左侧报告模型，右侧微信通知。

```css
.ct-onboarding {
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(360px, 0.92fr);
  gap: 16px;
  align-items: start;
}

.ct-onboarding-panel {
  min-width: 0;
  background: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-md);
  box-shadow: var(--ct-shadow-panel);
  padding: 20px;
}

.ct-onboarding-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.ct-onboarding-panel-title {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 700;
}
```

报告模型未就绪时不显示跳转按钮，直接显示表单。

## 5. 表单控件

统一输入框高度和状态。

```css
.ct-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.ct-field > span,
.ct-field-label {
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
  color: var(--ct-text-muted);
}

.ct-field input,
.ct-field select {
  width: 100%;
  height: var(--ct-control-h);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-sm);
  background: #fff;
  color: var(--ct-text);
  padding: 0 11px;
  font: inherit;
  font-size: 14px;
  line-height: 20px;
}

.ct-field input::placeholder {
  color: var(--ct-text-soft);
}

.ct-field input:focus,
.ct-field select:focus {
  outline: none;
  border-color: var(--ct-primary);
  box-shadow: var(--ct-focus-ring);
}

.ct-field.is-error input,
.ct-field.is-error select {
  border-color: var(--ct-danger);
}

.ct-field-error {
  font-size: 12px;
  line-height: 18px;
  color: var(--ct-danger);
}
```

## 6. Buttons

主按钮只用于当前主动作：测试并保存、生成报告。

```css
.ct-button {
  min-height: var(--ct-control-h);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border: 1px solid var(--ct-primary);
  border-radius: var(--ct-radius-sm);
  background: var(--ct-primary);
  color: #fff;
  padding: 0 14px;
  font: inherit;
  font-size: 14px;
  line-height: 20px;
  font-weight: 600;
  cursor: pointer;
}

.ct-button:hover {
  background: var(--ct-primary-hover);
  border-color: var(--ct-primary-hover);
}

.ct-button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.ct-button-secondary {
  border-color: var(--ct-border);
  background: #fff;
  color: var(--ct-primary);
}

.ct-button-secondary:hover {
  border-color: var(--ct-border-strong);
  background: var(--ct-surface-muted);
}

.ct-button-row {
  margin-top: 16px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
```

## 7. Status Pills

状态不使用大面积色块，只用小标签。

```css
.ct-status-pill {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-pill);
  background: var(--ct-surface-muted);
  color: var(--ct-text-muted);
  padding: 2px 9px;
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
}

.ct-status-ready {
  border-color: var(--ct-success-border);
  background: var(--ct-success-bg);
  color: var(--ct-success);
}

.ct-status-pending {
  border-color: var(--ct-warning-border);
  background: var(--ct-warning-bg);
  color: var(--ct-warning);
}

.ct-status-error {
  border-color: var(--ct-danger-border);
  background: var(--ct-danger-bg);
  color: var(--ct-danger);
}
```

## 8. 微信二维码区

二维码是视觉中心，但不是页面主任务。

```css
.ct-wechat-onboarding {
  display: grid;
  grid-template-columns: var(--ct-qr-size) minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}

.ct-qr-frame {
  width: var(--ct-qr-size);
  height: var(--ct-qr-size);
  display: grid;
  place-items: center;
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-md);
  background: #fff;
  color: var(--ct-text-muted);
  font-size: 13px;
  line-height: 20px;
  text-align: center;
}

.ct-qr-frame img {
  width: 164px;
  height: 164px;
  object-fit: contain;
}

.ct-wechat-copy {
  min-width: 0;
}

.ct-wechat-copy p {
  margin: 0;
  font-size: 13px;
  line-height: 21px;
  color: var(--ct-text-muted);
}

.ct-wechat-copy p + p {
  margin-top: 8px;
}
```

微信文案固定：

```text
请先在微信端启用插件：
我 → 设置 → 插件 → 微信 ClawBot

按微信端提示安装或启用后，返回本页扫描二维码完成连接。
```

## 9. 设置页三块结构

设置页使用三块主面板，不做 tab。

```css
.ct-settings {
  display: grid;
  gap: 14px;
}

.ct-settings-section {
  background: var(--ct-surface);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-md);
  box-shadow: var(--ct-shadow-panel);
  padding: 18px;
}

.ct-settings-section h2 {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 700;
}

.ct-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.ct-section-desc {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--ct-text-muted);
}
```

三块顺序：

1. 模型
2. 增强数据源
3. 微信通知

## 10. 模型区块布局

报告模型在上，Embedding 在下。Embedding 使用弱边框分隔，不做嵌套卡片。

```css
.ct-model-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.ct-model-subsection {
  padding-top: 16px;
  margin-top: 18px;
  border-top: 1px solid var(--ct-border);
}

.ct-model-subsection-title {
  margin: 0 0 10px;
  font-size: 14px;
  line-height: 22px;
  font-weight: 700;
}
```

## 11. 增强数据源列表

增强源是列表行，不做大卡片堆叠。

```css
.ct-source-list {
  display: grid;
  gap: 10px;
}

.ct-source-row {
  display: grid;
  grid-template-columns: minmax(180px, 0.8fr) minmax(0, 1.4fr) auto;
  gap: 12px;
  align-items: center;
  min-height: 56px;
  padding: 10px 0;
  border-top: 1px solid var(--ct-border);
}

.ct-source-row:first-child {
  border-top: 0;
}

.ct-source-name {
  min-width: 0;
  font-size: 14px;
  line-height: 20px;
  font-weight: 700;
}

.ct-source-meta {
  margin-top: 2px;
  font-size: 12px;
  line-height: 18px;
  color: var(--ct-text-muted);
}

.ct-source-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
```

## 12. 任务确认卡片

任务卡片要短，不能像表单页。

```css
.ct-task-confirm {
  width: min(100%, 520px);
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-md);
  background: var(--ct-surface);
  box-shadow: var(--ct-shadow-panel);
  padding: 16px;
}

.ct-task-confirm-title {
  margin: 0 0 12px;
  font-size: 15px;
  line-height: 22px;
  font-weight: 700;
}

.ct-task-fields {
  display: grid;
  gap: 8px;
}

.ct-task-field {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  font-size: 14px;
  line-height: 22px;
}

.ct-task-field-label {
  color: var(--ct-text-muted);
}

.ct-task-field-value {
  min-width: 0;
  color: var(--ct-text);
  overflow-wrap: anywhere;
}

.ct-task-market-select {
  width: 180px;
  height: 34px;
  border: 1px solid var(--ct-border);
  border-radius: var(--ct-radius-sm);
  background: #fff;
  color: var(--ct-text);
  padding: 0 9px;
  font: inherit;
  font-size: 13px;
}

.ct-task-confirm-actions {
  margin-top: 14px;
  display: flex;
  gap: 8px;
}
```

卡片字段：

```text
标的
名称
市场
```

不显示：

```text
报告方案
计价单位
profile
默认币种
```

## 13. Error And Empty States

错误提示只说用户能做什么。

```css
.ct-inline-alert {
  margin-top: 10px;
  border: 1px solid var(--ct-warning-border);
  border-radius: var(--ct-radius-sm);
  background: var(--ct-warning-bg);
  color: var(--ct-warning);
  padding: 9px 11px;
  font-size: 13px;
  line-height: 20px;
}

.ct-inline-alert.is-error {
  border-color: var(--ct-danger-border);
  background: var(--ct-danger-bg);
  color: var(--ct-danger);
}

.ct-inline-alert.is-success {
  border-color: var(--ct-success-border);
  background: var(--ct-success-bg);
  color: var(--ct-success);
}
```

## 14. Responsive Rules

桌面优先，移动端只保证能用，不做移动端专用体验。

```css
@media (max-width: 960px) {
  .ct-onboarding {
    grid-template-columns: minmax(0, 1fr);
  }

  .ct-wechat-onboarding {
    grid-template-columns: minmax(0, 1fr);
  }

  .ct-qr-frame {
    width: 168px;
    height: 168px;
  }

  .ct-qr-frame img {
    width: 152px;
    height: 152px;
  }

  .ct-model-grid,
  .ct-form-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .ct-source-row {
    grid-template-columns: minmax(0, 1fr);
  }

  .ct-source-actions {
    justify-content: flex-start;
  }
}

@media (max-width: 560px) {
  .ct-settings-wrap,
  .ct-onboarding-wrap {
    padding: 14px 10px 28px;
  }

  .ct-onboarding-panel,
  .ct-settings-section,
  .ct-task-confirm {
    padding: 14px;
  }

  .ct-page-title {
    font-size: 20px;
    line-height: 28px;
  }

  .ct-button-row,
  .ct-task-confirm-actions {
    flex-direction: column;
  }

  .ct-button,
  .ct-button-secondary {
    width: 100%;
  }

  .ct-task-field {
    grid-template-columns: 64px minmax(0, 1fr);
  }
}
```

## 15. Motion

只允许微动效。

```css
@media (prefers-reduced-motion: no-preference) {
  .ct-button,
  .ct-button-secondary,
  .ct-field input,
  .ct-field select,
  .ct-status-pill {
    transition:
      background-color 120ms ease,
      border-color 120ms ease,
      color 120ms ease,
      box-shadow 120ms ease;
  }
}
```

不做页面级动画。

## 16. Implementation Notes

实现时建议：

1. 先补 tokens。
2. 再补启动引导布局类。
3. 再收敛设置页现有 `.ct-settings-section`、`.ct-form-grid`、`.ct-wechat-channel`。
4. 最后补任务卡片类。

不要把本设计一次性变成大规模视觉重构；先服务设置模块。

