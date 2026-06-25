import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

describe('theme and layout contract', () => {
  it('keeps three-column workspace and core panel classes', () => {
    const file = path.resolve(process.cwd(), 'src/styles.css');
    const css = fs.readFileSync(file, 'utf8');

    expect(css).toContain('.ct-workspace');
    expect(css).toContain(
      'grid-template-columns: 280px minmax(640px, 1fr) 320px;',
    );
    expect(css).toContain('.ct-center-panel');
    expect(css).toContain('.ct-message-stream');
    expect(css).toContain('.ct-history-rail');
    expect(css).toContain('.ct-right-rail');
  });

  it('keeps the chat composer inside the visible workbench viewport', () => {
    const file = path.resolve(process.cwd(), 'src/styles.css');
    const css = fs.readFileSync(file, 'utf8');

    expect(css).toMatch(/\.ct-workspace\s*{[^}]*height:\s*calc\(100vh - 56px\);/s);
    expect(css).toMatch(/\.ct-workspace\s*{[^}]*overflow:\s*hidden;/s);
    expect(css).toMatch(/\.ct-center-panel\s*{[^}]*overflow:\s*hidden;/s);
    expect(css).toMatch(/\.ct-message-stream\s*{[^}]*flex:\s*1;[^}]*overflow:\s*auto;[^}]*min-height:\s*0;/s);
    expect(css).toMatch(/\.ct-composer\s*{[^}]*flex:\s*0 0 auto;/s);
  });

  it('preserves line breaks in plain chat messages', () => {
    const file = path.resolve(process.cwd(), 'src/styles.css');
    const css = fs.readFileSync(file, 'utf8');

    expect(css).toMatch(/\.ct-message-text\s*{[^}]*white-space:\s*pre-wrap;/s);
  });

  it('keeps core workspace labels free of internal forbidden terms', () => {
    const files = [
      'src/routes/HomePage.tsx',
      'src/components/AppShell.tsx',
      'src/components/HistoryRail.tsx',
      'src/components/MessageStream.tsx',
      'src/components/RightRail.tsx',
      'src/components/EmptyStates.tsx',
    ];
    const text = files
      .map((file) => fs.readFileSync(path.resolve(process.cwd(), file), 'utf8'))
      .join('\n')
      .toLowerCase();

    const forbidden = [
      'openclaw',
      'openviking',
      'mongodb',
      'worker id',
      'provider attempt',
      'runtime marker',
      'artifact',
      'localpath',
      'credentialref',
    ];

    for (const term of forbidden) {
      expect(text).not.toContain(term);
    }
  });
});
