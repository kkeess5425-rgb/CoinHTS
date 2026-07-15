import { useState, useEffect } from 'react'
import useStore from '../store/useStore'

export default function PositionsPanel() {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(false)
  const { prices } = useStore()

  const load = async () => {
    try {
      const r = await fetch('/api/positions')
      setData(await r.json())
    } catch {}
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 2000)
    return () => clearInterval(t)
  }, [])

  const closePosition = async (posId) => {
    setLoading(true)
    try {
      await fetch(`/api/positions/${posId}/close`, { method: 'POST' })
      await load()
    } finally { setLoading(false) }
  }

  const partialClose = async (posId) => {
    setLoading(true)
    try {
      await fetch(`/api/positions/${posId}/partial?pct=0.5`, { method: 'POST' })
      await load()
    } finally { setLoading(false) }
  }

  const setBE = async (posId) => {
    try {
      await fetch(`/api/positions/${posId}/breakeven`, { method: 'POST' })
      await load()
    } catch {}
  }

  const positions = data?.positions || []
  const balance   = data?.balance   || 0
  const dailyLoss = data?.daily_loss_pct || 0

  return (
    <div className="flex flex-col h-full bg-[#0d1117] text-xs">
      {/* 헤더 */}
      <div className="flex items-center gap-3 px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] shrink-0">
        <span className="text-[#8b949e]">💼 포지션 관리</span>
        <span className="text-[#c9d1d9] font-mono font-semibold">
          ${balance.toLocaleString('en', { minimumFractionDigits: 2 })}
        </span>
        <div className={`ml-auto text-[10px] ${dailyLoss > 2 ? 'text-[#f85149]' : dailyLoss > 1 ? 'text-[#e3b341]' : 'text-[#484f58]'}`}>
          일일 손실: {dailyLoss.toFixed(2)}%
        </div>
      </div>

      {/* 포지션 목록 */}
      <div className="flex-1 overflow-y-auto">
        {positions.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-2">
            <div className="text-[#484f58]">열린 포지션 없음</div>
            <div className="text-[10px] text-[#30363d]">신호 발생 시 자동으로 진입됩니다</div>
          </div>
        ) : positions.map((pos, i) => {
          const curPrice = prices[pos.symbol] || pos.entry
          const risk     = Math.abs(pos.entry - pos.sl)
          const r        = risk > 0
            ? (pos.direction === 'LONG' ? (curPrice - pos.entry) / risk : (pos.entry - curPrice) / risk)
            : 0
          const pnlUsd = pos.direction === 'LONG'
            ? (curPrice - pos.entry) * pos.size
            : (pos.entry - curPrice) * pos.size
          const isLong   = pos.direction === 'LONG'
          const color    = pnlUsd >= 0 ? '#3fb950' : '#f85149'

          return (
            <div key={pos.id || i} className="px-3 py-2 border-b border-[#21262d]/50 hover:bg-[#161b22]">
              {/* 심볼 + 방향 + PnL */}
              <div className="flex items-center gap-2 mb-1">
                <span className={`font-semibold ${isLong ? 'text-[#3fb950]' : 'text-[#f85149]'}`}>
                  {isLong ? '▲' : '▼'} {pos.symbol?.replace('-USDT-SWAP','')}
                </span>
                <span className="text-[#8b949e]">{pos.direction}</span>
                <span className="ml-auto font-mono" style={{ color }}>
                  {r >= 0 ? '+' : ''}{r.toFixed(2)}R
                </span>
                <span className="font-mono text-[10px]" style={{ color }}>
                  {pnlUsd >= 0 ? '+' : ''}${pnlUsd.toFixed(1)}
                </span>
              </div>

              {/* 가격 정보 */}
              <div className="flex gap-3 text-[10px] text-[#8b949e] mb-1.5">
                <span>진입 <span className="text-[#c9d1d9]">{pos.entry?.toFixed(1)}</span></span>
                <span>현재 <span className="text-[#c9d1d9]">{curPrice?.toFixed(1)}</span></span>
                <span>SL <span className="text-[#f85149]">{pos.sl?.toFixed(1)}</span></span>
                <span>TP <span className="text-[#3fb950]">{pos.tp?.toFixed(1)}</span></span>
              </div>

              {/* R 프로그레스바 */}
              <div className="flex items-center gap-2 mb-1.5">
                <div className="flex-1 h-1 bg-[#21262d] rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${Math.min(100, Math.abs(r) / 2 * 100)}%`,
                      background: color,
                    }}
                  />
                </div>
                <span className="text-[10px] text-[#484f58]">{pos.size?.toFixed(4)}</span>
              </div>

              {/* 액션 버튼 */}
              <div className="flex gap-1.5">
                <button
                  onClick={() => partialClose(pos.id)}
                  disabled={loading}
                  className="flex-1 py-0.5 text-[10px] rounded border border-[#3fb950]/50 text-[#3fb950] hover:bg-[#3fb950]/10 transition-colors"
                >
                  50% 익절
                </button>
                <button
                  onClick={() => setBE(pos.id)}
                  disabled={loading}
                  className="flex-1 py-0.5 text-[10px] rounded border border-[#e3b341]/50 text-[#e3b341] hover:bg-[#e3b341]/10 transition-colors"
                >
                  BE
                </button>
                <button
                  onClick={() => closePosition(pos.id)}
                  disabled={loading}
                  className="flex-1 py-0.5 text-[10px] rounded border border-[#f85149]/50 text-[#f85149] hover:bg-[#f85149]/10 transition-colors"
                >
                  청산
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
