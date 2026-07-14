import useStore from '../store/useStore'

// ── 스캐너 패널 ───────────────────────────────────────
export function Scanner() {
  const { scannerSignals } = useStore()

  const iconMap = {
    VOLUME_SPIKE:     ['📊', '#e3b341'],
    OI_SURGE:         ['📈', '#58a6ff'],
    FUNDING_EXTREME:  ['⚠️', '#f85149'],
    DELTA_BURST:      ['⚡', '#bc8cff'],
    BULL_ABSORPTION:  ['🐂', '#3fb950'],
    BEAR_ABSORPTION:  ['🐻', '#f85149'],
    BULL_SWEEP:       ['🎯', '#3fb950'],
    BEAR_SWEEP:       ['🎯', '#f85149'],
  }

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      <div className="px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] text-[#8b949e] text-xs flex items-center justify-between">
        <span>🔍 스캐너</span>
        <span className="text-[#484f58]">{scannerSignals.length}건</span>
      </div>
      <div className="flex-1 overflow-y-auto">
        {scannerSignals.length === 0 ? (
          <div className="text-[#484f58] text-xs text-center mt-4">신호 대기 중...</div>
        ) : scannerSignals.map((sig, i) => {
          const [icon, color] = iconMap[sig.signal_type] || ['📡', '#8b949e']
          const ts = new Date(sig.ts * 1000).toLocaleTimeString('ko-KR')
          return (
            <div key={i} className="px-3 py-1.5 border-b border-[#21262d]/50 hover:bg-[#161b22]">
              <div className="flex items-center gap-1.5 text-xs">
                <span>{icon}</span>
                <span className="font-mono text-[10px]" style={{ color }}>{sig.symbol.replace('-USDT-SWAP','')}</span>
                <span className="text-[#484f58] ml-auto">{ts}</span>
              </div>
              <div className="text-[#8b949e] text-[10px] mt-0.5 truncate">{sig.message}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Time & Sales ──────────────────────────────────────
export function TimeSales() {
  const { trades, activeSymbol } = useStore()
  const filtered = trades.filter(t => t.symbol === activeSymbol).slice(0, 100)

  return (
    <div className="flex flex-col h-full bg-[#0d1117] font-mono text-xs">
      <div className="px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] text-[#8b949e] text-xs">
        ⏱ Time & Sales
      </div>
      <div className="grid grid-cols-4 px-2 py-1 text-[#484f58] text-[10px] border-b border-[#21262d]">
        <span>시간</span><span className="text-right">가격</span>
        <span className="text-right">수량</span><span className="text-right">방향</span>
      </div>
      <div className="flex-1 overflow-y-auto">
        {filtered.map((t, i) => {
          const isBuy  = t.side === 'buy'
          const color  = isBuy ? '#3fb950' : '#f85149'
          const ts     = new Date(t.ts * 1000).toLocaleTimeString('ko-KR')
          const isWhale = t.size >= 1.0
          return (
            <div
              key={i}
              className={`grid grid-cols-4 px-2 py-[1px] text-[10px] ${isWhale ? 'bg-[#e3b341]/5' : ''}`}
            >
              <span className="text-[#484f58]">{ts}</span>
              <span className="text-right" style={{ color }}>{Number(t.price).toFixed(1)}</span>
              <span className="text-right" style={{ color: isWhale ? '#e3b341' : color, fontWeight: isWhale ? 'bold' : 'normal' }}>
                {Number(t.size).toFixed(3)}
              </span>
              <span className="text-right" style={{ color }}>{isBuy ? '▲' : '▼'}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── 신호 로그 ─────────────────────────────────────────
export function SignalLog() {
  const { signals } = useStore()

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      <div className="px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] text-[#8b949e] text-xs flex items-center justify-between">
        <span>🚀 ICT 신호</span>
        <span className="text-[#484f58]">{signals.length}건</span>
      </div>
      <div className="flex-1 overflow-y-auto">
        {signals.length === 0 ? (
          <div className="text-[#484f58] text-xs text-center mt-4">신호 대기 중...</div>
        ) : signals.map((sig, i) => {
          const isLong = sig.direction === 'LONG'
          const color  = isLong ? '#3fb950' : '#f85149'
          const icon   = isLong ? '🟢' : '🔴'
          const ts     = new Date(sig.ts * 1000).toLocaleTimeString('ko-KR')
          return (
            <div key={i} className="px-3 py-2 border-b border-[#21262d]/50 hover:bg-[#161b22]">
              <div className="flex items-center gap-2 text-xs">
                <span>{icon}</span>
                <span className="font-semibold" style={{ color }}>
                  {sig.symbol.replace('-USDT-SWAP','')} {sig.direction}
                </span>
                <span className="text-[#484f58] ml-auto text-[10px]">{ts}</span>
              </div>
              <div className="mt-1 grid grid-cols-3 gap-1 text-[10px]">
                <span className="text-[#8b949e]">진입 <span className="text-[#c9d1d9]">{Number(sig.entry).toFixed(1)}</span></span>
                <span className="text-[#8b949e]">SL <span className="text-[#f85149]">{Number(sig.sl).toFixed(1)}</span></span>
                <span className="text-[#8b949e]">TP <span className="text-[#3fb950]">{Number(sig.tp).toFixed(1)}</span></span>
              </div>
              <div className="flex items-center gap-2 mt-1">
                <div className="flex-1 bg-[#21262d] rounded-full h-1">
                  <div
                    className="h-1 rounded-full"
                    style={{ width: `${sig.score}%`, background: sig.score >= 70 ? '#3fb950' : sig.score >= 50 ? '#e3b341' : '#f85149' }}
                  />
                </div>
                <span className="text-[10px] text-[#8b949e]">{sig.score?.toFixed(0)}점</span>
                <span className="text-[10px] text-[#58a6ff]">RR 1:{Number(sig.rr).toFixed(1)}</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
