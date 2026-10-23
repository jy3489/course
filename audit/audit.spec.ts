import fs from 'node:fs';
import path from 'node:path';
import { test, type Page } from '@playwright/test';
import { createUser, createGame, loginAs, uniqueName } from '../e2e/helpers';

interface Issue { severity: 'error' | 'warn'; kind: string; detail: string }

const SIZES = [
  { name: 'small-phone', width: 360, height: 640, touch: true },
  { name: 'phone', width: 390, height: 844, touch: true },
  { name: 'phone-landscape', width: 844, height: 390, touch: true },
  { name: 'tablet', width: 820, height: 1180, touch: true },
  { name: 'laptop', width: 1366, height: 768, touch: false },
  { name: 'desktop', width: 1920, height: 1080, touch: false },
];

const PAGES = [
  { name: 'portal (logged out)', url: '/static/portal.html', login: false },
  { name: 'portal (logged in)', url: '/static/portal.html', login: true },
  { name: 'builder (logged out)', url: '/static/builder.html', login: false },
  { name: 'builder (logged in)', url: '/static/builder.html', login: true },
  { name: 'browse', url: '/static/browse.html', login: false },
  { name: 'invites', url: '/static/invites.html', login: false },
  { name: 'stats', url: '/static/stats.html', login: false },
  { name: 'privacy', url: '/static/privacy.html', login: false },
];

/** Runs inside the browser: finds layout and accessibility problems on the current page. */
function inspect(touch: boolean) {
  const issues: { severity: 'error' | 'warn'; kind: string; detail: string }[] = [];
  const vw = document.documentElement.clientWidth;
  const describe = (el: Element) => {
    const id = el.id ? `#${el.id}` : '';
    const cls = typeof (el as HTMLElement).className === 'string' && (el as HTMLElement).className
      ? '.' + (el as HTMLElement).className.trim().split(/\s+/).slice(0, 2).join('.') : '';
    const text = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 30);
    return `<${el.tagName.toLowerCase()}${id}${cls}>${text ? ` "${text}"` : ''}`;
  };
  const visible = (el: Element) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !el.closest('[hidden]');
  };
  const inViewport = (el: Element) => {
    // Skip things inside an off-screen drawer or scrolled-away containers.
    const r = el.getBoundingClientRect();
    return r.right > 0 && r.left < vw;
  };

  if (document.documentElement.scrollWidth > vw) {
    issues.push({ severity: 'error', kind: 'horizontal-scroll', detail: `page is ${document.documentElement.scrollWidth - vw}px wider than the screen` });
  }

  // Report only the outermost element that sticks out. If a scrollable container holds it,
  // the user can still reach it by swiping sideways, so that is a warning, not an error.
  const sticking: Element[] = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || !inViewport(el) || el.closest('iframe') || el.closest('.sidebar')) continue;
    if (el.getBoundingClientRect().right > vw + 1) sticking.push(el);
  }
  const outermost = sticking.filter((el) => !sticking.some((o) => o !== el && o.contains(el)));
  const seen = new Set<string>();
  for (const el of outermost.slice(0, 8)) {
    let scroller: Element | null = el.parentElement;
    while (scroller && !['auto', 'scroll'].includes(getComputedStyle(scroller).overflowX)) scroller = scroller.parentElement;
    const d = describe(el);
    if (seen.has(d)) continue;
    seen.add(d);
    issues.push(scroller
      ? { severity: 'warn', kind: 'needs-sideways-scroll', detail: `${d} is wider than the screen (inside scrollable ${describe(scroller)})` }
      : { severity: 'error', kind: 'sticks-out-of-screen', detail: d });
  }

  for (const el of document.querySelectorAll('button, a[href], input, select, textarea, [role=button]')) {
    if (!visible(el) || el.closest('.sidebar') && !inViewport(el)) continue;
    const input = el as HTMLInputElement;
    if (input.type === 'hidden') continue;
    const name = (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || input.placeholder || '').trim()
      || (input.id && document.querySelector(`label[for="${input.id}"]`)?.textContent?.trim())
      || (el.closest('label')?.textContent?.trim());
    if (!name) issues.push({ severity: 'error', kind: 'no-accessible-name', detail: describe(el) });
    if (touch) {
      const r = el.getBoundingClientRect();
      if (r.width < 40 || r.height < 40) {
        issues.push({ severity: 'warn', kind: 'small-tap-target', detail: `${describe(el)} is ${Math.round(r.width)}x${Math.round(r.height)}px (want ~44x44)` });
      }
    }
  }

  for (const img of document.querySelectorAll('img')) {
    if (!visible(img)) continue;
    if (!img.complete || img.naturalWidth === 0) issues.push({ severity: 'error', kind: 'broken-image', detail: img.src.slice(0, 80) });
    if (!img.hasAttribute('alt')) issues.push({ severity: 'warn', kind: 'image-without-alt', detail: img.src.slice(0, 80) });
  }

  const small = new Set<string>();
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || !inViewport(el)) continue;
    const hasText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent!.trim());
    if (hasText && parseFloat(getComputedStyle(el).fontSize) < 11) small.add(describe(el));
  }
  for (const d of [...small].slice(0, 5)) issues.push({ severity: 'warn', kind: 'tiny-text(<11px)', detail: d });

  const clipped = new Set<string>();
  for (const el of document.querySelectorAll('button, h1, h2, h3, label, .badge')) {
    if (!visible(el) || !inViewport(el)) continue;
    const s = getComputedStyle(el);
    if (el.scrollWidth > el.clientWidth + 2 && s.overflowX !== 'visible' && s.textOverflow !== 'ellipsis') clipped.add(describe(el));
  }
  for (const d of [...clipped].slice(0, 5)) issues.push({ severity: 'warn', kind: 'clipped-text', detail: d });

  if (!document.title.trim()) issues.push({ severity: 'warn', kind: 'missing-title', detail: 'page has no <title>' });
  if (!document.documentElement.lang) issues.push({ severity: 'warn', kind: 'missing-lang', detail: '<html> has no lang attribute' });
  return issues;
}

async function collect(page: Page, touch: boolean): Promise<Issue[]> {
  return page.evaluate(inspect, touch);
}

test('UI audit: report everything that does not look right', async ({ browser, request }) => {
  const outDir = path.resolve(__dirname, '../audit-report');
  fs.rmSync(outDir, { recursive: true, force: true });
  fs.mkdirSync(path.join(outDir, 'screenshots'), { recursive: true });

  const user = await createUser(request, uniqueName('Auditor'));
  await createGame(request, user, uniqueName('Audit game'));

  const results: { page: string; size: string; screenshot: string; issues: Issue[] }[] = [];

  for (const size of SIZES) {
    for (const p of PAGES) {
      const context = await browser.newContext({
        viewport: { width: size.width, height: size.height },
        hasTouch: size.touch,
        isMobile: size.touch,
      });
      const page = await context.newPage();
      const runtime: Issue[] = [];
      page.on('pageerror', (e) => runtime.push({ severity: 'error', kind: 'javascript-error', detail: e.message.slice(0, 160) }));
      page.on('console', (m) => {
        if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) {
          runtime.push({ severity: 'warn', kind: 'console-error', detail: m.text().slice(0, 160) });
        }
      });
      page.on('response', (r) => {
        if (r.status() >= 400 && r.status() !== 401) {
          runtime.push({ severity: 'error', kind: `http-${r.status()}`, detail: `${r.request().method()} ${r.url().replace(/^https?:\/\/[^/]+/, '')}` });
        }
      });
      if (p.login) await loginAs(page, user);

      await page.goto(p.url);
      await page.waitForLoadState('networkidle').catch(() => {});
      await page.waitForTimeout(300);

      const file = `${size.name}__${p.name.replace(/[^a-z0-9]+/gi, '-')}.png`;
      await page.screenshot({ path: path.join(outDir, 'screenshots', file), fullPage: true });
      const issues = [...(await collect(page, size.touch)), ...runtime];
      results.push({ page: p.name, size: `${size.name} (${size.width}x${size.height})`, screenshot: `screenshots/${file}`, issues });
      await context.close();
    }
  }

  // Group identical problems across sizes so the report stays short.
  const byPage = new Map<string, Map<string, { issue: Issue; sizes: Set<string> }>>();
  for (const r of results) {
    const pageMap = byPage.get(r.page) ?? new Map();
    byPage.set(r.page, pageMap);
    for (const i of r.issues) {
      const key = `${i.severity}|${i.kind}|${i.detail}`;
      const entry = pageMap.get(key) ?? { issue: i, sizes: new Set<string>() };
      entry.sizes.add(r.size.split(' ')[0]);
      pageMap.set(key, entry);
    }
  }

  let errors = 0;
  let warns = 0;
  const lines: string[] = ['# UI audit report', '', `Pages x screen sizes checked: ${results.length}`, ''];
  for (const [pageName, map] of byPage) {
    lines.push(`## ${pageName}`, '');
    if (!map.size) { lines.push('Nothing wrong found.', ''); continue; }
    const sorted = [...map.values()].sort((a, b) => (a.issue.severity === b.issue.severity ? 0 : a.issue.severity === 'error' ? -1 : 1));
    for (const { issue, sizes } of sorted) {
      issue.severity === 'error' ? errors++ : warns++;
      const icon = issue.severity === 'error' ? 'ERROR' : 'warn ';
      lines.push(`- ${icon} **${issue.kind}**: ${issue.detail} _(on: ${[...sizes].join(', ')})_`);
    }
    lines.push('');
  }
  lines.splice(3, 0, `**${errors} errors, ${warns} warnings** (screenshots of every page and size are in \`audit-report/screenshots/\`)`, '');
  fs.writeFileSync(path.join(outDir, 'report.md'), lines.join('\n'));
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(results, null, 2));

  console.log('\n' + lines.join('\n'));
  console.log(`\nFull report: ${path.join(outDir, 'report.md')}`);
});
