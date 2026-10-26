// Local MQTT broker (Aedes): TCP on 1883 for Python / Node-RED, WebSocket on 9001 for the browser dashboard.
// Install once with `npm install` in this folder, then run `node broker.js [tcpPort] [wsPort]`.
const aedes = require('aedes')();
const net = require('net');
const http = require('http');
const { WebSocketServer, createWebSocketStream } = require('ws');

const TCP_PORT = +(process.argv[2] || 1883), WS_PORT = +(process.argv[3] || 9001);

net.createServer(aedes.handle).listen(TCP_PORT);

const httpServer = http.createServer();
const wss = new WebSocketServer({ server: httpServer });
wss.on('connection', (conn, req) => aedes.handle(createWebSocketStream(conn), req));
httpServer.listen(WS_PORT, () =>
  console.log(`MQTT broker running: tcp://localhost:${TCP_PORT}  ws://localhost:${WS_PORT}`));

aedes.on('client', c => console.log('client connected:', c.id));
aedes.on('clientDisconnect', c => console.log('client disconnected:', c.id));
