const { chromium } = require("playwright");
const fs = require("fs"), path = require("path"), { execFileSync } = require("child_process");
const BASE = path.join(__dirname,"..");
const CREDS = JSON.parse(fs.readFileSync(path.join(BASE,"state","credentials.json"),"utf8"));
const OCR = path.join(BASE,"ocr","read2.js"), PNG = path.join(BASE,"data","captcha_pw.png");
const API = "https://api.meibours.ir/Web/V1";
function solve(uri){ if(!uri||!uri.startsWith("data:image")) return ""; fs.writeFileSync(PNG, Buffer.from(uri.split(",")[1],"base64"));
  try{ const o=execFileSync(process.execPath,[OCR,PNG],{encoding:"utf8",timeout:180000}); return String(JSON.parse((o||"{}").trim()||"{}").text||""); }catch(e){ return ""; } }
(async()=>{
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
async function loginFormVisible() {
  return await page.evaluate(() => {
    if (!/login/i.test(location.href)) return false;
    const u = document.querySelector("#username");
    if (!u) return false;
    const r = u.getBoundingClientRect();
    const st = window.getComputedStyle(u);
    return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
  });
}

  const ctx = await chromium.launchPersistentContext(path.join(BASE,"state","pw-profile"), { channel:"chrome", headless:false, args:["--no-first-run"] });
  const page = ctx.pages()[0] || await ctx.newPage();
  await page.goto("https://online.meibours.ir/", { waitUntil:"domcontentloaded", timeout:60000 });
  await page.waitForTimeout(3500);
  if (await loginFormVisible()) {
    let code="";
    for(let i=0;i<3;i++){ await page.click("#captcha-img",{force:true}).catch(()=>{}); await page.waitForTimeout(2500);
      code = solve(await page.getAttribute("#captcha-img","src").catch(()=>"")); if(code.length===4) break; }
    for (const [sel,val] of [["#username",CREDS.user],["#password",CREDS.password],["#captcha",code]]) {
      await page.click(sel); await page.keyboard.press("Control+A"); await page.keyboard.press("Backspace");
      await page.keyboard.type(val,{delay:20}); }
    await page.evaluate(()=>{ if(typeof submitForm==="function") submitForm(); else document.getElementById("main-form")?.click(); });
    await page.waitForTimeout(12000);
  }
  const j = async (url, opts) => page.evaluate(async ({url, opts}) => {
    const r = await fetch(url, Object.assign({credentials:"include"}, opts||{}));
    return {s:r.status, t:(await r.text()).slice(0,220)};
  }, {url, opts});
  const out = {before:null, tries:[], after:null, order:null, open:null};
  out.before = await j(`${API}/Agreement/CheckAgreement`);
  const bodies = [{AgreementId:2},{Id:2},{agreementId:2},{}];
  for (const b of bodies) {
    const r = await j(`${API}/Agreement/AcceptAgreement`, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(b)});
    out.tries.push({b, ...r});
    const chk = await j(`${API}/Agreement/CheckAgreement`);
    if (!/IsSuccessfull":false/.test(chk.t)) { out.after = chk; break; }
  }
  out.after = out.after || await j(`${API}/Agreement/CheckAgreement`);
  out.order = await j(`${API}/Order/Post`, {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({IsSymbolCautionAgreement:true, CautionAgreementSelected:true, IsSymbolSepahAgreement:false, SepahAgreementSelected:false,
      orderCount:10000, orderPrice:1919, FinancialProviderId:1, minimumQuantity:0, maxShow:0, orderId:0, isin:"IRO1ABAD0001",
      orderSide:65, orderValidity:74, orderValiditydate:null, shortSellIsEnabled:false, shortSellIncentivePercent:0})});
  await page.waitForTimeout(4000);
  out.open = await j(`${API}/Order/GetOpenOrder/OpenOrder`);
  console.log(JSON.stringify(out).slice(0,1500));
  await ctx.close();
})();
