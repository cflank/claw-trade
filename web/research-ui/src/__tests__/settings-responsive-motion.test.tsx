import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

function read(file: string) {
  return fs.readFileSync(path.resolve(process.cwd(), file), 'utf8');
}

describe('settings-responsive-motion', () => {
  it('contains required 960px and 560px responsive rules for settings surfaces', () => {
    const css = read('src/styles.css');

    expect(css).toMatch(/@media\s*\(max-width:\s*960px\)\s*{[\s\S]*?\.ct-onboarding\s*{[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\);/);
    expect(css).toMatch(/@media\s*\(max-width:\s*960px\)\s*{[\s\S]*?\.ct-model-grid,\s*\.ct-form-grid\s*{[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\);/);
    expect(css).toMatch(/@media\s*\(max-width:\s*960px\)\s*{[\s\S]*?\.ct-source-grid\s*{[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\);/);
    expect(css).toMatch(/@media\s*\(max-width:\s*560px\)\s*{[\s\S]*?\.ct-settings-wrap,\s*\.ct-onboarding-wrap\s*{[\s\S]*?padding:\s*14px 10px 28px;/);
    expect(css).toMatch(/@media\s*\(max-width:\s*560px\)\s*{[\s\S]*?\.ct-button,\s*\.ct-button-secondary\s*{[\s\S]*?width:\s*100%;/);
    expect(css).toMatch(/@media\s*\(max-width:\s*560px\)\s*{[\s\S]*?\.ct-task-field\s*{[\s\S]*?grid-template-columns:\s*64px minmax\(0,\s*1fr\);/);
  });

  it('defines only 120ms micro-motion transition and no page-level animation keyframes', () => {
    const css = read('src/styles.css');

    expect(css).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*no-preference\)\s*{[\s\S]*?\.ct-button,[\s\S]*?\.ct-status-pill\s*{[\s\S]*?transition:[\s\S]*?120ms[\s\S]*?120ms[\s\S]*?120ms[\s\S]*?120ms/s,
    );
    expect(css).not.toContain('@keyframes');
    expect(css).not.toMatch(/animation\s*:/);
  });
});
