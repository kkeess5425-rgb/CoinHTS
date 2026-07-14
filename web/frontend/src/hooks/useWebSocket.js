import { useEffect, useRef, useCallback } from 'react'
import useStore from '../store/useStore'

const WS_URL = import.meta.env.VITE_WS_URL || `ws://${location.host}/ws`

export function useWebSocket() {
  const ws      = useRef(null)
  const reconnectTimer = useRef(null)
  const {
    setConnected, addTrade, setOrderbook,
    addSignal, addScannerSignal, updateMarket,
  } = useStore()

  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) return

    ws.current = new WebSocket(WS_URL)

    ws.current.onopen = () => {
      setConnected(true)
      console.log('[WS] 연결됨')
      // 15초마다 ping
      const ping = setInterval(() => {
        if (ws.current?.readyState === WebSocket.OPEN) {
          ws.current.send(JSON.stringify({ type: 'ping' }))
        }
      }, 15000)
      ws.current._ping = ping
    }

    ws.current.onclose = () => {
      setConnected(false)
      clearInterval(ws.current?._ping)
      console.log('[WS] 연결 끊김, 3초 후 재연결...')
      reconnectTimer.current = setTimeout(connect, 3000)
    }

    ws.current.onerror = (e) => console.warn('[WS] 오류:', e)

    ws.current.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        handleMessage(msg)
      } catch (err) {
        console.warn('[WS] 파싱 오류:', err)
      }
    }
  }, [])

  function handleMessage(msg) {
    switch (msg.type) {
      case 'tick':
        addTrade(msg.data)
        break
      case 'orderbook':
        setOrderbook({ bids: msg.data.bids, asks: msg.data.asks, symbol: msg.data.symbol })
        break
      case 'strategy_signal':
        addSignal(msg.data)
        break
      case 'scanner_signal':
        addScannerSignal(msg.data)
        break
      case 'market_data':
        updateMarket(msg.data)
        break
      case 'init':
        console.log('[WS] 초기화:', msg.data.symbols)
        break
      case 'pong':
        break
      default:
        break
    }
  }

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      ws.current?.close()
    }
  }, [connect])

  return ws
}
