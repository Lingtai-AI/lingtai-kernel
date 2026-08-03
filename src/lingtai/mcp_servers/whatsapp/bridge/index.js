#!/usr/bin/env node
/**
 * lingtai-whatsapp-bridge
 *
 * Local bridge between the LingTai WhatsApp MCP (Python) and a personal
 * WhatsApp account via whatsapp-web.js. Speaks newline-delimited JSON over
 * stdin/stdout:
 *
 *   Outbound events (bridge -> parent):
 *     {"type":"qr",        "data":{"qr_base64":"...","ascii":"..."}}
 *     {"type":"ready",    "data":{"me":"15551234567@c.us","pushname":"..."}}
 *     {"type":"message",  "data":{...normalized message...}}
 *     {"type":"disconnected","data":{"reason":"..."}}
 *     {"type":"error",    "data":{"error":"..."}}
 *
 *   Inbound requests (parent -> bridge):
 *     {"id":1,"method":"send","params":{"to":"15551234567","text":"hi"}}
 *     {"id":2,"method":"reply","params":{"message_id":"...","text":"hi"}}
 *     {"id":3,"method":"react","params":{"message_id":"...","emoji":"👍"}}
 *     {"id":4,"method":"read","params":{"limit":20}}
 *     {"id":5,"method":"search","params":{"query":"...","limit":20}}
 *     {"id":6,"method":"contacts","params":{}}
 *     {"id":7,"method":"status","params":{}}
 *     {"id":8,"method":"logout","params":{}}
 *
 *   Response: {"id":1,"result":{...}} or {"id":1,"error":"..."}
 */
'use strict';

const { Client, LocalAuth } = require('whatsapp-web.js');
const QRCode = require('qrcode');
const readline = require('readline');

const SESSION_DIR = process.env.LINGTAI_WHATSAPP_SESSION_DIR || '.wwebjs_auth';

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

function normalizeMessage(msg) {
  const out = {
    id: msg.id ? msg.id._serialized || String(msg.id.id) : null,
    from: msg.from ? msg.from._serialized || String(msg.from) : null,
    to: msg.to ? msg.to._serialized || String(msg.to) : null,
    author: msg.author ? msg.author._serialized || String(msg.author) : null,
    body: typeof msg.body === 'string' ? msg.body : '',
    type: msg.type || 'text',
    timestamp: msg.timestamp || null,
    fromMe: !!msg.fromMe,
    hasMedia: !!msg.hasMedia,
  };
  if (msg.hasQuotedMsg) {
    out.quoted = {
      id: msg.quotedMsgId || null,
      body: msg.quotedMsgBody || null,
      author: msg.quotedParticipant ? msg.quotedParticipant._serialized || String(msg.quotedParticipant) : null,
    };
  }
  return out;
}

async function toWaId(target) {
  if (!target) return null;
  const s = String(target).trim();
  if (s.includes('@')) return s;
  // Plain digits: add the @c.us suffix (group ids keep their suffix when passed through).
  const digits = s.replace(/[^0-9]/g, '');
  if (digits && digits.length >= 8) return `${digits}@c.us`;
  return s;
}

const rl = readline.createInterface({ input: process.stdin });

let client = null;
let qrTimeout = null;
let readyResolvers = [];
let lastQr = null;

function clientReady() {
  return new Promise((resolve, reject) => {
    if (client && client.info) return resolve(client);
    readyResolvers.push({ resolve, reject });
    setTimeout(() => reject(new Error('bridge: not ready (no authenticated session)')), 60000);
  });
}

async function main() {
  client = new Client({
    authStrategy: new LocalAuth({ dataPath: SESSION_DIR }),
    puppeteer: {
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    },
  });

  client.on('qr', async (qr) => {
    lastQr = qr;
    try {
      const qr_base64 = await QRCode.toDataURL(qr);
      emit({ type: 'qr', data: { qr_base64, ascii: null } });
    } catch (e) {
      emit({ type: 'qr', data: { qr_base64: null, ascii: String(qr) } });
    }
  });

  client.on('ready', () => {
    const me = client.info ? client.info.wid._serialized : null;
    const pushname = client.info ? client.info.pushname : null;
    emit({ type: 'ready', data: { me, pushname } });
    for (const r of readyResolvers) r.resolve(client);
    readyResolvers = [];
  });

  client.on('authenticated', () => {
    emit({ type: 'authenticated' });
  });

  client.on('auth_failure', (msg) => {
    emit({ type: 'disconnected', data: { reason: 'auth_failure', detail: String(msg) } });
  });

  client.on('disconnected', (reason) => {
    emit({ type: 'disconnected', data: { reason: String(reason) } });
  });

  client.on('message', (msg) => {
    emit({ type: 'message', data: normalizeMessage(msg) });
  });

  client.on('message_reaction', (reaction) => {
    emit({ type: 'reaction', data: {
      id: reaction.id ? reaction.id._serialized : null,
      from: reaction.from ? reaction.from._serialized : null,
      messageId: reaction.msgId ? reaction.msgId._serialized : null,
      reaction: reaction.reaction || null,
    }});
  });

  client.initialize().catch((e) => {
    emit({ type: 'error', data: { error: String(e && e.stack || e) } });
  });
}

async function dispatch(id, method, params) {
  try {
    switch (method) {
      case 'status': {
        const ready = !!(client && client.info);
        emit({ id, result: {
          ready,
          me: ready ? client.info.wid._serialized : null,
          pushname: ready ? client.info.pushname : null,
          qr_available: !!lastQr,
        }});
        return;
      }
      case 'get_qr': {
        if (!lastQr) {
          emit({ id, error: 'bridge: no QR available yet; wait for the qr event' });
          return;
        }
        const qr_base64 = await QRCode.toDataURL(lastQr);
        emit({ id, result: { qr_base64 } });
        return;
      }
      case 'send': {
        const c = await clientReady();
        const waId = await toWaId(params && (params.to || params.wa_id));
        if (!waId) throw new Error('send requires to');
        let result;
        if (params && params.text) {
          result = await c.sendMessage(waId, params.text);
        } else if (params && params.media) {
          const media = params.media;
          const opts = media.caption ? { caption: media.caption } : undefined;
          result = await c.sendMessage(waId, { url: media.url, filename: media.filename }, opts);
        } else {
          throw new Error('send requires text or media');
        }
        emit({ id, result: { id: result.id ? result.id._serialized : null, wa_id: waId } });
        return;
      }
      case 'reply': {
        const c = await clientReady();
        if (!params || !params.message_id || !params.text) throw new Error('reply requires message_id and text');
        const waId = await toWaId(params.to);
        if (!waId) throw new Error('reply requires to');
        const result = await c.sendMessage(waId, params.text, { quotedMessageId: params.message_id });
        emit({ id, result: { id: result.id ? result.id._serialized : null, wa_id: waId } });
        return;
      }
      case 'react': {
        const c = await clientReady();
        if (!params || !params.message_id || !params.emoji) throw new Error('react requires message_id and emoji');
        const msg = await c.fetchMessageById(params.message_id);
        if (!msg) throw new Error('react: message not found');
        await msg.react(params.emoji);
        emit({ id, result: { ok: true } });
        return;
      }
      case 'read': {
        const c = await clientReady();
        const limit = Math.min(parseInt((params && params.limit) || 20, 10) || 20, 100);
        const chats = await c.getChats();
        const out = [];
        for (const chat of chats.slice(0, Math.max(limit, 10))) {
          try {
            const messages = await chat.fetchMessages({ limit: 5 });
            out.push({
              id: chat.id._serialized,
              name: chat.name || null,
              unreadCount: chat.unreadCount || 0,
              lastMessage: messages.length ? normalizeMessage(messages[messages.length - 1]) : null,
            });
          } catch (e) { /* skip chat */ }
        }
        emit({ id, result: { chats: out } });
        return;
      }
      case 'search': {
        const c = await clientReady();
        const q = (params && params.query) || '';
        const limit = Math.min(parseInt((params && params.limit) || 20, 10) || 20, 100);
        const chats = await c.getChats();
        const out = [];
        for (const chat of chats) {
          if (out.length >= limit) break;
          try {
            const messages = await chat.fetchMessages({ limit: 100 });
            for (const m of messages) {
              if (out.length >= limit) break;
              if (typeof m.body === 'string' && m.body.toLowerCase().includes(q.toLowerCase())) {
                out.push(normalizeMessage(m));
              }
            }
          } catch (e) { /* skip */ }
        }
        emit({ id, result: { messages: out } });
        return;
      }
      case 'contacts': {
        const c = await clientReady();
        const contacts = await c.getContacts();
        const out = contacts.slice(0, 500).map((ct) => ({
          id: ct.id ? ct.id._serialized : null,
          name: ct.name || ct.pushname || ct.number || null,
          number: ct.number || null,
        }));
        emit({ id, result: { contacts: out } });
        return;
      }
      case 'logout': {
        if (client) {
          try { await client.logout(); } catch (e) { /* ignore */ }
        }
        emit({ id, result: { ok: true } });
        return;
      }
      case 'ping': {
        emit({ id, result: { pong: true } });
        return;
      }
      default:
        emit({ id, error: `bridge: unknown method ${method}` });
    }
  } catch (e) {
    emit({ id, error: String(e && e.message || e) });
  }
}

rl.on('line', (line) => {
  line = line.trim();
  if (!line) return;
  let req;
  try {
    req = JSON.parse(line);
  } catch (e) {
    emit({ id: null, error: 'bridge: invalid JSON' });
    return;
  }
  if (req && typeof req.id !== 'undefined' && req.method) {
    dispatch(req.id, req.method, req.params || {});
  } else {
    emit({ id: null, error: 'bridge: malformed request' });
  }
});

process.on('SIGTERM', () => {
  process.exit(0);
});

main().catch((e) => {
  emit({ type: 'error', data: { error: String(e && e.stack || e) } });
  process.exit(1);
});
