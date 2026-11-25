// Port-80 / port-8080 reverse proxy for the trading dashboard.
//
//   http://<host>/        -> trading dashboard   (127.0.0.1:5077)
//   http://<host>/tse     -> trading dashboard   (127.0.0.1:5077)
//   http://<host>/pdf     -> PDF Manager         (127.0.0.1:3000)
//   http://<host>/4ms     -> 4Ms web app         (127.0.0.1:4410)
//
// Both port 80 (dual stack IPv4+IPv6) and 8090 are served, so a browser or
// network that blocks port 80 still has a working entry point.
const http = require('http');

const PDF = { host: '127.0.0.1', port: 3000 };
const TSE = { host: '127.0.0.1', port: 5077 };
const M4S = { host: '127.0.0.1', port: 4410 };

const TSE_PREFIX = '/tse';
const PDF_PREFIX = '/pdf';
const M4S_PREFIX = '/4ms';

function forward(req, res, target, path) {
  const headers = Object.assign({}, req.headers, {
    host: target.host + ':' + target.port,
    'x-forwarded-host': req.headers.host || '',
    'x-forwarded-proto': 'http',
  });
  const opts = { host: target.host, port: target.port, method: req.method, path: path, headers };
  const proxyReq = http.request(opts, (proxyRes) => {
    res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
    proxyRes.pipe(res);
  });
  proxyReq.on('error', () => {
    if (!res.headersSent) {
      res.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' });
    }
    res.end('upstream error: ' + target.port);
  });
  req.pipe(proxyReq);
}

function stripPrefix(url, prefix) {
  let p = url.slice(prefix.length);
  if (!p) p = '/';
  if (p.startsWith('?')) p = '/' + p;
  if (!p.startsWith('/')) p = '/' + p;
  return p;
}

function handle(req, res) {
  const url = req.url || '/';

  if (url === M4S_PREFIX || url.startsWith(M4S_PREFIX + '/') || url.startsWith(M4S_PREFIX + '?')) {
    return forward(req, res, M4S, url);
  }
  if (url === PDF_PREFIX || url.startsWith(PDF_PREFIX + '/') || url.startsWith(PDF_PREFIX + '?')) {
    return forward(req, res, PDF, stripPrefix(url, PDF_PREFIX));
  }
  if (url === TSE_PREFIX || url.startsWith(TSE_PREFIX + '/') || url.startsWith(TSE_PREFIX + '?')) {
    return forward(req, res, TSE, stripPrefix(url, TSE_PREFIX));
  }
  // default: the trading dashboard
  return forward(req, res, TSE, url);
}

const listeners = [
  { port: 80, host: '::', ipv6Only: false, backlog: 511 },
  { port: 8090, host: '::', ipv6Only: false, backlog: 511 },
];

listeners.forEach((opt) => {
  const server = http.createServer(handle);
  server.on('error', (e) => console.log('[proxy] ' + opt.port + ' error: ' + e.message));
  server.listen(opt, () => {
    console.log('[proxy] :' + opt.port + ' dual-stack  /,/tse -> 5077   /pdf -> 3000   /4ms -> 4410');
  });
});
