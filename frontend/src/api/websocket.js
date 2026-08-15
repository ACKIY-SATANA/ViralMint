const WS_PROTOCOL = window.location.protocol === "https:" ? "wss:" : "ws:"
const WS_URL = `${WS_PROTOCOL}//${window.location.host}/ws/chat`

class ViralMintWS {
  constructor() {
    this.ws = null
    this.listeners = {}
    this.reconnectDelay = 1000
    this.maxReconnectDelay = 30000
    this._shouldReconnect = true
    this._queue = [] // messages queued while disconnected
  }

  get connected() {
    return this.ws?.readyState === WebSocket.OPEN
  }

  connect() {
    // Guard BOTH open and still-CONNECTING sockets. connect() is called on
    // every Layout mount — React StrictMode double-invokes that effect, and
    // the second call used to land while the first socket was still
    // CONNECTING, creating a SECOND WebSocket without closing the first.
    // Both sockets then fed the same listener set, so every server event was
    // handled twice: duplicate chat bubbles, doubled streaming text, and
    // duplicate job cards.
    if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
      return
    }

    // Capture the socket in a local: every handler below compares it against
    // `this.ws` so a SUPERSEDED socket goes silent instead of racing the
    // current one.
    const socket = new WebSocket(WS_URL)
    this.ws = socket

    socket.onmessage = (e) => {
      // A superseded socket must go silent even if it lingers half-open.
      if (this.ws !== socket) return
      try {
        const msg = JSON.parse(e.data)
        const handlers = this.listeners[msg.type] || []
        handlers.forEach(h => h(msg))
      } catch (err) {
        console.error("WS parse error:", err)
      }
    }

    socket.onclose = () => {
      // Only the CURRENT socket drives connection state + reconnect; a stale
      // socket closing must not trigger a parallel reconnect loop.
      if (this.ws !== socket) return

      // Notify listeners of disconnection
      const handlers = this.listeners["_connection_state"] || []
      handlers.forEach(h => h({ connected: false }))

      if (this._shouldReconnect) {
        setTimeout(() => this.connect(), this.reconnectDelay)
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay)
      }
    }

    socket.onopen = () => {
      if (this.ws !== socket) {
        // Superseded while connecting — close it so it can't double-deliver.
        try { socket.close() } catch { /* already closing */ }
        return
      }
      this.reconnectDelay = 1000

      // Flush queued messages
      const pending = this._queue.splice(0)
      for (const msg of pending) {
        socket.send(JSON.stringify(msg))
      }

      // Notify listeners of connection
      const handlers = this.listeners["_connection_state"] || []
      handlers.forEach(h => h({ connected: true }))
    }

    socket.onerror = (err) => {
      if (this.ws !== socket) return
      console.error("WS error:", err)
    }
  }

  on(type, callback) {
    if (!this.listeners[type]) this.listeners[type] = []
    this.listeners[type].push(callback)
    return () => {
      this.listeners[type] = this.listeners[type].filter(h => h !== callback)
    }
  }

  send(message) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message))
    } else {
      // Queue important messages (chat, wizard steps) for retry on reconnect
      const queueable = ["chat_message", "set_session", "wizard_step_complete", "wizard_cancel"]
      if (queueable.includes(message.type)) {
        this._queue.push(message)
      }
    }
  }

  disconnect() {
    this._shouldReconnect = false
    this.ws?.close()
  }

}

export const ws = new ViralMintWS()
