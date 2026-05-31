import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

function read(file: string) {
  return fs.readFileSync(path.resolve(process.cwd(), file), 'utf8');
}

describe('settings-css-controls', () => {
  it('defines unified field controls with focus and error styles', () => {
    const css = read('src/styles.css');

    expect(css).toMatch(/\.ct-field input,\s*\.ct-field select\s*{[^}]*height:\s*var\(--ct-control-h\);/s);
    expect(css).toContain('.ct-field input:focus');
    expect(css).toContain('box-shadow: var(--ct-focus-ring);');
    expect(css).toContain('.ct-field.is-error input');
    expect(css).toContain('.ct-field-error');
  });

  it('defines button system, status pills and inline alerts', () => {
    const css = read('src/styles.css');

    expect(css).toContain('.ct-button');
    expect(css).toContain('.ct-button-secondary');
    expect(css).toContain('.ct-button-row');
    expect(css).toContain('.ct-status-pill');
    expect(css).toContain('.ct-status-ready');
    expect(css).toContain('.ct-status-pending');
    expect(css).toContain('.ct-status-error');
    expect(css).toContain('.ct-inline-alert');
    expect(css).toContain('.ct-inline-alert.is-error');
    expect(css).toContain('.ct-inline-alert.is-success');
  });

  it('keeps primary buttons scoped to test/save/confirm flows', () => {
    const settingsSections = read('src/components/SettingsSections.tsx');
    const homePage = read('src/routes/HomePage.tsx');
    const messageStream = read('src/components/MessageStream.tsx');

    expect(settingsSections).toMatch(/className="ct-button"[\s\S]*?测试报告模型连接/s);
    expect(settingsSections).toMatch(/className="ct-button"[\s\S]*?测试 Embedding 连接/s);
    expect(settingsSections).toMatch(/className="ct-button"[\s\S]*?测试数据源/s);
    expect(homePage).toMatch(/className="ct-button-link"[\s\S]*?去设置模型/s);
    expect(messageStream).toMatch(/className="ct-button"[\s\S]*?\{isBusy \? '处理中' : '确认'\}/s);

    expect(settingsSections).toMatch(/className="ct-button ct-button-secondary"[\s\S]*?保存报告模型配置/s);
    expect(settingsSections).toMatch(/className="ct-button ct-button-secondary"[\s\S]*?保存 Embedding 配置/s);
    expect(settingsSections).toMatch(/className="ct-button ct-button-secondary"[\s\S]*?重新连接/s);
    expect(settingsSections).toMatch(/className="ct-button ct-button-secondary"[\s\S]*?刷新二维码/s);
    expect(settingsSections).toMatch(/className="ct-button ct-button-secondary"[\s\S]*?稍后设置/s);
    expect(messageStream).toMatch(/className="ct-button ct-button-secondary"[\s\S]*?更新标的/s);
    expect(messageStream).toMatch(/className="ct-button ct-button-secondary"[\s\S]*?取消/s);
  });

  it('uses inline alerts for settings feedback and a compact model warning on home', () => {
    const settingsSections = read('src/components/SettingsSections.tsx');
    const homePage = read('src/routes/HomePage.tsx');
    const messageStream = read('src/components/MessageStream.tsx');

    expect(settingsSections).toContain('ct-inline-alert');
    expect(homePage).toContain('ct-model-warning');
    expect(messageStream).toContain('ct-inline-alert is-error');
    expect(settingsSections).not.toContain('ct-banner');
    expect(homePage).not.toContain('ct-banner');
  });
});
