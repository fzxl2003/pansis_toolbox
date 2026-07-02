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

module.exports = {
    bindingAddress: host,
    port,
    crossDomainPort,
    publicDir: null,
    enableWorkers: false,
    workers: 1,
    ssl: null,
    getServerInfo: () => ({ hostname: publicHost, port: publicPort, crossDomainPort: publicPort, protocol: publicProtocol }),
    password,
    disableLocalStorageSync: false,
    restrictSessionToIP: true,
    jsCache: new RammerheadJSFileCache(path.join(dataDir, 'cache-js'), 256 * 1024 * 1024, 10000, false),
    disableHttp2: false,
    stripClientHeaders: [],
    rewriteServerHeaders: {
        'x-frame-options': null,
        'content-security-policy': null,
        'content-security-policy-report-only': null
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
