// IPv6 bridge: desktop Chrome often resolves "localhost" to ::1 first, and the
// Flask app only listens on IPv4 (0.0.0.0). This small forwarder listens on
// [::1] for the dashboard ports and proxies to 127.0.0.1 on the same port, so
// http://localhost:5077 works on desktop as well as on mobile.
const http = require('http');

const PORTS = [5077, 5000];

function bridge(port) {
  const server = http.createServer((req, res) => {
    const opts = {
      host: '127.0.0.1',
      port,
      method: req.method,
      path: req.url,
      headers: Object.assign({}, req.headers, { host: '127.0.0.1:' + port }),
    };
    const up = http.request(opts, (upRes) => {
      res.writeHead(upRes.statusCode || 502, upRes.headers);
      upRes.pipe(res);
    });
    up.on('error', () => {
      if (!res.headersSent) res.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' });
      res.end('upstream error');
    });
    req.pipe(up);
  });
  server.listen({ port, host: '::1', ipv6Only: true }, () => {
    console.log('[v6bridge] ::1:' + port + ' -> 127.0.0.1:' + port);
  });
  server.on('error', (e) => console.log('[v6bridge] ' + port + ' error: ' + e.message));
}

PORTS.forEach(bridge);
