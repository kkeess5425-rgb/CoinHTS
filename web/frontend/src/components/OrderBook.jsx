import useStore from '../store/useStore'

export default function OrderBook() {
  const { orderbook, prices, activeSymbol } = useStore()
  const { bids = [], asks = [] } = orderbook
  const price = prices[activeSymbol]

  const maxSize = Math.max(
    ...bids.slice(0,10).map(b => b[1]),
    ...asks.slice(0,10).map(a => a[1]),
    0.001
  )

  return (
    <div className="flex flex-col h-full bg-[#0d1117] text-xs font-mono">
      <div className="px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] text-[#8b949e] text-xs">
        📊 호가창
      </div>

      {/* 헤더 */}
      <div className="grid grid-cols-3 px-2 py-1 text-[#484f58] border-b border-[#21262d]">
        <span>가격</span>
        <span className="text-right">수량</span>
        <span className="text-right">합계</span>
      </div>

      {/* Asks (매도, 위에서 아래로) */}
      <div className="flex-1 overflow-hidden">
        {[...asks].reverse().slice(0, 12).map(([p, s], i) => {
          const pct = (s / maxSize) * 100
          return (
            <div key={i} className="relative grid grid-cols-3 px-2 py-[1px] hover:bg-[#161b22]">
              <div
                className="absolute right-0 top-0 h-full bg-[#f85149]/10"
                style={{ width: `${pct}%` }}
              />
              <span className="text-[#f85149] relative z-10">{Number(p).toFixed(1)}</span>
              <span className="text-right text-[#c9d1d9] relative z-10">{Number(s).toFixed(4)}</span>
              <span className="text-right text-[#484f58] relative z-10">{(p*s).toFixed(0)}</span>
            </div>
          )
        })}
      </div>

      {/* 현재가 */}
      <div className="px-2 py-1.5 border-y border-[#30363d] bg-[#161b22]">
        <span className="text-[#c9d1d9] font-bold text-sm">
          {price?.toFixed(2) ?? '--'}
        </span>
      </div>

      {/* Bids (매수, 위에서 아래로) */}
      <div className="flex-1 overflow-hidden">
        {bids.slice(0, 12).map(([p, s], i) => {
          const pct = (s / maxSize) * 100
          return (
            <div key={i} className="relative grid grid-cols-3 px-2 py-[1px] hover:bg-[#161b22]">
              <div
                className="absolute right-0 top-0 h-full bg-[#3fb950]/10"
                style={{ width: `${pct}%` }}
              />
              <span className="text-[#3fb950] relative z-10">{Number(p).toFixed(1)}</span>
              <span className="text-right text-[#c9d1d9] relative z-10">{Number(s).toFixed(4)}</span>
              <span className="text-right text-[#484f58] relative z-10">{(p*s).toFixed(0)}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
