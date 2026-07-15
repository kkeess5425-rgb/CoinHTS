import { useState } from 'react'
import useStore from '../store/useStore'

const TF_OPTIONS = ['1m', '5m', '15m', '1H', '4H']
const GRADE = (wr, cumR) => {
  if (wr >= 55 && cumR > 2) return { label: 'A+', color: '#3fb950' }
  if (wr >= 50 && cumR > 0) return { label: 'A',  color: '#58a6ff' }
  if (cumR > 0)             return { label: 'B',  color: '#e3b341' }
  return                           { label: 'C',  color: '#f85149' }
}

export default function GridSearch() {
  const { activeSymbol }  = useStore()
  const [loading, setLoading] = useState(false)
  const [result,  setResult]  = useState(null)
  const [error,   setError]   = useState(null)
  const [params,  setParams]  = useState({ timeframe: '15m', limit: 300 })

  async function run() {
    setLoading(true); setError(null); setResult(null)
    try {
      const url = `/api/backtest/grid-search?symbol=${encodeURIComponent(activeSymbol)}&timeframe=${params.timeframe}&limit=${params.limit}`
      const res  = await fetch(url, { method: 'POST' })
      const data = await res.json()
      if (data.error) { setError(data.error); return }
      setResult(data)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-full bg-[#0d1117]">
      {/* 헤더 */}
      <div className="px-3 py-1.5 bg-[#161b22] border-b border-[#21262d] text-xs flex items-center gap-2 shrink-0">
        <span className="text-[#8b949e]">⚡ 파라미터 최적화</span>
        <span className="text-[#484f58] text-[10px]">{activeSymbol.replace('-USDT-SWAP','')}</span>
      </div>

      {/* 설정 */}
      <div className="flex gap-2 px-3 py-2 border-b border-[#21262d] shrink-0">
        <select
          value={params.timeframe}
          onChange={e => setParams(p => ({...p, timeframe: e.target.value}))}
          className="bg-[#21262d] text-[#c9d1d9] border border-[#30363d] rounded px-2 py-1 text-xs"
        >
          {TF_OPTIONS.map(tf => <option key={tf}>{tf}</option>)}
        </select>
        <select
          value={params.limit}
          onChange={e => setParams(p => ({...p, limit: Number(e.target.value)}))}
          className="bg-[#21262d] text-[#c9d1d9] border border-[#30363d] rounded px-2 py-1 text-xs"
        >
          {[300, 500, 1000].map(n => <option key={n} value={n}>{n}봉</option>)}
        </select>
        <button
          onClick={run}
          disabled={loading}
          className="ml-auto px-3 py-1 bg-[#1f6feb] hover:bg-[#388bfd] text-white rounded text-xs transition-colors disabled:opacity-50"
        >
          {loading ? '⏳ 실행 중...' : '▶ 그리드서치'}
        </button>
      </div>

      {/* 에러 */}
      {error && (
        <div className="mx-3 my-2 px-2 py-1.5 bg-[#f85149]/10 border border-[#f85149]/30 rounded text-[#f85149] text-xs">
          ⚠️ {error}
        </div>
      )}

      {/* 결과 */}
      <div className="flex-1 overflow-y-auto">
        {result && (
          <>
            {/* 요약 */}
            <div className="px-3 py-2 border-b border-[#21262d] bg-[#161b22]/50">
              <div className="text-xs text-[#8b949e]">
                총 <span className="text-[#c9d1d9]">{result.total_combos}</span>개 조합 테스트
              </div>
              {result.best && (
                <div className="text-xs text-[#3fb950] mt-0.5">
                  최고: 승률 {result.best.win_rate}% | 누적R {result.best.cum_r > 0 ? '+' : ''}{result.best.cum_r}R
                </div>
              )}
            </div>

            {/* TOP 5 결과 */}
            <div className="p-2 space-y-1.5">
              {(result.top5 || []).map((r, i) => {
                const { label, color } = GRADE(r.win_rate, r.cum_r)
                return (
                  <div key={i} className={`bg-[#161b22] rounded p-2 border ${i === 0 ? 'border-[#3fb950]/40' : 'border-[#21262d]'}`}>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                            style={{ background: color + '20', color }}>
                        #{i+1} {label}
                      </span>
                      <span className="text-[#8b949e] text-[10px] ml-auto">
                        {r.total}건 ({r.wins}승/{r.losses}패)
                      </span>
                    </div>
                    <div className="grid grid-cols-3 gap-1 text-[10px]">
                      <div className="text-center">
                        <div className="text-[#484f58]">승률</div>
                        <div className="font-mono" style={{ color: r.win_rate >= 50 ? '#3fb950' : '#f85149' }}>
                          {r.win_rate?.toFixed(1)}%
                        </div>
                      </div>
                      <div className="text-center">
                        <div className="text-[#484f58]">누적R</div>
                        <div className="font-mono" style={{ color: r.cum_r >= 0 ? '#3fb950' : '#f85149' }}>
                          {r.cum_r > 0 ? '+' : ''}{r.cum_r?.toFixed(2)}R
                        </div>
                      </div>
                      <div className="text-center">
                        <div className="text-[#484f58]">평균R</div>
                        <div className="font-mono" style={{ color: r.avg_r >= 0 ? '#3fb950' : '#f85149' }}>
                          {r.avg_r > 0 ? '+' : ''}{r.avg_r?.toFixed(3)}R
                        </div>
                      </div>
                    </div>
                    {/* 파라미터 */}
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {Object.entries(r.params || {}).map(([k, v]) => (
                        <span key={k} className="px-1 py-0.5 bg-[#21262d] rounded text-[9px] text-[#8b949e]">
                          {k}: <span className="text-[#c9d1d9]">{String(v)}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          </>
        )}

        {!result && !loading && !error && (
          <div className="flex flex-col items-center justify-center h-full gap-2 text-[#484f58] text-xs">
            <span>▶ 그리드서치를 실행하면</span>
            <span>최적 파라미터를 자동으로 찾습니다</span>
          </div>
        )}
      </div>
    </div>
  )
}
