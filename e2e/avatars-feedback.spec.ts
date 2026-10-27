import {test, expect} from '@playwright/test';
import {createUser, createGame, loginAs, uniqueName} from './helpers';

test('avatars load, sound preference persists, and reduced motion disables transitions', async ({page, request}) => {
 const user=await createUser(request, uniqueName('Avatar'));
 const name=uniqueName('Avatar game');
 await createGame(request,user,name);
 await loginAs(page,user);
 await page.goto('/static/portal.html');
 await page.getByRole('combobox',{name:'Search games'}).fill(name);
 await page.getByRole('option',{name:new RegExp(name)}).click();
 await page.getByRole('button',{name:/Create match/}).click();
 await page.getByRole('button',{name:'Add computer opponent'}).click();
 await expect(page.locator('#seats img')).toHaveCount(2);
 await expect.poll(() => page.locator('#seats img').evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth === 96))).toBe(true);
 const paths=await page.locator('#seats img').evaluateAll(images=>images.map(image=>(image as HTMLImageElement).src));
 expect(paths.some(path=>path.includes('/computers/'))).toBe(true);
 expect(paths.some(path=>path.includes('/users/'))).toBe(true);
 const accountPicture = await page.locator('.auth-menu-button .auth-avatar').getAttribute('src');
 await expect(page.locator('#seats img').first()).toHaveAttribute('src', accountPicture!);
 await expect(page.locator('#seats img').first()).toHaveCSS('width', '44px');
 const sound=page.locator('#match-sounds');
 await expect(sound).toHaveAttribute('aria-pressed','false');
 await page.evaluate(() => {
   const original=AudioContext.prototype.createOscillator;
   (window as any).toneCount=0;
   AudioContext.prototype.createOscillator=function() {(window as any).toneCount++; return original.call(this);};
 });
 await sound.click();
 await expect(sound).toHaveAttribute('aria-pressed','true');
 await page.evaluate(async () => {
   await new Promise(resolve=>setTimeout(resolve,100));
   const feedback=(window as any).PortalFeedback;
   feedback.reset();
   feedback.update({id:'sound-check',move_count:0,status:'ongoing'},'test',{yourTurn:false});
   feedback.update({id:'sound-check',move_count:1,status:'ongoing'},'test',{yourTurn:true});
   feedback.update({id:'sound-check',move_count:1,status:'ongoing'},'test',{yourTurn:true});
 });
 await expect.poll(()=>page.evaluate(()=>(window as any).toneCount)).toBe(2);
 await page.reload();
 await expect(sound).toHaveAttribute('aria-pressed','true');
 await page.emulateMedia({reducedMotion:'reduce'});
 await expect.poll(()=>page.locator('#match-tools').evaluate(node=>getComputedStyle(node).animationName)).toBe('none');
 await sound.click();
 await expect(sound).toHaveAttribute('aria-pressed','false');
});
