const express = require('express');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode');
const fs = require('fs');
const path = require('path');

const app = express();
app.use(express.json());

const PORT = 4050;
const API_URL = (process.env.API_URL || 'http://api:8000').replace(/\/$/, '');
const SECRET = process.env.WA_BRIDGE_SECRET || '';
const AUTH_DIR = path.join(__dirname, '.wwebjs_auth');
const sessions = {}; // agent_id -> { client, qrDataUrl, status, info }

// Every /api/wa/* call must carry the shared secret when one is configured.
app.use('/api/wa', (req, res, next) => {
    if (SECRET && req.get('X-Bridge-Secret') !== SECRET) {
        return res.status(401).json({ ok: false, error: 'Bad bridge secret.' });
    }
    next();
});

// Linked numbers are ordinary WhatsApp accounts: behave like a person to avoid bans.
const MAX_SENDS_PER_MIN = 8;                 // per number, replies included
const MAX_RATE_WAIT_MS = 20000;              // wait for a free slot this long, then 429
const sendTimes = {};                        // agent_id -> timestamps of recent sends
const reconnects = {};                       // agent_id -> consecutive reconnect attempts
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function takeSendSlot(agentId) {
    const recent = (sendTimes[agentId] = (sendTimes[agentId] || []).filter((t) => Date.now() - t < 60000));
    if (recent.length >= MAX_SENDS_PER_MIN) {
        const wait = recent[0] + 60000 - Date.now();
        if (wait > MAX_RATE_WAIT_MS) return false;
        await sleep(wait + 50);
    }
    sendTimes[agentId] = (sendTimes[agentId] || []).filter((t) => Date.now() - t < 60000).concat(Date.now());
    return true;
}

// Reconnect after a drop with exponential backoff (5s ... 5min); never a hot loop.
function scheduleReconnect(agentId, sessionData) {
    if (sessionData.manual || sessionData.reconnectTimer) return;
    const attempt = reconnects[agentId] = (reconnects[agentId] || 0) + 1;
    const delay = Math.min(300000, 5000 * 2 ** (attempt - 1));
    console.log(`[Agent ${agentId}] Reconnecting in ${Math.round(delay / 1000)}s (attempt ${attempt}).`);
    sessionData.reconnectTimer = setTimeout(async () => {
        try { await sessionData.client.destroy(); } catch (_) { /* already closed */ }
        if (sessions[agentId] === sessionData) delete sessions[agentId];
        getClient(agentId);
    }, delay);
}

const MEDIA_TYPES =new Set(['image', 'video', 'audio', 'ptt', 'document', 'sticker', 'location', 'vcard', 'multi_vcard']);

// Hand an inbound chat message to the api; one retry, never throws.
async function forwardInbound(payload) {
    for (let attempt = 0; attempt < 2; attempt++) {
        try {
            const res = await fetch(`${API_URL}/api/whatsapp/inbound`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Bridge-Secret': SECRET },
                body: JSON.stringify(payload),
                signal: AbortSignal.timeout(10000)
            });
            if (res.ok) return;
            console.error(`[Agent ${payload.agent_id}] Inbound relay HTTP ${res.status}`);
        } catch (err) {
            console.error(`[Agent ${payload.agent_id}] Inbound relay failed:`, err.message);
        }
        await new Promise((r) => setTimeout(r, 1500));
    }
}

async function onMessage(agentId, msg) {
    try {
        if (msg.fromMe || msg.isStatus || !msg.from) return;
        if (msg.from === 'status@broadcast' || !msg.from.endsWith('@c.us') && !msg.from.endsWith('@lid')) return; // groups, channels, broadcasts
        let text;
        if (msg.type === 'chat') text = msg.body;
        else if (MEDIA_TYPES.has(msg.type)) text = `[media: ${msg.type}]` + (msg.body ? ` ${msg.body}` : '');
        else return;
        if (!text || !text.trim()) return;
        let digits = msg.from.replace(/\D/g, '');
        let name = msg._data?.notifyName || '';
        try {
            const contact = await msg.getContact();
            // "@lid" ids are not phone numbers: the contact carries the real one.
            if (contact?.number) digits = String(contact.number).replace(/\D/g, '');
            name = contact?.pushname || name;
        } catch (_) { /* contact lookup is best effort */ }
        // Read receipt a moment after it arrives, like a person opening the chat.
        sleep(800 + Math.random() * 1200).then(() => msg.getChat()).then((chat) => chat.sendSeen()).catch(() => {});
        await forwardInbound({
            agent_id: Number(agentId),
            from: digits,
            name,
            text,
            message_id: msg.id?._serialized || '',
            timestamp: msg.timestamp
        });
    } catch (err) {
        console.error(`[Agent ${agentId}] Message handler error:`, err);
    }
}

function getClient(agentId) {
    if (sessions[agentId]) {
        return sessions[agentId];
    }

    const authPath = path.join(__dirname, '.wwebjs_auth', `session_agent_${agentId}`);
    fs.mkdirSync(authPath, { recursive: true });

    const client = new Client({
        authStrategy: new LocalAuth({
            clientId: `agent_${agentId}`,
            dataPath: AUTH_DIR
        }),
        puppeteer: {
            headless: true,
            executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
            protocolTimeout: 180000, // WhatsApp Web's first load on a small VPS takes well over the 30 s default
            timeout: 120000,
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu'
            ]
        }
    });

    const sessionData = {
        client,
        qrDataUrl: null,
        status: 'initializing',
        phone: null,
        pushname: null,
        updatedAt: new Date().toISOString()
    };

    client.on('qr', async (qr) => {
        try {
            sessionData.qrDataUrl = await qrcode.toDataURL(qr, { margin: 2, scale: 7 });
            sessionData.status = 'qr_ready';
            sessionData.updatedAt = new Date().toISOString();
            console.log(`[Agent ${agentId}] New QR Code generated.`);
        } catch (err) {
            console.error(`[Agent ${agentId}] Error generating QR data URL:`, err);
        }
    });

    client.on('ready', () => {
        sessionData.status = 'connected';
        reconnects[agentId] = 0;
        sessionData.qrDataUrl = null;
        sessionData.phone = client.info?.wid?.user || 'Connected';
        sessionData.pushname = client.info?.pushname || '';
        sessionData.updatedAt = new Date().toISOString();
        console.log(`[Agent ${agentId}] WhatsApp Client is READY! Connected as: ${sessionData.phone}`);
    });

    client.on('message', (msg) => onMessage(agentId, msg));

    client.on('authenticated', () => {
        sessionData.status = 'authenticated';
        sessionData.updatedAt = new Date().toISOString();
        console.log(`[Agent ${agentId}] WhatsApp Client Authenticated.`);
    });

    client.on('auth_failure', (msg) => {
        sessionData.status = 'auth_failed';
        sessionData.updatedAt = new Date().toISOString();
        console.error(`[Agent ${agentId}] WhatsApp Auth failure:`, msg);
    });

    client.on('disconnected', (reason) => {
        sessionData.status = 'disconnected';
        sessionData.qrDataUrl = null;
        sessionData.phone = null;
        sessionData.updatedAt = new Date().toISOString();
        console.log(`[Agent ${agentId}] WhatsApp Disconnected:`, reason);
        scheduleReconnect(agentId, sessionData);
    });

    client.on('change_state', (state) => console.log(`[Agent ${agentId}] State changed: ${state}`));

    client.initialize().catch((err) => {
        console.error(`[Agent ${agentId}] Client init error:`, err);
        sessionData.status = 'error';
    });

    sessions[agentId] = sessionData;
    return sessionData;
}

// 1. Get status & QR code
app.get('/api/wa/:agentId/status', (req, res) => {
    const agentId = req.params.agentId;
    const session = getClient(agentId);
    res.json({
        ok: true,
        agentId,
        status: session.status,
        hasQr: !!session.qrDataUrl,
        qr: session.qrDataUrl,
        phone: session.phone,
        pushname: session.pushname,
        updatedAt: session.updatedAt
    });
});

// 1b. QR code on its own (same payload as status)
app.get('/api/wa/:agentId/qr', (req, res) => {
    const agentId = req.params.agentId;
    const session = getClient(agentId);
    res.json({ ok: true, agentId, status: session.status, qr: session.qrDataUrl });
});

// 2. Disconnect / Logout
app.post('/api/wa/:agentId/disconnect', async (req, res) => {
    const agentId = req.params.agentId;
    if (sessions[agentId]) {
        sessions[agentId].manual = true; // a deliberate unlink must not trigger the auto-reconnect
        clearTimeout(sessions[agentId].reconnectTimer);
        try {
            await sessions[agentId].client.logout().catch(() => {});
            await sessions[agentId].client.destroy().catch(() => {});
        } catch (e) {
            console.error(`Error closing session for agent ${agentId}:`, e);
        }
        delete sessions[agentId];
    }
    res.json({ ok: true, status: 'disconnected' });
});

// 3. Send message endpoint
app.post('/api/wa/:agentId/send', async (req, res) => {
    const agentId = req.params.agentId;
    const { to, text, typing } = req.body;

    if (!to || !text) {
        return res.status(400).json({ ok: false, error: 'Recipient "to" and "text" are required.' });
    }

    const session = getClient(agentId);
    if (session.status !== 'connected') {
        return res.status(503).json({
            ok: false,
            error: `WhatsApp not connected (Status: ${session.status}). Please scan QR code first.`
        });
    }

    try {
        let clean = to.replace(/\D/g, '');
        if (clean.length === 10) clean = '91' + clean;
        const chatId = `${clean}@c.us`;

        if (!(await takeSendSlot(agentId))) {
            return res.status(429).json({ ok: false, error: `Sending too fast (limit ${MAX_SENDS_PER_MIN}/min). Try again shortly.` });
        }
        if (typing) {
            // Replies show "typing..." for roughly as long as a person would take to write them.
            try {
                const chat = await session.client.getChatById(chatId);
                await chat.sendSeen();
                await chat.sendStateTyping();
                await sleep(Math.min(6000, Math.max(1500, text.length * 30)) + Math.random() * 800);
                await chat.clearState();
            } catch (_) { /* typing is cosmetic: send anyway */ }
        }
        const msg = await session.client.sendMessage(chatId, text);
        res.json({ ok: true, messageId: msg.id._serialized });
    } catch (e) {
        console.error(`Failed to send WhatsApp message from agent ${agentId}:`, e);
        res.status(500).json({ ok: false, error: e.message });
    }
});

app.get('/health', (req, res) => {
    res.json({ status: 'ok', activeSessions: Object.keys(sessions).length });
});

// Bring back every number that was linked before a restart (LocalAuth keeps `session-agent_<id>`).
// One at a time: two WhatsApp Web loads at once starve each other and both time out. Chromium's
// Singleton* files left by a killed container would make it refuse the profile, so they go first.
function restoreSessions() {
    try {
        let delay = 0;
        for (const dir of fs.readdirSync(AUTH_DIR)) {
            const m = /^session-agent_(\d+)$/.exec(dir);
            if (!m) continue;
            for (const lock of ['SingletonLock', 'SingletonSocket', 'SingletonCookie']) {
                fs.rmSync(path.join(AUTH_DIR, dir, lock), { force: true });
            }
            setTimeout(() => getClient(m[1]), delay);
            delay += 60000;
        }
    } catch (err) {
        console.log('No saved WhatsApp sessions to restore.');
    }
}

process.on('unhandledRejection', (err) => console.error('Unhandled rejection:', err));
process.on('uncaughtException', (err) => console.error('Uncaught exception:', err));

app.listen(PORT, '0.0.0.0', () => {
    console.log(`WhatsApp Bridge running on port ${PORT}`);
    restoreSessions();
});
