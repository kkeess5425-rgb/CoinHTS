import { useState } from 'react'
import useStore from '../store/useStore'

const GRADE = (wr, avgR) => {
  if (wr >= 55 && avgR > 0.2) return { label: '✅ 양호', color: '#3fb950' }
  if (avgR > 0)               return { label: '⚠️ 보통', color: '#e3b341' }
  return                             { label: '❌ 개선필요', color: '#f85149' }
}

export default function Backtest({ onClose }) {
  const { activeSymbol } = useStore()
  const [loading, setLoading]   = useState(false)
  const [result,  setResult]    = useState(null)
  const [error,   setError]     = useState(null)
  const [params,  setParams]    = useState({
    timeframe: '15m', limit: 500,
  })

  async function runBacktest() {
    setLoading(true); setError(null); setResult(null)
    try {
      const res  = await fetch(
        `/api/backtest?symbol=${encodeURIComponent(activeSymbol)}&timeframe=${params.timeframe}&limit=${params.limit}`
      )
      const data = await res.json()
      if (data.error) { setError(data.error); return }
      setResult(data)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const s = result?.summary
  const grade = s ? GRADE(s.win_rate, s.avg_r) : null

  // Equity curve
  const trades = result?.trades || []
  const equityCurve = trades
    .filter(t => t.result !== 'OPEN')
    .reduce((acc, t) => {
      const prev = acc.length ? acc[acc.length-1] : 0
      return [...acc, prev + (t.r || 0)]
    }, [])

  const maxR = Math.max(...equityCurve, 0.5)
  const minR = Math.min(...equityCurve, -0.5)

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-[#0d1117] border border-[#30363d] rounded-lg w-[700px] max-h-[85vh] overflow-y-auto shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-[#21262d]">
          <h2 className="text-[#c9d1d9] font-semibold">📊 ICT 백테스트 — {activeSymbol}</h2>
          <button onClick={onClose} className="text-[#484f58] hover:text-[#8b949e] text-lg">✕</button>
        </div>

        {/* 설정 */}
        <div className="flex gap-3 px-5 py-3 border-b border-[#21262d]">
          <div className="flex flex-col gap-1">
            <label className="text-[#8b949e] text-xs">타임프레임</label>
            <select
              value={params.timeframe}
              onChange={e => setParams(p => ({...p, timeframe: e.target.value}))}
              className="bg-[#21262d] text-[#c9d1d9] border border-[#30363d] rounded px-2 py-1 text-sm"
            >
              {['1m','5m','15m','1H','4H'].map(tf => <option key={tf}>{tf}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[#8b949e] text-xs">봉 수</label>
            <select
              value={params.limit}
              onChange={e => setParams(p => ({...p, limit: Number(e.target.value)}))}
              className="bg-[#21262d] text-[#c9d1d9] border border-[#30363d] rounded px-2 py-1 text-sm"
            >
              {[300,500,1000].map(l => <option key={l} value={l}>{l}봉</option>)}
            </select>
          </div>
          <div className="flex items-end">
            <button
              onClick={runBacktest}
              disabled={loading}
              className="px-4 py-1.5 bg-[#238636] hover:bg-[#2ea043] text-white rounded text-sm transition-colors disabled:opacity-50"
            >
              {loading ? '⏳ 실행 중...' : '▶ 백테스트 실행'}
            </button>
          </div>
        </div>

        {/* 에러 */}
        {error && (
          <div className="mx-5 my-3 px-3 py-2 bg-[#f85149]/10 border border-[#f85149]/30 rounded text-[#f85149] text-sm">
            ⚠️ {error}
          </div>
        )}

        {/* 결과 */}
        {s && (
          <div className="px-5 py-4 space-y-4">
            {/* 요약 */}
            <div className="grid grid-cols-3 gap-3">
              {[
                ['총 트레이드', `${s.total}건`],
                ['승률', `${s.win_rate?.toFixed(1)}%`],
                ['누적 R', `${s.cum_r > 0 ? '+' : ''}${s.cum_r?.toFixed(2)}R`],
                [`${s.wins}승 / ${s.losses}패`, ''],
                ['평균 R', `${s.avg_r > 0 ? '+' : ''}${s.avg_r?.toFixed(3)}R`],
                [grade.label, ''],
              ].map(([label, val], i) => (
                <div key={i} className="bg-[#161b22] rounded px-3 py-2">
                  <div className="text-[#8b949e] text-xs">{val ? label : ''}</div>
                  <div className="text-sm font-semibold mt-0.5"
                    style={{ color: i === 5 ? grade.color : i === 2 ? (s.cum_r >= 0 ? '#3fb950' : '#f85149') : '#c9d1d9' }}>
                    {val || label}
                  </div>
                </div>
              ))}
            </div>

            {/* Equity Curve */}
            {equityCurve.length > 1 && (
              <div>
                <div className="text-[#8b949e] text-xs mb-2">📈 Equity Curve (R 단위)</div>
                <div className="bg-[#161b22] rounded p-3 h-28 relative">
                  <svg viewBox={`0 0 ${equityCurve.length - 1} ${maxR - minR}`}
                    className="w-full h-full" preserveAspectRatio="none">
                    {/* 0선 */}
                    <line x1="0" y1={maxR} x2={equityCurve.length-1} y2={maxR}
                      stroke="#30363d" strokeWidth="0.05" strokeDasharray="0.2,0.2" />
                    {/* Equity 라인 */}
                    <polyline
                      points={equityCurve.map((v, i) => `${i},${maxR - v}`).join(' ')}
                      fill="none"
                      stroke={s.cum_r >= 0 ? '#3fb950' : '#f85149'}
                      strokeWidth="0.08"
                    />
                    {/* 면적 */}
                    <polygon
                      points={`0,${maxR} ${equityCurve.map((v,i) => `${i},${maxR-v}`).join(' ')} ${equityCurve.length-1},${maxR}`}
                      fill={s.cum_r >= 0 ? '#3fb95020' : '#f8514920'}
                    />
                  </svg>
                  <div className="absolute top-1 right-2 text-xs" style={{ color: s.cum_r >= 0 ? '#3fb950' : '#f85149' }}>
                    {s.cum_r > 0 ? '+' : ''}{s.cum_r?.toFixed(2)}R
                  </div>
                </div>
              </div>
            )}

            {/* 트레이드 내역 */}
            <div>
              <div className="text-[#8b949e] text-xs mb-2">최근 트레이드</div>
              <div className="space-y-1 max-h-52 overflow-y-auto">
                {trades.slice(-20).reverse().map((t, i) => {
                  const isWin  = t.result === 'WIN'
                  const isOpen = t.result === 'OPEN'
                  const color  = isOpen ? '#58a6ff' : isWin ? '#3fb950' : '#f85149'
                  const ts     = new Date((t.ts || t.exit_ts || 0) * 1000).toLocaleDateString('ko-KR')
                  return (
                    <div key={i} className="flex items-center gap-2 text-xs bg-[#161b22] rounded px-3 py-1.5">
                      <span className={t.dir === 'LONG' ? 'text-[#3fb950]' : 'text-[#f85149]'}>
                        {t.dir === 'LONG' ? '▲' : '▼'} {t.dir}
                      </span>
                      <span className="text-[#8b949e]">{Number(t.entry).toFixed(1)}</span>
                      <span className="text-[#484f58]">→</span>
                      <span className="text-[#8b949e]">TP {Number(t.tp).toFixed(1)}</span>
                      <span className="ml-auto font-semibold" style={{ color }}>
                        {isOpen ? '⏳OPEN' : (t.r > 0 ? '+' : '') + t.r?.toFixed(1) + 'R'}
                      </span>
                      <span className="text-[#484f58]">{ts}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
