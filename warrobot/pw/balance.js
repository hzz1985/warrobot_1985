const { chromium } = require("playwright");
const SESSION = require("./session");
const fs = require("fs"), path = require("path"), { execFileSync } = require("child_process");
const BASE = path.join(__dirname,"..");
const CREDS = JSON.parse(fs.readFileSync(path.join(BASE,"state","credentials.json"),"utf8"));
const OCR = path.join(BASE,"ocr","read2.js"), PNG = path.join(BASE,"data","captcha_pw.png");
function solve(u){ if(!u||!u.startsWith("data:image")) return ""; fs.writeFileSync(PNG, Buffer.from(u.split(",")[1],"base64"));
 try{ const o=execFileSync(process.execPath,[OCR,PNG],{encoding:"utf8",timeout:180000}); return String(JSON.parse((o||"{}").trim()||"{}").text||""); }catch(e){ return ""; } }
(async()=>{
 const ctx = await chromium.launchPersistentContext(path.join(BASE,"state","pw-profile"), { channel:"chrome", headless:true, args:["--no-first-run"] });
 await SESSION.load(ctx);
 const page = ctx.pages()[0] || await ctx.newPage();
 await page.goto("https://online.meibours.ir/", { waitUntil:"domcontentloaded", timeout:60000 });
 await page.waitForTimeout(3500);
 
async function setVal(sel, val) {
  return await page.evaluate(({ sel, val }) => {
    const el = document.querySelector(sel);
    if (!el) return false;
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                                                     : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, String(val));
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }, { sel, val });
}
async function loginVisible() {
  return await page.evaluate(() => {
    const u = document.querySelector("#username");
    return !!(u && u.offsetParent !== null);
  });
}

 if (await SESSION.needLogin(page)) {
  const _gate = SESSION.loginAllowed();
  if (!_gate.ok) {
    console.log(JSON.stringify({ message: "login_blocked", reason: _gate.reason, day: _gate.day }));
    await ctx.close();
    return;
  }
  SESSION.noteLogin();
  let c=""; for(let i=0;i<4;i++){ await page.click("#captcha-img",{force:true}).catch(()=>{}); await page.waitForTimeout(2000);
   c = solve(await page.getAttribute("#captcha-img","src").catch(()=>"")); if(c.length===4) break; }
  await setVal("#username", CREDS.user);
  await setVal("#password", CREDS.password);
  await setVal("#captcha", c);
  await page.evaluate(()=>{ if(typeof submitForm==="function") submitForm(); else document.getElementById("main-form")?.click(); });
  await page.waitForTimeout(12000);
  await SESSION.save(ctx);
 }
 const open = await page.evaluate(async ()=>{ const r=await fetch("https://api.meibours.ir/Web/V1/Order/GetOpenOrder/OpenOrder",{credentials:"include"}); return (await r.text()).slice(0,900); });
 const acc = await page.evaluate(async ()=>{ const r=await fetch("https://api.meibours.ir/Web/V1/Accounting/Remain",{credentials:"include"}); return (await r.text()); });
 let parsed = {};
 try { parsed = JSON.parse(acc).Data || {}; } catch (e) { parsed = {_raw: String(acc).slice(0,300), _err: String(e)}; }
 console.log(JSON.stringify({
   ok: !!parsed.AccountBalance,
   account_balance: parsed.AccountBalance,
   real_balance: parsed.RealBalance,
   blocked_balance: parsed.BlockedBalance,
   purchasing_power: parsed.PurchasingPower,
   bourse_code: parsed.BourseCode,
   title: parsed.Title,
 }, null, 0));
 await SESSION.save(ctx).catch(()=>{});
 await ctx.close();
})();
