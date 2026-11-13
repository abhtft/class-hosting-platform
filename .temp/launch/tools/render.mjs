import puppeteer from 'puppeteer-core';
import { mkdirSync } from 'node:fs';
const ROOT = 'D:/Github_Codes/class-hosting-platform-github/.temp/launch';
const URL = `file:///${ROOT}/slides/deck.html`;
mkdirSync(`${ROOT}/out/frames`, { recursive: true });
mkdirSync(`${ROOT}/out/png`, { recursive: true });
const browser = await puppeteer.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: 'new', args: ['--no-sandbox'] });
const open = async (hash, scale) => {
  const page = await browser.newPage();
  await page.setViewport({ width: 1080, height: 1350, deviceScaleFactor: scale });
  await page.goto(URL + hash, { waitUntil: 'networkidle0' });
  await page.evaluate(() => document.fonts.ready);
  await new Promise((r) => setTimeout(r, 250));
  return page;
};
const probe = await open('', 1);
const steps = await probe.evaluate(() => window.__steps);
await probe.close();
console.log('steps per slide:', steps.join(','));
for (let i = 0; i < steps.length; i++) {
  for (let k = 0; k < steps[i]; k++) {
    const p = await open(`#slide=${i + 1}&step=${k}`, 1);
    await p.screenshot({ path: `${ROOT}/out/frames/s${i + 1}_k${k}.png` });
    await p.close();
  }
  const f = await open(`#slide=${i + 1}`, 2); // final state, crisp
  await f.screenshot({ path: `${ROOT}/out/png/slide-${i + 1}.png` });
  await f.close();
}
const all = await open('', 1);
await all.pdf({ path: `${ROOT}/out/class-hosting-platform-carousel.pdf`, width: '1080px', height: '1350px', printBackground: true });
await all.close();
await browser.close();
console.log('rendered');
