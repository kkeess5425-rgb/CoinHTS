import { create } from 'zustand'

const useStore = create((set, get) => ({
  // 연결 상태
  connected: false,
  setConnected: (v) => set({ connected: v }),

  // 심볼
  symbols:       ['BTC-USDT-SWAP', 'ETH-USDT-SWAP'],
  activeSymbol:  'BTC-USDT-SWAP',
  setActiveSymbol: (sym) => set({ activeSymbol: sym }),

  // 타임프레임
  timeframe:    '15m',
  setTimeframe: (tf) => set({ timeframe: tf }),

  // 가격 / 시장 데이터
  prices:   {},
  oi:       {},
  funding:  {},
  updateMarket: (data) => set((s) => ({
    prices:  { ...s.prices,  [data.symbol]: data.price  },
    oi:      { ...s.oi,      [data.symbol]: data.oi     },
    funding: { ...s.funding, [data.symbol]: data.funding_rate },
  })),

  // Time & Sales
  trades: [],
  addTrade: (tick) => set((s) => ({
    trades: [tick, ...s.trades].slice(0, 300),
  })),

  // 오더북
  orderbook: { bids: [], asks: [] },
  setOrderbook: (ob) => set({ orderbook: ob }),

  // 신호 로그
  signals:        [],
  scannerSignals: [],
  addSignal:         (sig) => set((s) => ({ signals:        [sig, ...s.signals].slice(0, 100) })),
  addScannerSignal:  (sig) => set((s) => ({ scannerSignals: [sig, ...s.scannerSignals].slice(0, 200) })),

  // Footprint
  footprintBars: [],
  setFootprintBars: (bars) => set({ footprintBars: bars }),

  // 레이아웃 토글
  showOrderbook:   true,
  showScanner:     true,
  showTimeSales:   true,
  showFootprint:   false,
  toggleOrderbook:  () => set((s) => ({ showOrderbook:  !s.showOrderbook  })),
  toggleScanner:    () => set((s) => ({ showScanner:    !s.showScanner    })),
  toggleTimeSales:  () => set((s) => ({ showTimeSales:  !s.showTimeSales  })),
  toggleFootprint:  () => set((s) => ({ showFootprint:  !s.showFootprint  })),
}))

export default useStore
