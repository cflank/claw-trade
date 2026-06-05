import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

function read(file: string) {
  return fs.readFileSync(path.resolve(process.cwd(), file), 'utf8');
}

describe('settings-css-onboarding', () => {
  it('extends ct tokens for settings and onboarding shell', () => {
    const css = read('src/styles.css');

    expect(css).toContain('--ct-bg: #f5f7fb;');
    expect(css).toContain('--ct-border: #d8e0eb;');
    expect(css).toContain('--ct-text: #1f2d3d;');
    expect(css).toContain('--ct-primary: #2f5f73;');
    expect(css).toContain('--ct-surface-subtle:');
    expect(css).toContain('--ct-border-strong:');
    expect(css).toContain('--ct-text-soft:');
    expect(css).toContain('--ct-primary-hover:');
    expect(css).toContain('--ct-primary-soft:');
    expect(css).toContain('--ct-warning:');
    expect(css).toContain('--ct-qr-size:');
    expect(css).toContain('--ct-shadow-panel:');
    expect(css).toContain('--ct-focus-ring:');
  });

  it('keeps onboarding as two-column desktop layout with page shell classes', () => {
    const css = read('src/styles.css');
    const homePage = read('src/routes/HomePage.tsx');
    const settingsPage = read('src/routes/SettingsPage.tsx');

    expect(css).toMatch(
      /\.ct-onboarding\s*{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(0,\s*1\.08fr\)\s*minmax\(360px,\s*(?:0?\.)?92fr\);/s,
    );
    expect(css).toMatch(/\.ct-onboarding-panel\s*{[^}]*border:\s*1px solid var\(--ct-border\);[^}]*box-shadow:\s*var\(--ct-shadow-panel\);/s);
    expect(css).toContain('.ct-page-head');
    expect(css).toContain('.ct-page-title');
    expect(css).toContain('.ct-page-subtitle');

    expect(homePage).toContain('data-testid="workspace-layout"');
    expect(homePage).not.toContain('className="ct-onboarding-wrap"');
    expect(homePage).not.toContain('className="ct-onboarding ct-onboarding-single"');
    expect(homePage).not.toContain('className="ct-onboarding-panel"');
    expect(settingsPage).toContain('className="ct-settings-wrap"');
    expect(settingsPage).toContain('className="ct-page-head"');
  });

  it('does not introduce hero/ornament marketing styles in onboarding shell blocks', () => {
    const css = read('src/styles.css');
    const onboardingBlockMatch = css.match(/\.ct-onboarding[\s\S]*?\.ct-onboarding-panel-title\s*{[\s\S]*?}/);
    expect(onboardingBlockMatch).not.toBeNull();
    const onboardingBlock = onboardingBlockMatch?.[0] ?? '';

    expect(onboardingBlock.toLowerCase()).not.toContain('hero');
    expect(onboardingBlock).not.toContain('radial-gradient');
    expect(onboardingBlock).not.toContain('conic-gradient');
    expect(onboardingBlock).not.toContain('url(');
  });
});
