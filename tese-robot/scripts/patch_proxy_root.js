// Serve the trading dashboard at "/" (port 80), keep the PDF app under /pdf.
const fs = require("fs");
const path = "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\TeseRobot\\scripts\\proxy80.js";
let s = fs.readFileSync(path, "utf8");

if (!s.includes("PDF_PREFIX")) {
  s = s.replace("const TSE_PREFIX = '/tse';", "const TSE_PREFIX = '/tse';\nconst PDF_PREFIX = '/pdf';   // the PDF app moved off \"/\"");
  s = s.replace(`  } else {
    forward(req, res, PDF, url);
  }`, `  } else if (url === PDF_PREFIX || url.startsWith(PDF_PREFIX + '/') || url.startsWith(PDF_PREFIX + '?')) {
    let p = url.slice(PDF_PREFIX.length);
    if (!p) p = '/';
    if (!p.startsWith('/')) p = '/' + p;
    forward(req, res, PDF, p);
  } else {
    // default: the trading dashboard (was the PDF app before 1405-08-22)
    forward(req, res, TSE, url);
  }`);
  s = s.replace("console.log('proxy :80  /4ms -> 4410   /tse -> 5077   / -> 3000');",
                "console.log('proxy :80  /4ms -> 4410   /tse,/ -> 5077   /pdf -> 3000');");
  fs.writeFileSync(path, s, "utf8");
  console.log("proxy80.js: / now serves the dashboard, PDF app moved to /pdf");
} else {
  console.log("proxy already patched");
}
