const path = require('path');
const RammerheadJSFileCache = require('./src/classes/RammerheadJSFileCache.js');

const dataDir = process.env.PANSIS_WEB_PROXY_DATA_DIR || path.join(__dirname, '../../../storage/web_proxy');
const port = Number(process.env.PANSIS_WEB_PROXY_PORT || 8787);
const crossDomainPort = Number(process.env.PANSIS_WEB_PROXY_CROSS_PORT || 8788);
const host = process.env.PANSIS_WEB_PROXY_HOST || '127.0.0.1';
const publicHost = process.env.PANSIS_WEB_PROXY_PUBLIC_HOST || host;
const publicPort = Number(process.env.PANSIS_WEB_PROXY_PUBLIC_PORT || port);
const publicProtocol = process.env.PANSIS_WEB_PROXY_PUBLIC_PROTOCOL || 'http:';
const password = process.env.PANSIS_WEB_PROXY_PASSWORD || null;

// Resolve the public origin for the *current* request so that URL rewriting
// matches how the browser actually reached us.  The toolbox reverse proxy
// forwards ``X-Forwarded-Proto`` / ``X-Forwarded-Host`` from the real client,
// so when the site is served over HTTPS the rewritten asset URLs use
// ``https:`` too (preventing mixed-content blocks).  Falls back to the
// environment-variable defaults for direct (non-proxied) access.
const parseHostPort = (raw) => {
    if (!raw) return { hostname: publicHost, port: publicPort };
    let hostname = raw;
    let port = publicPort;
    if (raw.startsWith('[') && raw.includes(']')) {
        // IPv6 literal, e.g. [::1]:8799
        const end = raw.indexOf(']');
        hostname = raw.slice(0, end + 1);
        const tail = raw.slice(end + 1);
        if (tail.startsWith(':') && /^\d+$/.test(tail.slice(1))) port = Number(tail.slice(1));
    } else if (raw.includes(':')) {
        const idx = raw.lastIndexOf(':');
        const maybePort = raw.slice(idx + 1);
        if (/^\d+$/.test(maybePort)) {
            hostname = raw.slice(0, idx);
            port = Number(maybePort);
        }
    }
    return { hostname, port };
};

const resolveServerInfo = (req) => {
    const headers = (req && req.headers) || {};
    let proto = (headers['x-forwarded-proto'] || '').toString().split(',')[0].trim();
    if (proto === 'ws' || proto === 'ws:') proto = 'http';
    else if (proto === 'wss' || proto === 'wss:') proto = 'https';
    if (!proto) proto = publicProtocol.replace(':', '');
    const forwardedHost = (headers['x-forwarded-host'] || '').toString().split(',')[0].trim();
    const { hostname, port } = parseHostPort(forwardedHost || headers['host']);
    return {
        hostname: hostname || publicHost,
        port: port || publicPort,
        crossDomainPort: port || publicPort,
        protocol: proto.endsWith(':') ? proto : `${proto}:`
    };
};

module.exports = {
    bindingAddress: host,
    port,
    crossDomainPort,
    publicDir: null,
    enableWorkers: false,
    workers: 1,
    ssl: null,
    getServerInfo: resolveServerInfo,
    password,
    disableLocalStorageSync: false,
    restrictSessionToIP: true,
    jsCache: new RammerheadJSFileCache(path.join(dataDir, 'cache-js'), 256 * 1024 * 1024, 10000, false),
    disableHttp2: false,
    // These headers describe the toolbox/reverse-proxy path, not the user's
    // browser request.  They are consumed by getServerInfo for URL rewriting
    // and then removed by Rammerhead before it connects to the destination.
    stripClientHeaders: [
        'forwarded', 'via', 'x-forwarded-for', 'x-forwarded-host',
        'x-forwarded-proto', 'x-real-ip', 'cf-connecting-ip', 'cf-ipcountry',
        'cf-ray', 'cf-visitor', 'cdn-loop'
    ],
    rewriteServerHeaders: {
        'x-frame-options': null,
        'content-security-policy': null,
        'content-security-policy-report-only': null,
        // Some upstream gateways add this to otherwise normal HTML pages.
        // Hammerhead then treats the page as a download and skips URL/script
        // rewriting, so follow-up links leave the proxy session.
        'content-disposition': null
    },
    fileCacheSessionConfig: {
        saveDirectory: path.join(dataDir, 'sessions'),
        cacheTimeout: 1000 * 60 * 20,
        cacheCheckInterval: 1000 * 60 * 10,
        deleteUnused: false,
        staleCleanupOptions: null,
        deleteCorruptedSessions: true
    },
    logLevel: process.env.PANSIS_WEB_PROXY_LOG_LEVEL || 'warn',
    generatePrefix: (level) => `[${new Date().toISOString()}] [${level.toUpperCase()}] `,
    getIP: (req) => req.socket.remoteAddress
};
